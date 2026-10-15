"""R001: a single statement, and a read.

Two checks that look like one.

Single: `sqlglot.parse` returns a list. `parse_one` would return the first statement and
silently drop the rest, which is how `SELECT 1; DROP TABLE dim_customer` gets past a
hasty validator: validation sees the `SELECT` and the engine runs both. The guard calls
`parse`, not `parse_one`, and reports a multi-statement input under this rule.

Read: the root must be a query. `PRAGMA`, `SET`, `EXPLAIN`, `INSTALL`, `ATTACH` and
similar do not write data (that is R010's job), but they do not ask anything either:
they change engine state, load extensions or reveal configuration. A warehouse queried
in natural language needs none of that.
"""

from __future__ import annotations

from typing import Final

from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.rule import PASS, PRE_QUALIFY, GuardContext, RuleResult, reject
from datawarden.guard.rules import messages

#: The only node classes allowed at the root. `Subquery` is included because a
#: parenthesised `(SELECT 1)` is a legitimate query.
READ_ROOTS: Final = (exp.Select, exp.Union, exp.Except, exp.Intersect, exp.Subquery)

#: Set operations, whose branches must also be queries.
SET_OPERATIONS: Final = (exp.Union, exp.Except, exp.Intersect)


class SingleReadStatementRule:
    """The tree root is a query, and there is only one."""

    rule_id = "R001"
    code = "not_a_read_statement"
    severity = Severity.SECURITY
    summary = "A single statement whose root is a read query"
    families: tuple[str, ...] = (
        "sentencia_no_consulta",
        "sentencia_multiple",
        "comando_de_motor",
    )
    phase = PRE_QUALIFY

    def check(self, ctx: GuardContext) -> RuleResult:
        root = ctx.tree
        if isinstance(root, READ_ROOTS):
            return self._check_branches(ctx) or PASS
        kind = root.__class__.__name__
        message, suggestion = messages.not_a_query(kind)
        return reject(
            self,
            message=message,
            suggestion=suggestion,
            position=Position.STATEMENT,
            subject=kind,
            retryable=False,
        )

    def _check_branches(self, ctx: GuardContext) -> RuleResult | None:
        """Every branch of a set operation is itself a query.

        This also prevents a segfault. `x UNION SELECT 1` parses without error (the root
        is a valid `Union`), but `qualify()` in the mypyc-compiled `sqlglot[c]` crashes
        the process when a branch is not a query.

        A segfault is the worst way to break fail-closed because no `except` catches it:
        the guard does not reject, the process dies. The only place to stop it is here,
        before qualification, by checking what has to hold anyway. The residual risk is
        recorded in the threat model: a native crash inside a compiled extension cannot
        be caught in-process, so the defence is never to reach it and, in production, to
        isolate the process.
        """
        for node in ctx.tree.walk():
            if not isinstance(node, SET_OPERATIONS):
                continue
            for side in (node.this, node.expression):
                if side is None or isinstance(side, READ_ROOTS):
                    continue
                kind = side.__class__.__name__
                message, suggestion = messages.branch_not_a_query(kind)
                return reject(
                    self,
                    message=message,
                    suggestion=suggestion,
                    position=Position.STATEMENT,
                    subject=kind,
                    retryable=True,
                )
        return None


RULE = SingleReadStatementRule()
