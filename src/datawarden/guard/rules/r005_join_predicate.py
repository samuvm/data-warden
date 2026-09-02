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
from datawarden.guard.rules import messages

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
    """`ON TRUE` es un cartesiano con otro nombre, y lo encontró el corpus.

    Una condición que no menciona NINGUNA columna no relaciona las dos tablas: las
    multiplica. `ON TRUE`, `ON 1 = 1` y `ON 'a' = 'a'` son el mismo producto
    cartesiano escrito de tres maneras, y comprobar la ausencia de columnas los
    cubre los tres sin tener que enumerar formas.
    """
    return condition.find(exp.Column) is None


def _from_relation(select: exp.Select) -> exp.Expression | None:
    """La relación del `FROM`, buscada POR CLASE DE NODO y no por clave.

    **Esto era un bug, y lo destapó asertar el VALOR del rechazo en vez de su
    existencia.** El código decía `select.args.get("from")`, y sqlglot 30 renombró
    esa clave a `from_`. La llamada devolvía `None` SIEMPRE, así que `left` era
    siempre la cadena vacía, con dos consecuencias que ningún test veía:

    1. **El mensaje mentía en su mitad izquierda.** `FROM fact_payment_attempt a,
       fact_order_line l` producía «the join between *a subquery* and
       fact_order_line», y no hay ninguna subconsulta. Un rechazo que nombra mal el
       objeto no redirige el trabajo, y `G-RECOVERY` lo habría pagado en la fase 6
       sin que nadie supiera por qué.
    2. **La exención de relación pequeña estaba muerta en un lado.**
       `_is_small(left)` nunca era cierta, así que un `FROM ref_fx_rate_daily JOIN
       fact_payment_attempt` se rechazaba pese a que multiplicar por catorce filas
       es justo lo que `SMALL_RELATION_PREFIXES` declara normal. Un falso positivo,
       o sea la dirección segura, pero un guard que bloquea trabajo legítimo se
       desactiva en tres semanas —lo dice `docs/spec/policy.yaml`—.

    Se busca la instancia de `exp.From` entre los argumentos DIRECTOS del `Select`,
    que es el mismo principio que R010 aplica a los nodos de escritura: la clase de
    nodo es estable, el nombre de la clave es un detalle de la versión. Y son
    argumentos directos, no `find()`, para no bajar a la subconsulta de otro ámbito.
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
