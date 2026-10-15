"""R009: `SELECT *` does not survive the guard (invariant I-10).

`qualify()` with the catalog schema expands the star on its own, so this rule is
usually a check that the expansion happened. That check matters: if a star survived,
the guard would be reasoning about a column list it never saw.

`qualify()` alone is not enough because, when it cannot tell which relation to expand
(a schemaless subquery, a table function, a malformed `UNION`), it returns the tree with
the star still in it and raises nothing. R008 would then check the policy against zero
columns and pass. An unexpanded `SELECT *` is exactly how a sensitive column reaches a
response without any rule naming it.
"""

from __future__ import annotations

from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.rule import PASS, POST_QUALIFY, GuardContext, RuleResult, reject
from datawarden.guard.rules import messages


class StarExpansionRule:
    """No star remains in the validated tree."""

    rule_id = "R009"
    code = "star_not_expanded"
    severity = Severity.SECURITY
    summary = "`SELECT *` is expanded against the catalog or rejected"
    families: tuple[str, ...] = ("select_estrella",)
    phase = POST_QUALIFY

    def check(self, ctx: GuardContext) -> RuleResult:
        star = next(
            (
                node
                for node in ctx.tree.find_all(exp.Star)
                # `COUNT(*)` is not an unexpanded star: it counts rows and names
                # no column. Rejecting it would reject most legitimate aggregates.
                if not isinstance(node.parent, exp.Func)
            ),
            None,
        )
        if star is None:
            return PASS
        parent = star.parent
        where = parent.__class__.__name__ if parent is not None else "the query"
        message, suggestion = messages.star_survived(where)
        return reject(
            self,
            message=message,
            suggestion=suggestion,
            position=Position.PROJECTION,
            subject="*",
            retryable=True,
        )


RULE = StarExpansionRule()
