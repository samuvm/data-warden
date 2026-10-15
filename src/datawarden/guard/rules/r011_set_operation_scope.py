"""R011: set-operation branches are queries, and there are few of them.

R004 already walks the whole tree, so a branch reading an out-of-scope table is caught
there. R011 adds what R004 cannot see:

1. Every branch is a query, not a `VALUES` list. `SELECT a FROM t UNION ALL VALUES
   ('...')` injects rows that do not come from the warehouse and then appear in the
   answer as if they were data: content injection shaped like a legitimate query.
2. Branches are few. A hundred unioned branches are a hundred scans, and the cost
   estimator sums per branch: the total grows fast while no single branch looks
   expensive.
"""

from __future__ import annotations

from typing import Final

from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.rule import PASS, POST_QUALIFY, GuardContext, RuleResult, reject
from datawarden.guard.rules import messages

MAX_BRANCHES: Final = 10

SET_OPERATIONS: Final = (exp.Union, exp.Except, exp.Intersect)


class SetOperationScopeRule:
    """Every branch of a set operation is a catalog query."""

    rule_id = "R011"
    code = "set_operation_branch"
    severity = Severity.SECURITY
    summary = "UNION/EXCEPT/INTERSECT branches are queries, and there are few of them"
    families: tuple[str, ...] = ("union_con_datos_inyectados", "demasiadas_ramas")
    phase = POST_QUALIFY

    def check(self, ctx: GuardContext) -> RuleResult:
        branches = 0
        for node in ctx.tree.walk():
            if not isinstance(node, SET_OPERATIONS):
                continue
            branches += 1
            for side in (node.this, node.expression):
                if isinstance(side, exp.Values) or (
                    side is not None and side.find(exp.Values) is not None
                ):
                    message, suggestion = messages.values_branch()
                    return reject(
                        self,
                        message=message,
                        suggestion=suggestion,
                        position=Position.SUBQUERY,
                        subject="VALUES",
                        retryable=False,
                    )
        if branches >= MAX_BRANCHES:
            message, suggestion = messages.too_many_branches(branches + 1, MAX_BRANCHES)
            return reject(
                self,
                message=message,
                suggestion=suggestion,
                position=Position.SUBQUERY,
                subject=f"{branches + 1} branches",
                retryable=True,
            )
        return PASS


RULE = SetOperationScopeRule()
