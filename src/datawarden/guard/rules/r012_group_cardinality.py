"""R012 · la agregación de grupo único, que es el ataque que casi nadie contempla.

`docs/RULES.md §7`, error 7: *«Olvidar la agregación de grupo único.
`SELECT count(*) … GROUP BY national_id` expone la columna por cardinalidad»*. Y hay
una versión más limpia todavía, que no necesita ninguna columna sensible:

    SELECT country_code, count(*) FROM dim_customer GROUP BY country_code
    HAVING count(*) = 1

Eso devuelve, país a país, las cohortes de una sola persona. Ninguna columna sale en
la respuesta y sin embargo la respuesta ES una persona. **Pedir explícitamente los
grupos de tamaño uno es pedir individuos con forma de agregado.**

Esta regla cierra las dos formas estructurales de pedirlo:

1. Un `HAVING` que compara un conteo con un literal por debajo de `K_MIN`.
2. Agrupar —o particionar una ventana— por una columna que la política clasifica
   como IDENTIFICADOR o CUASI-IDENTIFICADOR, aunque para ese rol sea `allow`.

**Límite declarado, y es el mismo que declara la política.** El k-anonimato de
verdad exige contar antes de devolver, lo que cambia el coste de toda consulta
agrupada; `docs/spec/policy.yaml` ya lo publica como fuera de alcance. Esto para las
dos formas ESTRUCTURALES de pedir grupos pequeños, no todas: una combinación de
columnas todas en `allow` puede seguir aislando a una persona, y eso está escrito en
`limites_declarados` en vez de fingido.
"""

from __future__ import annotations

from typing import Final

from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.position import table_and_column
from datawarden.guard.rule import PASS, POST_QUALIFY, GuardContext, RuleResult, reject

#: Tamaño mínimo de grupo que se puede pedir explícitamente. Cinco es el suelo
#: habitual en estadística oficial para publicar una celda, y aquí se aplica al
#: PREDICADO —lo que se pide—, no al resultado, que es lo que no se puede acotar
#: sin contar antes de devolver.
K_MIN: Final = 5

#: Tipos de dato de la política que identifican a una persona o casi. Salen del
#: contrato firmado, no de una lista escrita aquí: si negocio reclasifica una
#: columna, esta regla cambia con ella y sin tocar código.
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
    """Ni se piden grupos de tamaño uno, ni se agrupa por un identificador."""

    rule_id = "R012"
    code = "group_too_small"
    severity = Severity.POLICY
    summary = "No se piden grupos por debajo de k, ni se agrupa por identificadores"
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
        for having in ctx.tree.find_all(exp.Having):
            for comparison in having.find_all(*_SMALL_COMPARISONS):
                left, right = comparison.this, comparison.expression
                for count_side, literal_side in ((left, right), (right, left)):
                    if not isinstance(count_side, _COUNT_NODES):
                        continue
                    value = _int_literal(literal_side)
                    if value is None or value >= K_MIN:
                        continue
                    return reject(
                        self,
                        message=(
                            f"the HAVING clause asks for groups whose count is "
                            f"{value} or fewer, and the minimum group size is "
                            f"{K_MIN}; groups that small are individuals with the "
                            "shape of an aggregate"
                        ),
                        suggestion=(
                            f"ask for groups of at least {K_MIN} rows, or drop the "
                            "HAVING and read the distribution of group sizes instead"
                        ),
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
                return reject(
                    self,
                    message=(
                        f"grouping by {base} makes each group one person: it is a "
                        f"{data_type} and every distinct value identifies an individual"
                    ),
                    suggestion=(
                        f"group by {alternative} instead, which is the generalised "
                        "column the policy publishes"
                        if alternative
                        else "group by a category, a period or a region, so that each "
                        "group covers many rows"
                    ),
                    position=position,
                    subject=base,
                    alternative=alternative,
                    retryable=True,
                )
        return None


def _int_literal(node: exp.Expression | None) -> int | None:
    """El entero de un literal, o `None` si no es un entero literal."""
    if isinstance(node, exp.Literal) and node.is_int:
        return int(node.this)
    if isinstance(node, exp.Neg):
        inner = _int_literal(node.this)
        return None if inner is None else -inner
    return None


RULE = GroupCardinalityRule()
