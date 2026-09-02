"""R008 · rol, columna y POSICIÓN. Aquí vive la tesis del proyecto.

Es la regla que hace serio a `data-warden`, y lo que la distingue de un
enmascarado de salida —que es teatro— son dos decisiones:

**1 · La posición, no solo la columna.** Un nivel `mask` admite la columna en
proyección directa y la RECHAZA en `WHERE`, `JOIN ON`, `GROUP BY`, `ORDER BY`,
`HAVING`, `QUALIFY`, dentro de cualquier función y como clave de partición de
ventana. Si pudieras FILTRAR por una columna, la reconstruirías a base de preguntas
sin ver nunca su valor.

**2 · El LINAJE, no el nombre.** La comprobación no se hace sobre
`tabla.columna` tal y como aparece escrita, sino sobre las columnas BASE de las que
sale, que el catálogo resolvió con sqlglot al generarse. Eso cierra de una vez tres
evasiones que de otro modo necesitarían tres reglas:

    SELECT full_name FROM v_customer          -- una vista que reexpone la columna
    SELECT concat(first_name,last_name) ...   -- la expresión derivada de PROJECT.md
    WITH c AS (SELECT birth_date AS b ...) SELECT b FROM c WHERE b > '1990-01-01'

El tercero es el interesante: `qualify()` deja la CTE resuelta, y el linaje lleva
`c.b` hasta `dim_customer.birth_date`. **Un alias no cambia de qué columna sale un
dato**, y una política que casara por nombre no vería ninguno de los tres.

**Y el rechazo nombra la salida.** Cuando la política publica una columna
generalizada —`age_band` para `birth_date`—, la sugerencia la dice. Un rechazo sin
salida no redirige el trabajo: lo bloquea, y un guard que bloquea el trabajo se
desactiva en tres semanas (I-09).
"""

from __future__ import annotations

from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.position import position_of, table_and_column
from datawarden.guard.query_lineage import UNKNOWN as LINEAGE_UNKNOWN
from datawarden.guard.rule import PASS, POST_QUALIFY, GuardContext, RuleResult, reject
from datawarden.principal.policy import Level

#: Cómo se lee cada posición en el mensaje. En vocabulario de SQL y no de sqlglot:
#: quien lee el rechazo escribió SQL, no un árbol.
_POSITION_TEXT: dict[Position, str] = {
    Position.PROJECTION: "in the SELECT list",
    Position.WHERE: "in a WHERE predicate",
    Position.JOIN_ON: "in a JOIN condition",
    Position.GROUP_BY: "in a GROUP BY",
    Position.ORDER_BY: "in an ORDER BY",
    Position.HAVING: "in a HAVING clause",
    Position.QUALIFY: "in a QUALIFY clause",
    Position.FUNCTION_ARGUMENT: "as a function argument",
    Position.WINDOW_PARTITION: "as a window PARTITION BY key",
    Position.SUBQUERY: "inside a subquery",
    Position.CTE: "inside a CTE",
    Position.STATEMENT: "in the statement",
    Position.UNKNOWN: "somewhere the guard could not place",
}


class ColumnPolicyRule:
    """Toda columna, en la posición donde está, es legal para este rol."""

    rule_id = "R008"
    code = "column_policy"
    severity = Severity.POLICY
    summary = "Rol, columna y posición en el árbol, resueltos por linaje"
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
                    # FAIL-CLOSED. Si el guard no puede seguir de dónde sale una
                    # columna, no puede afirmar que sea segura, y lo que no se puede
                    # afirmar no se acepta. Un linaje que falla en silencio es una fuga.
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
    """Cuando no se puede saber de dónde sale una columna."""
    return reject(
        rule,
        message=(
            f"the guard cannot resolve where column {name} comes from, so it cannot "
            "tell whether the access policy applies to it"
        ),
        suggestion=(
            "reference the column from its table directly instead of through the "
            "construct that hides it. A column whose origin cannot be followed is "
            "refused, not assumed safe"
        ),
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
    """El rechazo, con el detalle que lo hace accionable en vez de mudo."""
    alternative = ctx.policy.generalized_for(base)
    written = f"{written_table}.{written_name}" if written_table else written_name
    derived = base != written
    where = _POSITION_TEXT.get(position, "in the query")

    if level is Level.DENY:
        message = (
            f"column {base} is denied for role {ctx.principal.role.value} and appears {where}"
        )
    else:
        message = (
            f"column {base} is masked for role {ctx.principal.role.value}: it may only "
            f"appear as a direct projection, and it appears {where}"
        )
    if derived:
        message += f", reached through {written}"

    if alternative is not None:
        suggestion = (
            f"use {alternative} instead: the policy publishes it as the generalised "
            f"answer to the same question, and it is visible to your role"
        )
    elif level is Level.MASK:
        suggestion = (
            "move the column to the SELECT list, where it is returned masked. What is "
            "not allowed is filtering, grouping or ordering by it — that would let the "
            "value be reconstructed one question at a time"
        )
    else:
        suggestion = (
            "this column is not available to your role in any position. Ask the "
            "question in terms of the aggregates and generalised columns the catalog "
            "publishes"
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
