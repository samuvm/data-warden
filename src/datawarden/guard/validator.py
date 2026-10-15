"""Entry point of the guard.

`validate()` never raises: any internal failure, including a timeout, ends in a
`RejectionReason` verdict (fail-closed, invariant I-04). The guard's only
`except Exception` lives here. A guard that crashes on odd input hands control back to a
caller that cannot know whether the query was safe; turning internal failures into
rejections is what gives the `G-FAILCLOSED` property (arbitrary inputs, zero
exceptions) its meaning. The time budget is 250 ms per query, both the hard ceiling of
`G-GUARD-P95` and the fail-closed timeout.

On success it returns a `ValidatedQuery` carrying the AST; what reaches the engine is
`ast.sql(dialect)`, never the input string (I-02). If sqlglot misreads a query, the
engine runs what sqlglot understood rather than what the attacker wrote, which removes
parser-differential attacks by construction.
"""

from __future__ import annotations

import re
import time
from dataclasses import replace

from sqlglot import expressions as exp

from datawarden.catalog.types import CatalogSchema
from datawarden.domain.types import (
    Position,
    Principal,
    RejectionReason,
    Severity,
    ValidatedQuery,
    Verdict,
)
from datawarden.guard.allowlist import ALLOWED_NODES
from datawarden.guard.context import DEFAULT_TIMEOUT_MS, ParseProblem, build_context, parse
from datawarden.guard.context import qualify_tree as _qualify
from datawarden.guard.query_lineage import resolve as resolve_query_lineage
from datawarden.guard.registry import POST_RULES, PRE_RULES
from datawarden.guard.rule import GuardContext, Rule
from datawarden.guard.rules.r013_tree_size import MAX_SQL_CHARS
from datawarden.principal.policy import AccessPolicy


def validate(
    sql: str,
    *,
    principal: Principal,
    schema: CatalogSchema,
    policy: AccessPolicy,
    max_rows: int,
    dialect: str = "duckdb",
    timeout_ms: float = DEFAULT_TIMEOUT_MS,
) -> Verdict:
    """Validate a query against the fourteen rules. Always returns a verdict."""
    try:
        return _validate(
            sql,
            principal=principal,
            schema=schema,
            policy=policy,
            max_rows=max_rows,
            dialect=dialect,
            timeout_ms=timeout_ms,
        )
    except Exception as exc:
        # The exception message never reaches the client: it may quote input fragments,
        # and the rejection contract forbids revealing values. Only the type is named.
        return RejectionReason(
            rule_id="INTERNAL",
            code="guard_internal_error",
            message=(
                "the guard could not decide about this query and therefore refuses it; "
                f"the failure was a {type(exc).__name__} while validating"
            ),
            suggestion=(
                "simplify the query and try again. A guard that cannot decide always "
                "refuses: that is what fail-closed means"
            ),
            severity=Severity.INTERNAL,
            position=Position.STATEMENT,
            subject=type(exc).__name__,
            retryable=True,
        )


def _validate(
    sql: str,
    *,
    principal: Principal,
    schema: CatalogSchema,
    policy: AccessPolicy,
    max_rows: int,
    dialect: str,
    timeout_ms: float,
) -> Verdict:
    if len(sql) > MAX_SQL_CHARS:
        return RejectionReason(
            rule_id="R013",
            code="tree_too_large",
            message=(
                f"the input is {len(sql)} characters and the limit is {MAX_SQL_CHARS}; "
                "it is refused before parsing, because parsing it is already the cost"
            ),
            suggestion="ask a smaller question, or split it into several queries",
            severity=Severity.SECURITY,
            position=Position.STATEMENT,
            subject=f"{len(sql)} characters",
        )

    parsed = parse(sql, dialect)
    if isinstance(parsed, ParseProblem):
        return _parse_rejection(parsed)

    ctx = build_context(
        raw_sql=sql,
        tree=parsed,
        schema=schema,
        policy=policy,
        principal=principal,
        dialect=dialect,
        max_rows=max_rows,
        timeout_ms=timeout_ms,
    )

    ctx, verdict = _run(PRE_RULES, ctx)
    if verdict is not None:
        return verdict

    qualified = _qualify(ctx.tree, schema, dialect)
    if isinstance(qualified, ParseProblem):
        return _qualify_rejection(qualified)
    ctx = replace(
        ctx,
        tree=qualified,
        column_sources=resolve_query_lineage(qualified, ctx.lineage),
    )

    ctx, verdict = _run(POST_RULES, ctx)
    if verdict is not None:
        return verdict

    # Belt and braces. Pre-qualify rules saw the raw tree; `qualify()` and R006 have
    # rewritten it since. Checking the allowlist on the final tree guarantees that what
    # runs is what was validated, not a relative of it.
    final = _final_allowlist(ctx.tree)
    if final is not None:
        return final

    return ValidatedQuery(
        ast=ctx.tree,
        dialect=dialect,
        principal=principal,
        tables=_tables_of(ctx.tree),
        columns=_columns_of(ctx.tree),
        max_rows=max_rows,
        rule_ids=tuple(rule.rule_id for rule in (*PRE_RULES, *POST_RULES)),
    )


