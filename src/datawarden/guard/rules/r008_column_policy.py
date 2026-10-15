"""R008: role, column and position, resolved through lineage.

Output masking alone is not enough. Two decisions make this rule effective.

1. Position, not just the column. A `mask` level admits the column as a direct
   projection and rejects it in `WHERE`, `JOIN ON`, `GROUP BY`, `ORDER BY`, `HAVING`,
   `QUALIFY`, inside any function and as a window partition key. A column that can be
   filtered on can be reconstructed question by question without its value ever
   being returned.

2. Lineage, not name. The check runs on the base columns a reference derives from, as
   resolved with sqlglot when the catalog was generated, rather than on `table.column`
   as written. That closes three evasions that would otherwise need three rules:

    SELECT full_name FROM v_customer          -- a view that re-exposes the column
    SELECT concat(first_name,last_name) ...   -- a derived expression
    WITH c AS (SELECT birth_date AS b ...) SELECT b FROM c WHERE b > '1990-01-01'

In the third, `qualify()` resolves the CTE and lineage follows `c.b` back to
`dim_customer.birth_date`. An alias does not change which column a value comes from,
and a policy that matched by name would miss all three.

The rejection names the way out. When the policy publishes a generalised column
(`age_band` for `birth_date`), the suggestion names it. A rejection without an
alternative blocks work instead of redirecting it, and a guard that blocks work ends up
disabled (I-09).
"""

from __future__ import annotations

from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.position import position_of, table_and_column
from datawarden.guard.query_lineage import UNKNOWN as LINEAGE_UNKNOWN
from datawarden.guard.rule import PASS, POST_QUALIFY, GuardContext, RuleResult, reject
from datawarden.guard.rules import messages
from datawarden.principal.policy import Level


class ColumnPolicyRule:
    """Every column, in its position, is legal for this role."""

    rule_id = "R008"
    code = "column_policy"
    severity = Severity.POLICY
    summary = "Role, column and tree position, resolved through lineage"
    families: tuple[str, ...] = (
        "columna_denegada",
        "columna_enmascarada_en_predicado",
        "expresion_derivada",
        "alias_que_oculta",
        "vista_que_reexpone",
    )
    phase = POST_QUALIFY

    def check(self, ctx: GuardContext) -> RuleResult:
        role = ctx.principal.role
        for column in ctx.tree.find_all(exp.Column):
            table, name = table_and_column(column)
            position = position_of(column)
            for base in ctx.sources_of(column, table, name):
                if base == LINEAGE_UNKNOWN:
                    # Fail-closed: if the guard cannot trace where a column comes from,
                    # it cannot claim the column is safe. Lineage that fails silently
                    # is a leak.
                    return _reject_unknown(self, ctx, name, position)
                level = ctx.policy.level_for(base, role)
                if level is Level.ALLOW:
                    continue
                if ctx.policy.is_position_allowed(base, role, position):
                    continue
                return _reject_for(self, ctx, base, name, table, position, level)
        return PASS


def _reject_unknown(
    rule: ColumnPolicyRule,
    ctx: GuardContext,
    name: str,
    position: Position,
) -> RuleResult:
    """Rejection for a column whose origin cannot be determined."""
    message, suggestion = messages.lineage_unknown(name)
    return reject(
        rule,
        message=message,
        suggestion=suggestion,
        position=position,
        subject=name,
        retryable=True,
    )


def _reject_for(
    rule: ColumnPolicyRule,
    ctx: GuardContext,
    base: str,
    written_name: str,
    written_table: str,
    position: Position,
    level: Level,
) -> RuleResult:
    """The rejection, with the detail that makes it actionable."""
    # The decisions stay here so mutation testing measures them: which level was
    # violated, whether the column was reached under another name, and whether the
    # policy publishes an alternative. The wording lives in `messages.py`, which is
    # excluded from mutation.
    alternative = ctx.policy.generalized_for(base)
    written = f"{written_table}.{written_name}" if written_table else written_name
    message, suggestion = messages.column_policy(
        base,
        ctx.principal.role.value,
        position,
        denied=level is Level.DENY,
        written=written if base != written else None,
        alternative=alternative,
    )

    return reject(
        rule,
        message=message,
        suggestion=suggestion,
        position=position,
        subject=base,
        alternative=alternative,
        retryable=True,
    )


RULE = ColumnPolicyRule()
