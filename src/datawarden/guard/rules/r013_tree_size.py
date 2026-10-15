"""R013: the tree has a bounded size.

Runs first of the fourteen, for arithmetic rather than importance. Every other rule
walks the tree; if the tree is unbounded, so is the guard's cost, and `G-GUARD-P95`
(25 ms p95, 250 ms hard ceiling) becomes unachievable by construction. A guard that
needs ten seconds to decide is a guard that is down.

It is the only rule that protects the guard itself rather than the data, hence the
`SECURITY` severity: an AST bomb is an evasion attempt by exhaustion, not a typo.
"""

from __future__ import annotations

from typing import Final

from datawarden.domain.types import Position, Severity
from datawarden.guard.rule import PASS, PRE_QUALIFY, GuardContext, RuleResult, reject
from datawarden.guard.rules import messages

#: Maximum node count. A legitimate query on this warehouse (four tables, a window and a
#: couple of subqueries) is around 200 nodes, and all 60 benchmark questions fit
#: comfortably. 4,000 leaves an order of magnitude of headroom and still bounds the cost.
MAX_NODES: Final = 4_000

#: Maximum input length in characters. The validator checks it before parsing; it is
#: defined here so the number lives in one place.
MAX_SQL_CHARS: Final = 40_000


class TreeSizeRule:
    """The tree fits within the guard's budget."""

    rule_id = "R013"
    code = "tree_too_large"
    severity = Severity.SECURITY
    summary = "The syntax tree does not exceed the maximum node count"
    families: tuple[str, ...] = ("bomba_de_ast", "entrada_desmesurada")
    phase = PRE_QUALIFY

    def check(self, ctx: GuardContext) -> RuleResult:
        nodes = sum(1 for _ in ctx.tree.walk())
        if nodes > MAX_NODES:
            message, suggestion = messages.tree_too_large(nodes, MAX_NODES)
            return reject(
                self,
                message=message,
                suggestion=suggestion,
                position=Position.STATEMENT,
                subject=f"{nodes} nodes",
                retryable=True,
            )
        return PASS


RULE = TreeSizeRule()
