"""R010: no write, anywhere in the tree.

Runs before R001 on purpose. R001 checks the class of the root statement; this rule
walks the whole tree, which is what catches the real attack:

    WITH x AS (DELETE FROM dim_customer RETURNING 1) SELECT * FROM x

The root is a `Select`. A validator that only inspects the root accepts it, and the
engine deletes the table. A CTE hiding a write is also why the two rules are separate:
merged, the message would say "only SELECT is accepted" about a query that starts with
SELECT.

Not retryable. Rephrasing a `DELETE` does not turn it into a question; retrying only
spends tokens and skews the recovery metric.
"""

from __future__ import annotations

from typing import Final

from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.rule import PASS, PRE_QUALIFY, GuardContext, RuleResult, reject
from datawarden.guard.rules import messages

#: Data writes only. `PRAGMA`, `SET`, `ATTACH` or `INSTALL` change engine state, not
#: data: R001 stops them at the root, where they can syntactically appear, and R002
#: anywhere else. The split is about the message: a `DELETE` deserves "this system is
#: read-only", a `PRAGMA` deserves "this is not a question about data".
#:
#: Matched by node class, not keyword: `DeLeTe`, `/*x*/DELETE` and `delete` are the same
#: node, with nothing to normalise.
WRITE_NODES: Final = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Merge,
    exp.Grant,
    exp.Copy,
    exp.Returning,
)


class NoWriteNodeRule:
    """Zero write nodes at any depth of the tree."""

    rule_id = "R010"
    code = "write_node_present"
    severity = Severity.SECURITY
    summary = "No write node appears in the tree, at any depth"
    families: tuple[str, ...] = (
        "escritura_directa",
        "escritura_oculta_en_cte",
        "escritura_en_subconsulta",
    )
    phase = PRE_QUALIFY

    def check(self, ctx: GuardContext) -> RuleResult:
        for node in ctx.tree.walk():
            if isinstance(node, WRITE_NODES):
                kind = node.__class__.__name__
                # The decision: where the write node was. It is computed from the
                # node's ancestry in the tree and returned as a `Position`, which the
                # case corpus asserts by value; it must never be derived from text.
                position = (
                    Position.CTE
                    if node.find_ancestor(exp.CTE) is not None
                    else Position.SUBQUERY
                    if node.find_ancestor(exp.Subquery) is not None
                    else Position.STATEMENT
                )
                message, suggestion = messages.write_node_present(kind, position)
                return reject(
                    self,
                    message=message,
                    suggestion=suggestion,
                    position=position,
                    subject=kind,
                    retryable=False,
                )
        return PASS


RULE = NoWriteNodeRule()
