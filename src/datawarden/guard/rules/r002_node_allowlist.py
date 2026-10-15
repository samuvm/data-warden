"""R002: the guard is an allowlist; an unknown node is rejected.

This rule underpins the other thirteen. A denylist of dangerous constructs falls short
the day DuckDB ships a new extension, without anyone touching this repository. An
allowlist errs the other way, which is the failure mode one can live with: someone
reads the rejection and the construct is added deliberately, with its commit and its
test case.

Matching is by node class, not by text. `TABLESAMPLE`, `tablesample` and
`/*x*/TABLESAMPLE` all parse to the same `exp.TableSample`, so there is no casing to
normalise and no inter-token comment to slip through.
"""

from __future__ import annotations

from datawarden.domain.types import Position, Severity
from datawarden.guard.allowlist import ALLOWED_NODES
from datawarden.guard.rule import PASS, PRE_QUALIFY, GuardContext, RuleResult, reject
from datawarden.guard.rules import messages


class NodeAllowlistRule:
    """Every node in the tree is on the allowlist."""

    rule_id = "R002"
    code = "node_not_allowed"
    severity = Severity.SECURITY
    summary = "Every node type in the tree is on the allowlist"
    families: tuple[str, ...] = ("nodo_desconocido", "construccion_no_analitica")
    phase = PRE_QUALIFY

    def check(self, ctx: GuardContext) -> RuleResult:
        for node in ctx.tree.walk():
            name = node.__class__.__name__
            if name in ALLOWED_NODES or name == "Anonymous":
                # `Anonymous` has its own rule and message (R003). Rejecting it here
                # would attribute the rejection to the wrong rule.
                continue
            message, suggestion = messages.node_not_allowed(name)
            return reject(
                self,
                message=message,
                suggestion=suggestion,
                position=Position.STATEMENT,
                subject=name,
                retryable=True,
            )
        return PASS


RULE = NodeAllowlistRule()
