"""Output ring: masking by rewriting the AST before execution.

Post-processing the result by column name breaks on aliases, on renaming views and on
two tables sharing a column name. More importantly, post-processing lets the real
value travel from the engine to the process, where it is already exposed to anyone
reading engine logs. Rewriting the tree keeps the real value inside the database.

Runs after `validate()` and before execution, and relaxes no guard rejection: R008
still rejects any `deny` column anywhere, and any `mask` column outside a direct
projection (`where`, `join_on`, `group_by`, `order_by`, `having`, `qualify`, function
argument, window partition). Exactly one case moves from rejection to rewrite: a
`mask` column in a direct projection. In the output a substitute is shown; in a
predicate the original can be guessed.

The guard keeps this small. Any projection that survives with a masked column is a
bare `exp.Column`, aliased or not: `concat(...)`, `upper(substring(...))` and
`CASE WHEN ... THEN first_name` are function arguments rejected by R008, and `a || b`
is rejected by R002 because `DPipe` is not allowlisted. Rewriting is therefore
"replace the projection expression, keep the alias"; any other shape is a guard
defect and is rejected, not improvised.

`hash_estable` is computed in the engine through the `warden_hash` macro, so the
pepper stays out of the executed tree and the audit record; see `mask/macro.py` for
the remaining exposure in engine logs. It affects two columns:
`dim_device.device_fingerprint` and `fact_payment_attempt.ip_address_int`.
"""

from __future__ import annotations

import dataclasses
from typing import Final

from sqlglot import expressions as exp

from datawarden.domain.types import (
    Position,
    RejectionReason,
    Severity,
    ValidatedQuery,
)
from datawarden.guard.allowlist import ALLOWED_NODES
from datawarden.guard.context import lineage_index
from datawarden.guard.query_lineage import UNKNOWN, resolve
from datawarden.mask.config import MaskConfig
from datawarden.mask.macro import MACRO_NAME
from datawarden.principal.policy import AccessPolicy, Level

#: Nodes a masked tree may contain: the guard's allowlist plus the hash call.
#:
#: Hashing is deliberately not added to `ALLOWED_NODES`: users must not write
#: `sha256()` themselves (R003 blocks engine functions), but the masker runs after
#: validation and must emit it. A separate set keeps the guard's surface bounded and
#: makes it checkable that masking does not widen it.
MASK_NODES: Final[frozenset[str]] = frozenset(ALLOWED_NODES) | {"Anonymous"}


def uses_only_mask_nodes(tree: exp.Expression) -> bool:
    """Whether the masked tree uses only what masking is allowed to add.

    A flat set is not enough. Masking calls `warden_hash`, which sqlglot represents
    as `exp.Anonymous`, but `exp.Anonymous` is a rejection under the project
    invariants; allowing it unconditionally would mean "any function", which the
    guard allowlist exists to prevent. `Anonymous` is accepted only with our macro
    name: masking adds exactly one function to the validated tree.
    """
    for node in tree.walk():
        name = node.__class__.__name__
        if name == "Anonymous":
            if str(node.this).lower() != MACRO_NAME:
                return False
            continue
        if name not in MASK_NODES:
            return False
    return True


#: Replacement for a redacted value. Length is not preserved: the length of a
#: surname or an address is itself information.
REDACTED: Final = "***"

#: Published hash length. Twelve hex characters are 48 bits: enough to make
#: collisions rare (zero across the 104,164 distinct IPs of the dev profile) and
#: short enough that the hash is not a copy of the value.
HASH_HEX: Final = 12


def mask_query(
    query: ValidatedQuery, *, policy: AccessPolicy, config: MaskConfig
) -> ValidatedQuery | RejectionReason:
    """Rewrite the validated tree so `mask` columns come out masked.

    Returns a new `ValidatedQuery` with `masked_columns` set (the evidence recorded in
    the audit log), or a rejection if it finds a shape the guard should not have let
    through.
    """
    role = query.principal.role
    tree = query.ast.copy()
    select = tree.find(exp.Select)
    if select is None:
        return query

    lineage = resolve(tree, lineage_index_for(query))
    masked: set[str] = set()

    for projection in list(select.expressions):
        target = projection.this if isinstance(projection, exp.Alias) else projection
        bases = _masked_bases(target, lineage, policy, role)
        if not bases:
            continue
        if not isinstance(target, exp.Column):
            return _not_a_bare_column(target)
        base = bases[0]
        column_policy = policy.column(base)
        replacement = _mask_expression(target, column_policy, config)
        if replacement is None:
            return _unknown_transformation(base, column_policy.transformation)
        if isinstance(projection, exp.Alias):
            projection.set("this", replacement)
        else:
            select.set(
                "expressions",
                [
                    exp.alias_(replacement, target.name) if p is projection else p
                    for p in select.expressions
                ],
            )
        masked.update(bases)

    if not masked:
        return query
    return dataclasses.replace(query, ast=tree, masked_columns=tuple(sorted(masked)))


