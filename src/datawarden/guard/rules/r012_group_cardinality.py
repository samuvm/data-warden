"""R012: single-person groups requested through aggregation.

`SELECT count(*) ... GROUP BY national_id` exposes the column through cardinality
(`docs/RULES.md` §7, item 7). A cleaner variant needs no sensitive column at all:

    SELECT country_code, count(*) FROM dim_customer GROUP BY country_code
    HAVING count(*) = 1

returns, country by country, the cohorts of a single person. No sensitive column
appears in the response, yet each row is an individual: explicitly asking for groups of
size one is asking for individuals shaped as aggregates.

This rule closes the two structural ways of asking for them:

1. A `HAVING` that compares a count with a literal below `K_MIN`, or bounds it from
   above.
2. Grouping, or partitioning a window, by a column the policy classifies as an
   identifier or quasi-identifier, even when it is `allow` for that role.

Declared limit, shared with the policy: real k-anonymity requires counting before
returning, which changes the cost of every grouped query, and `docs/spec/policy.yaml`
lists it as out of scope. This rule stops the structural forms, not all of them: a
combination of `allow` columns can still isolate a person, and that is recorded in
`limites_declarados` rather than hidden.
"""

from __future__ import annotations

from typing import Final

from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.position import table_and_column
from datawarden.guard.rule import PASS, POST_QUALIFY, GuardContext, RuleResult, reject
from datawarden.guard.rules import messages

#: Minimum group size that can be requested explicitly. Five is the usual floor in
#: official statistics for publishing a cell. It applies to the predicate (what is asked
#: for), not to the result, which cannot be bounded without counting before returning.
K_MIN: Final = 5

#: Policy data types that identify a person, directly or nearly. They are the
#: `data_type` vocabulary of the signed policy contract: columns are classified there, so
#: reclassifying a column changes this rule's behaviour without a code change.
IDENTIFYING_TYPES: Final = frozenset(
    {
        "identificador_directo",
        "identificador_directo_empleado",
        "identificador_nacional",
        "cuasi_identificador",
        "contacto",
        "contacto_empleado",
        "pseudonimo",
        "identificador_de_red",
        "identificador_de_dispositivo",
        "domicilio",
        "cuenta_bancaria",
        "identificador_fiscal",
        "texto_libre_con_pii",
    }
)

_COUNT_NODES: Final = (exp.Count, exp.ApproxDistinct)
_SMALL_COMPARISONS: Final = (exp.EQ, exp.LT, exp.LTE)


class GroupCardinalityRule:
    """Neither groups of size one nor grouping by an identifier."""

    rule_id = "R012"
    code = "group_too_small"
    severity = Severity.POLICY
    summary = "No groups below k are requested, and no grouping by identifiers"
    families: tuple[str, ...] = (
        "agregacion_de_grupo_unico",
        "reidentificacion_por_cardinalidad",
    )
    phase = POST_QUALIFY

    def check(self, ctx: GuardContext) -> RuleResult:
        rejection = self._check_having(ctx)
        if rejection is not None:
            return rejection
        return self._check_grouping(ctx) or PASS

    def _check_having(self, ctx: GuardContext) -> RuleResult | None:
        """A `HAVING` that bounds group size from above asks for the rare groups.

        For equality, rejecting literals below `K_MIN` is correct: `count(*) = 3` asks
        for groups of three. For an upper bound it is not: `count(*) <= 5` returns groups
        of 1 to 5, and `count(*) <= 1000` returns every small group in the warehouse,
        single-person groups included.

        Bounding group size from above is the k-anonymity query in reverse ("show me the
        rare cohorts"), so `<` or `<=` on a count is always rejected unless the same
        `HAVING` also sets a floor of at least `K_MIN` (`count(*) >= 5 AND count(*) <=
        100`), which is the legitimate way to ask for a band of sizes.

        Equality is unchanged: `count(*) = 5` asks for groups of exactly five, and five
        is the minimum.
        """
        for having in ctx.tree.find_all(exp.Having):
            floor = _group_floor(having)
            for comparison in having.find_all(*_SMALL_COMPARISONS):
                left, right = comparison.this, comparison.expression
                for count_side, literal_side in ((left, right), (right, left)):
                    if not isinstance(count_side, _COUNT_NODES):
                        continue
                    value = _int_literal(literal_side)
                    if value is None:
                        continue
                    bounded_above = isinstance(comparison, (exp.LT, exp.LTE))
                    if bounded_above:
                        # An upper bound without a floor always returns groups of one.
                        if floor is not None and floor >= K_MIN:
                            continue
                    elif value >= K_MIN:
                        continue
                    message, suggestion = messages.having_group_too_small(
                        comparison.key, value, K_MIN
                    )
                    return reject(
                        self,
                        message=message,
                        suggestion=suggestion,
                        position=Position.HAVING,
                        subject=f"count {comparison.key} {value}",
                        retryable=True,
                    )
        return None

    def _check_grouping(self, ctx: GuardContext) -> RuleResult | None:
        keys: list[tuple[exp.Column, Position]] = []
        for group in ctx.tree.find_all(exp.Group):
            keys.extend((c, Position.GROUP_BY) for c in group.find_all(exp.Column))
        for window in ctx.tree.find_all(exp.Window):
            for part in window.args.get("partition_by") or []:
                keys.extend((c, Position.WINDOW_PARTITION) for c in part.find_all(exp.Column))

        for column, position in keys:
            table, name = table_and_column(column)
            for base in ctx.sources_of(column, table, name):
                data_type = ctx.policy.column(base).data_type
                if data_type not in IDENTIFYING_TYPES:
                    continue
                alternative = ctx.policy.generalized_for(base)
                message, suggestion = messages.group_by_identifier(base, data_type, alternative)
                return reject(
                    self,
                    message=message,
                    suggestion=suggestion,
                    position=position,
                    subject=base,
                    alternative=alternative,
                    retryable=True,
                )
        return None


def _group_floor(having: exp.Having) -> int | None:
    """The floor the `HAVING` puts on group size, if any.

    `count(*) >= 5` or `count(*) > 4`. A floor is what turns an upper bound into a
    legitimate band: groups of 5 to 100 is normal analysis; groups of "100 or fewer" is
    asking for the rare ones.
    """
    best: int | None = None
    for comparison in having.find_all(exp.GT, exp.GTE):
        for count_side, literal_side in (
            (comparison.this, comparison.expression),
            (comparison.expression, comparison.this),
        ):
            if not isinstance(count_side, _COUNT_NODES):
                continue
            value = _int_literal(literal_side)
            if value is None:
                continue
            # `> 4` is a floor of 5, and so is `>= 5`.
            floor = value + 1 if isinstance(comparison, exp.GT) else value
            best = floor if best is None else max(best, floor)
    return best


def _int_literal(node: exp.Expression | None) -> int | None:
    """Integer value of a literal, or `None` if it is not an integer literal."""
    if isinstance(node, exp.Literal) and node.is_int:
        return int(node.this)
    if isinstance(node, exp.Neg):
        inner = _int_literal(node.this)
        return None if inner is None else -inner
    return None


RULE = GroupCardinalityRule()