def _run(rules: tuple[Rule, ...], ctx: GuardContext) -> tuple[GuardContext, Verdict | None]:
    """Run the context through the rules, propagating rewrites."""
    for rule in rules:
        if ctx.deadline is not None and time.monotonic() > ctx.deadline:
            return ctx, _timeout(rule.rule_id)
        result = rule.check(ctx)
        if result.rejection is not None:
            return ctx, result.rejection
        if result.tree is not None:
            ctx = replace(ctx, tree=result.tree)
    return ctx, None


def _timeout(rule_id: str) -> RejectionReason:
    return RejectionReason(
        rule_id="INTERNAL",
        code="guard_timeout",
        message=(
            f"the guard ran out of time while checking {rule_id}; a query it cannot "
            "finish deciding about is refused, not passed"
        ),
        suggestion=(
            "simplify the query: fewer joins, less nesting, shorter expressions. A "
            "guard that takes too long is a guard that is down"
        ),
        severity=Severity.INTERNAL,
        position=Position.STATEMENT,
        subject=rule_id,
        retryable=True,
    )


def _parse_rejection(problem: ParseProblem) -> RejectionReason:
    """Rejection for unparseable input, naming its subject like every rejection.

    A rejection that does not name its subject cannot be grouped by cause or corrected
    on retry.
    """
    return RejectionReason(
        rule_id="R001",
        code="unparseable_input",
        message=f"the input is not one valid SQL query: {problem.message}",
        suggestion=(
            "send exactly one SELECT statement. Several statements separated by "
            "semicolons are never accepted, not even when the first one is harmless"
        ),
        severity=Severity.MALFORMED,
        position=Position.STATEMENT,
        subject=_subject_of(problem) or "the input",
        retryable=True,
    )


def _qualify_rejection(problem: ParseProblem) -> RejectionReason:
    return RejectionReason(
        rule_id="R004",
        code="relation_out_of_scope",
        message=(
            f"the query could not be resolved against the generated catalog: {problem.message}"
        ),
        suggestion=(
            "check the table and column names against the catalog resource. An "
            "ambiguous column also lands here: qualify it with its table"
        ),
        severity=Severity.SECURITY,
        position=Position.STATEMENT,
        subject=_subject_of(problem) or "the catalog",
        retryable=True,
    )


def _subject_of(problem: ParseProblem) -> str | None:
    """The name sqlglot quotes in its error message, if any.

    `Column 'decimals' could not be resolved` contains the actionable detail; moving it
    to `subject` lets the NL-to-SQL retry loop substitute that column instead of
    guessing again. Only the quoted name is taken, never the whole sentence, because
    sqlglot errors quote input fragments that may contain literals with data (I-09).
    """
    match = re.search(r"'([A-Za-z_][A-Za-z0-9_.]{0,63})'", problem.message)
    return match.group(1) if match else None


def _final_allowlist(tree: exp.Expression) -> RejectionReason | None:
    for node in tree.walk():
        name = node.__class__.__name__
        if name not in ALLOWED_NODES:
            return RejectionReason(
                rule_id="R002",
                code="node_not_allowed",
                message=(
                    f"after qualification the tree still contains a {name} node, which "
                    "is not on the allowlist; what runs has to be what was validated"
                ),
                suggestion="rewrite the query using plain analytical SQL",
                severity=Severity.SECURITY,
                position=Position.STATEMENT,
                subject=name,
            )
    return None


def _tables_of(tree: exp.Expression) -> tuple[str, ...]:
    return tuple(sorted({t.name.lower() for t in tree.find_all(exp.Table) if t.name}))


def _columns_of(tree: exp.Expression) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                f"{(c.table or '').lower()}.{c.name.lower()}" if c.table else c.name.lower()
                for c in tree.find_all(exp.Column)
            }
        )
    )


__all__ = ["validate"]
