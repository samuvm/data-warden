"""R005: every JOIN has a condition; no cartesian products.

The motivation is cost before security. `fact_payment_attempt` has 66.6 M rows and
`fact_order_line` 146.8 M; their cartesian product is 9.8 x 10^15 rows. The
pre-execution cost estimate cannot stop it: it looks at bytes scanned, and the scan of
a cartesian product is small. What explodes is materialisation.

Both spellings are covered, and the second is the one usually missed:

    FROM a CROSS JOIN b        -- obvious
    FROM a, b                  -- a JOIN without ON, via the comma

A `CROSS JOIN` against a fourteen-row reference table is legitimate and common, so the
rule does not forbid cartesian products outright, only those whose two sides are large.
Forbidding all of them would break normal queries, and a rule that breaks normal work
ends up disabled.
"""

from __future__ import annotations

from typing import Final

from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.rule import PASS, POST_QUALIFY, GuardContext, RuleResult, reject
from datawarden.guard.rules import messages

#: Relations for which a cartesian product is harmless: the warehouse's reference tables
#: (currencies, countries, decline reasons). Multiplying by fourteen rows is normal;
#: multiplying by 66 million is not.
SMALL_RELATION_PREFIXES: Final = ("ref_", "dim_date")


class JoinPredicateRule:
    """No JOIN between large relations lacks a condition."""

    rule_id = "R005"
    code = "cartesian_join"
    severity = Severity.POLICY
    summary = "Every JOIN between large relations has a condition"
    families: tuple[str, ...] = ("producto_cartesiano",)
    phase = POST_QUALIFY

    def check(self, ctx: GuardContext) -> RuleResult:
        for select in ctx.tree.find_all(exp.Select):
            for join in select.args.get("joins") or []:
                if join.args.get("using"):
                    continue
                on = join.args.get("on")
                if on is not None and not _is_constant(on):
                    continue
                right = _relation_name(join.this)
                left = _relation_name(_from_relation(select))
                if _is_small(right) or _is_small(left):
                    continue
                message, suggestion = messages.cartesian_join(left, right)
                return reject(
                    self,
                    message=message,
                    suggestion=suggestion,
                    position=Position.JOIN_ON,
                    subject=f"{left} x {right}",
                    retryable=True,
                )
        return PASS


def _is_constant(condition: exp.Expression) -> bool:
    """`ON TRUE` is a cartesian product under another name.

    A condition that mentions no column does not relate the two tables, it multiplies
    them. `ON TRUE`, `ON 1 = 1` and `ON 'a' = 'a'` are the same product written three
    ways, and checking for the absence of columns covers all of them without
    enumerating forms.
    """
    return condition.find(exp.Column) is None


def _from_relation(select: exp.Select) -> exp.Expression | None:
    """The `FROM` relation, located by node class rather than by args key.

    sqlglot 30 renamed the args key `from` to `from_`, and a lookup by key silently
    returned `None`. The left-hand name was then always empty: the rejection named "a
    subquery" where there was none, and the small-relation exemption never applied to
    the left side, so `FROM ref_fx_rate_daily JOIN fact_payment_attempt` was wrongly
    rejected.

    Node classes are stable across sqlglot versions; key names are a version detail.
    Only the direct args of the `Select` are searched, not `find()`, so a subquery in
    another scope is never picked up.
    """
    for value in select.args.values():
        if isinstance(value, exp.From):
            relation: exp.Expression | None = value.this
            return relation
    return None


def _relation_name(node: exp.Expression | None) -> str:
    if isinstance(node, exp.Table):
        return node.name.lower()
    return ""


def _is_small(name: str) -> bool:
    return bool(name) and name.startswith(SMALL_RELATION_PREFIXES)


RULE = JoinPredicateRule()
