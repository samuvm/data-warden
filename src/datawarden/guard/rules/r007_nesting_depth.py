"""R007: maximum nesting depth.

Its value is less about stopping an attack (R013 already bounds total size) than about
stopping a class of query whose cost cannot be estimated: each level of correlated
subquery multiplies engine work by the row count of the level above, and the cost
estimator looks at bytes scanned, which capture none of that.

Eight is not arbitrary. The benchmark questions that use a window or a correlated
subquery reach at most four levels (two CTEs, one subquery, one window). Eight doubles
that headroom and still bounds the worst case.
"""

from __future__ import annotations

from typing import Final

from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.rule import PASS, POST_QUALIFY, GuardContext, RuleResult, reject
from datawarden.guard.rules import messages

MAX_DEPTH: Final = 8

#: What counts as one level. `Paren` does not: grouping an arithmetic expression nests
#: nothing, and counting it would make `((a+b)*c)` consume levels.
NESTING_NODES: Final = (exp.Select, exp.Subquery, exp.CTE)


class NestingDepthRule:
    """Query nesting does not exceed `MAX_DEPTH`."""

    rule_id = "R007"
    code = "nesting_too_deep"
    severity = Severity.POLICY
    summary = "Subquery and CTE depth does not exceed the maximum"
    families: tuple[str, ...] = ("anidamiento_excesivo",)
    phase = POST_QUALIFY

    def check(self, ctx: GuardContext) -> RuleResult:
        deepest = 0
        for node in ctx.tree.walk():
            if not isinstance(node, NESTING_NODES):
                continue
            depth = sum(
                1 for ancestor in _ancestors(node) if isinstance(ancestor, NESTING_NODES)
            )
            deepest = max(deepest, depth)
        if deepest > MAX_DEPTH:
            message, suggestion = messages.nesting_too_deep(deepest, MAX_DEPTH)
            return reject(
                self,
                message=message,
                suggestion=suggestion,
                position=Position.SUBQUERY,
                subject=f"depth {deepest}",
                retryable=True,
            )
        return PASS


def _ancestors(node: exp.Expression) -> list[exp.Expression]:
    """The node and all its ancestors, innermost first.

    Returns a list instead of looping over `.parent` because sqlglot types `.parent` with
    a self-type that strict type checking will not reassign to an `Expression` variable.
    Doing it once here avoids repeating the workaround in every rule that needs ancestry.
    """
    chain: list[exp.Expression] = []
    current: exp.Expression | None = node
    while current is not None:
        chain.append(current)
        parent = current.parent
        current = parent if isinstance(parent, exp.Expression) else None
    return chain


RULE = NestingDepthRule()
