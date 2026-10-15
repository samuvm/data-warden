"""Builds the `GuardContext`: parse and qualify. No decisions are made here.

`qualify()` against the catalog schema removes more code than any other choice in the
guard (`docs/RULES.md` §7, item 1): once the tree is qualified, aliases, nested CTEs and
`SELECT *` are already resolved, so there is no alias left to look up. Eleven of the
fourteen rules rely on it.

This module never rejects. It returns either a context or a described problem, and the
validator decides what to do with it; the validator is the only place allowed an
`except Exception` (I-04).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import cast

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import OptimizeError, ParseError, SqlglotError
from sqlglot.optimizer.qualify import qualify

from datawarden.catalog.types import CatalogSchema
from datawarden.domain.types import Principal
from datawarden.guard.rule import GuardContext
from datawarden.principal.policy import AccessPolicy

#: Guard budget per query. It is both the hard ceiling of `G-GUARD-P95` and the
#: fail-closed timeout: past it, the query is rejected.
DEFAULT_TIMEOUT_MS: float = 250.0


@dataclass(frozen=True, slots=True)
class ParseProblem:
    """What prevented building the context, in the guard's vocabulary."""

    stage: str
    message: str
    subject: str | None = None


def parse(sql: str, dialect: str) -> exp.Expression | ParseProblem:
    """Parse exactly one statement. Several statements are a problem, not a query.

    `parse_one` would accept the first and silently drop the rest, which is how
    `SELECT 1; DROP TABLE x` gets past a hasty validator: validation sees the `SELECT`
    and the engine runs both.
    """
    try:
        statements = sqlglot.parse(sql, dialect=dialect)
    except ParseError as exc:
        return ParseProblem(stage="parse", message=_first_line(str(exc)))
    except SqlglotError as exc:  # tokenizer, recursion, dialect
        return ParseProblem(stage="parse", message=_first_line(str(exc)))

    real = [s for s in statements if s is not None]
    if not real:
        return ParseProblem(stage="parse", message="the input contains no statement")
    if len(real) > 1:
        return ParseProblem(
            stage="parse",
            message=f"the input contains {len(real)} statements and only one is allowed",
        )
    # `cast` rather than an annotation: sqlglot types `parse()` with a self-type (`Expr`)
    # that strict type checking does not unify with `Expression`. Widening this
    # function's return type instead would leak sqlglot generics into the rest of the
    # guard, which reasons about `Expression`.
    return cast("exp.Expression", real[0])


def qualify_tree(
    tree: exp.Expression, schema: CatalogSchema, dialect: str
) -> exp.Expression | ParseProblem:
    """Qualify against the generated catalog. A failure here is a rejection.

    `validate_qualify_columns=True` makes a nonexistent column fail here rather than
    later: without it, `SELECT missing FROM t` would pass the whole guard and fail in
    the engine, where no actionable message can be given.
    """
    try:
        qualified: exp.Expression = qualify(
            tree.copy(),
            schema=schema.sqlglot_schema(),
            dialect=dialect,
            validate_qualify_columns=True,
            quote_identifiers=False,
            identify=False,
        )
        return qualified
    except OptimizeError as exc:
        return ParseProblem(stage="qualify", message=_first_line(str(exc)))
    except SqlglotError as exc:
        return ParseProblem(stage="qualify", message=_first_line(str(exc)))


def build_context(
    *,
    raw_sql: str,
    tree: exp.Expression,
    schema: CatalogSchema,
    policy: AccessPolicy,
    principal: Principal,
    dialect: str,
    max_rows: int,
    timeout_ms: float = DEFAULT_TIMEOUT_MS,
) -> GuardContext:
    """Package the context and start the clock."""
    return GuardContext(
        raw_sql=raw_sql,
        tree=tree,
        schema=schema,
        policy=policy,
        principal=principal,
        dialect=dialect,
        max_rows=max_rows,
        deadline=time.monotonic() + timeout_ms / 1000.0,
        lineage=lineage_index(schema),
    )


def lineage_index(schema: CatalogSchema) -> dict[str, tuple[str, ...]]:
    """`table.column` -> base columns, flattened once per catalog.

    Built here rather than in each rule because walking 428 columns per query does not
    fit in the 25 ms of `G-GUARD-P95`. A long-running server computes it at startup; the
    index is immutable as long as the catalog is.
    """
    return {
        f"{table.name.lower()}.{column.name.lower()}": column.derives_from
        for table in schema.tables
        for column in table.columns
    }


def _first_line(message: str) -> str:
    """First line of sqlglot's message, truncated. A rejection is not a stack trace.

    There is also a security reason: sqlglot errors quote the input fragment, which may
    contain a literal with data, and the rejection contract forbids revealing values
    (I-09).
    """
    first = message.strip().splitlines()[0] if message.strip() else "unparseable input"
    return first[:200]
