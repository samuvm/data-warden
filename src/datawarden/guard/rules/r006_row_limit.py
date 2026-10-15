"""R006: the row cap belongs to the domain, and is injected when missing.

Invariant I-12 says why the domain sets it and never the engine: if each engine applied
its own, DuckDB and Athena would return different results for the same role, and
cross-engine parity would fail because of the limit rather than the abstraction.

It is the only rule that rewrites in most cases, so it runs last: the injected `LIMIT`
comes from the role's `max_rows`, and clamping earlier would make the other rules
reason about a tree different from the one submitted.

Three things are rejected, all for the same reason: the number of rows cannot be known
before execution.

- A non-literal `LIMIT` (`LIMIT (SELECT count(*) ...)`) makes the cap data-dependent.
- A negative `LIMIT` means nothing, and each engine handles it differently.
- A huge `OFFSET` makes the engine produce and discard millions of rows: the cost is a
  full scan even if the `LIMIT` says ten.
"""

from __future__ import annotations

from typing import Final

from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.rule import PASS, POST_QUALIFY, GuardContext, RuleResult, reject
from datawarden.guard.rules import messages

#: Maximum offset. `OFFSET 1000000` walks a million rows to discard them: the cost equals
#: returning them, and it does not show in the `LIMIT`.
MAX_OFFSET: Final = 100_000


class RowLimitRule:
    """A `LIMIT` exists, is a sane literal, and does not exceed `max_rows`."""

    rule_id = "R006"
    code = "row_limit"
    severity = Severity.POLICY
    summary = "The row cap is set by the domain: injected when missing, clamped when larger"
    families: tuple[str, ...] = ("sin_limite", "limite_no_estatico", "desplazamiento_excesivo")
    phase = POST_QUALIFY

    def check(self, ctx: GuardContext) -> RuleResult:
        tree = ctx.tree

        # Every subquery, not just the root. Otherwise wrapping the query in a CTE or a
        # subquery would hide its `OFFSET`: `SELECT * FROM (SELECT ... OFFSET 9000000) s`
        # would make the engine produce and discard nine million rows unnoticed.
        for select in tree.find_all(exp.Select, exp.Union, exp.Except, exp.Intersect):
            rejection = self._check_bounds(select)
            if rejection is not None:
                return rejection
        rejection = self._check_bounds(tree)
        if rejection is not None:
            return rejection

        limit = tree.args.get("limit") if hasattr(tree, "args") else None
        if limit is None:
            rewritten = tree.copy()
            rewritten.set("limit", exp.Limit(expression=exp.Literal.number(ctx.max_rows)))
            return RuleResult(
                tree=rewritten,
                notes=(f"R006 injected LIMIT {ctx.max_rows}",),
            )

        value = _int_literal(limit.expression)
        if value is not None and value > ctx.max_rows:
            rewritten = tree.copy()
            rewritten.set("limit", exp.Limit(expression=exp.Literal.number(ctx.max_rows)))
            return RuleResult(
                tree=rewritten,
                notes=(f"R006 clamped LIMIT {value} to {ctx.max_rows}",),
            )
        return PASS

    def _check_bounds(self, node: exp.Expression | exp.Query) -> RuleResult | None:
        """`LIMIT` and `OFFSET` are sane on this node, wherever it is in the tree."""
        if not hasattr(node, "args"):
            return None

        offset = node.args.get("offset")
        if offset is not None:
            value = _int_literal(offset.expression)
            if value is None:
                return self._not_static(offset.expression, "OFFSET")
            if value < 0 or value > MAX_OFFSET:
                message, suggestion = messages.offset_too_large(value, MAX_OFFSET)
                return reject(
                    self,
                    message=message,
                    suggestion=suggestion,
                    position=Position.STATEMENT,
                    subject=f"OFFSET {value}",
                    retryable=True,
                )

        limit = node.args.get("limit")
        if limit is None:
            return None
        value = _int_literal(limit.expression)
        if value is None:
            return self._not_static(limit.expression, "LIMIT")
        if value <= 0:
            message, suggestion = messages.limit_asks_for_nothing(value)
            return reject(
                self,
                message=message,
                suggestion=suggestion,
                position=Position.STATEMENT,
                subject=f"LIMIT {value}",
                retryable=True,
            )
        return None

    def _not_static(self, node: exp.Expression | None, clause: str) -> RuleResult:
        rendered = node.__class__.__name__ if node is not None else "nothing"
        message, suggestion = messages.limit_not_literal(clause, rendered)
        return reject(
            self,
            message=message,
            suggestion=suggestion,
            position=Position.STATEMENT,
            subject=clause,
            retryable=True,
        )


def _int_literal(node: exp.Expression | None) -> int | None:
    if isinstance(node, exp.Literal) and node.is_int:
        return int(node.this)
    if isinstance(node, exp.Neg):
        inner = _int_literal(node.this)
        return None if inner is None else -inner
    return None


RULE = RowLimitRule()