def lineage_index_for(query: ValidatedQuery) -> dict[str, tuple[str, ...]]:
    """Catalog lineage index, to resolve lineage again on the copied tree.

    Recomputed rather than reused from the guard: `mask_query` works on a copy, and
    the guard's index is keyed by `id()` of the original nodes, so reusing it would
    silently answer for a different tree.
    """
    from datawarden.catalog import SCHEMA_PATH, load_generated

    return lineage_index(load_generated(SCHEMA_PATH))


def _masked_bases(
    target: exp.Expression,
    lineage: dict[int, tuple[str, ...]],
    policy: AccessPolicy,
    role: object,
) -> list[str]:
    """Base columns of this projection that are masked for the role."""
    found: list[str] = []
    for column in target.find_all(exp.Column):
        for base in lineage.get(id(column), ()):
            if base == UNKNOWN:
                continue
            if policy.level_for(base, role) is Level.MASK and base not in found:  # type: ignore[arg-type]
                found.append(base)
    return found


def _mask_expression(
    column: exp.Column, column_policy: object, config: MaskConfig
) -> exp.Expression | None:
    """Mask expression for a column, preserving NULL.

    Shape: `CASE WHEN <col> IS NULL THEN NULL ELSE <mask> END`. `policy.yaml` states
    that a NULL `last_name_2` means "this naming system has no second surname", i.e.
    it is data, and `resultset-equality.md` treats NULL and empty string as distinct.
    Masking NULL to `'***'` would invent a value and silently corrupt the evaluation
    reference answers.
    """
    body = _mask_body(column, column_policy)
    if body is None:
        return None
    # Same wrapper for all four transformations, including `generalizar` where the
    # substitute is another column: the preserved NULL is the original column's,
    # since an age band for a row with no birth date would be invented.
    return exp.Case(
        ifs=[exp.If(this=exp.Is(this=column.copy(), expression=exp.Null()), true=exp.Null())],
        default=body,
    )


def _mask_body(column: exp.Column, column_policy: object) -> exp.Expression | None:
    """The substitute expression, without the NULL-preserving wrapper.

    Takes no configuration: the pepper lives in the engine macro, not in the tree, so
    this function never handles the secret.
    """
    transformation = getattr(column_policy, "transformation", None)

    if transformation == "tachar":
        return exp.Literal.string(REDACTED)

    if transformation == "generalizar":
        generalized = getattr(column_policy, "generalized", None)
        if not generalized:
            return None
        # Keep the original table qualifier: the query may alias the table, and the
        # bare contract name would not resolve.
        return exp.column(str(generalized).split(".")[-1], table=column.table or None)

    if transformation == "ultimos_n":
        keep = getattr(column_policy, "keep_last_n", None)
        if not keep:
            return None
        # Build concrete node classes, not `exp.func("CONCAT", ...)`: in sqlglot 30
        # `func()` returns a generic `Func`, and the guard allowlist matches by class
        # name. Emitting the class the allowlist expects keeps `MASK_NODES` checkable.
        return exp.Concat(
            expressions=[
                exp.Literal.string(REDACTED),
                exp.Right(this=_as_text(column), expression=exp.Literal.number(int(keep))),
            ]
        )

    if transformation == "hash_estable":
        # `warden_hash(CAST(col AS TEXT))`. Pepper and truncation live inside the
        # macro (`mask/macro.py`), so the key neither travels with each query nor
        # lands in the audit record's `sql` field.
        return exp.Anonymous(this=MACRO_NAME, expressions=[_as_text(column)])

    return None


def _as_text(column: exp.Column) -> exp.Expression:
    """`CAST(col AS TEXT)`. Not every maskable column is text: `ip_address_int` is an
    integer and `birth_date` a date."""
    return exp.cast(column.copy(), "TEXT")


def _not_a_bare_column(target: exp.Expression) -> RejectionReason:
    return RejectionReason(
        rule_id="INTERNAL",
        code="mask_shape_unexpected",
        message=(
            f"the projection carrying a masked column is a "
            f"{target.__class__.__name__} and not a bare column reference, which the "
            "guard should have rejected before this point"
        ),
        suggestion=(
            "project the masked column on its own instead of inside an expression. "
            "If this rejection appears for a query the guard accepted, it is a guard "
            "defect and not a query defect: report it"
        ),
        severity=Severity.INTERNAL,
        position=Position.PROJECTION,
        subject=target.__class__.__name__,
        retryable=False,
    )


def _unknown_transformation(base: str, transformation: object) -> RejectionReason:
    return RejectionReason(
        rule_id="INTERNAL",
        code="mask_transformation_missing",
        message=(
            f"column {base} is masked for this role but its policy row declares "
            f"transformation {transformation!r}, which this masker cannot build"
        ),
        suggestion=(
            "declare one of the four transformations of docs/spec/policy.yaml for "
            "that row: generalizar, tachar, ultimos_n or hash_estable. A masked "
            "column with no way to mask it is refused, never shown"
        ),
        severity=Severity.INTERNAL,
        position=Position.PROJECTION,
        subject=base,
        retryable=False,
    )
