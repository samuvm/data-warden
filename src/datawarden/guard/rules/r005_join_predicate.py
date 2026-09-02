"""R005 · todo JOIN lleva condición. Nada de productos cartesianos.

`PROJECT.md` lo pide en su lista del anillo 3, y el motivo es de coste antes que de
seguridad: `fact_payment_attempt` tiene 66,6 M de filas y `fact_order_line` 146,8 M.
Un producto cartesiano entre las dos son **9,8 x 10^15 filas**, y ninguna estimación
de coste sensata evita que la consulta llegue al motor si el guard la deja pasar:
la estimación previa mira bytes escaneados, y el escaneo de un cartesiano es
pequeño; lo que explota es la materialización.

Se cubren las dos formas de escribirlo, y la segunda es la que se olvida:

    FROM a CROSS JOIN b        -- evidente
    FROM a, b                  -- un JOIN sin ON, en la coma

Un `CROSS JOIN` contra una tabla de referencia de catorce filas es legítimo y
frecuente, así que la regla no prohíbe el cartesiano: prohíbe el cartesiano cuyas
dos ramas son grandes. Prohibirlo entero rompería consultas normales, y una regla
que rompe el trabajo normal se desactiva en tres semanas.
"""

from __future__ import annotations

from typing import Final

from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.rule import PASS, POST_QUALIFY, GuardContext, RuleResult, reject

#: Tablas por debajo de las cuales un cartesiano no es un problema. Son las de
#: referencia del almacén: divisas, países, motivos de rechazo. Multiplicar por
#: catorce filas es una operación normal; multiplicar por 66 millones no lo es.
SMALL_RELATION_PREFIXES: Final = ("ref_", "dim_date")


class JoinPredicateRule:
    """Ningún JOIN entre relaciones grandes se queda sin condición."""

    rule_id = "R005"
    code = "cartesian_join"
    severity = Severity.POLICY
    summary = "Todo JOIN entre relaciones grandes lleva condición"
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
                left = _relation_name((select.args.get("from") or exp.From()).this)
                if _is_small(right) or _is_small(left):
                    continue
                return reject(
                    self,
                    message=(
                        f"the join between {left or 'a subquery'} and "
                        f"{right or 'a subquery'} has no ON or USING condition, so it "
                        "is a cartesian product between two large relations"
                    ),
                    suggestion=(
                        "add the join key. Facts join dimensions through their "
                        "surrogate key (`merchant_sk`, `customer_sk`, `card_sk`); "
                        "the catalog resource lists them"
                    ),
                    position=Position.JOIN_ON,
                    subject=f"{left} x {right}",
                    retryable=True,
                )
        return PASS


def _is_constant(condition: exp.Expression) -> bool:
    """`ON TRUE` es un cartesiano con otro nombre, y lo encontró el corpus.

    Una condición que no menciona NINGUNA columna no relaciona las dos tablas: las
    multiplica. `ON TRUE`, `ON 1 = 1` y `ON 'a' = 'a'` son el mismo producto
    cartesiano escrito de tres maneras, y comprobar la ausencia de columnas los
    cubre los tres sin tener que enumerar formas.
    """
    return condition.find(exp.Column) is None


def _relation_name(node: exp.Expression | None) -> str:
    if isinstance(node, exp.Table):
        return node.name.lower()
    return ""


def _is_small(name: str) -> bool:
    return bool(name) and name.startswith(SMALL_RELATION_PREFIXES)


RULE = JoinPredicateRule()
