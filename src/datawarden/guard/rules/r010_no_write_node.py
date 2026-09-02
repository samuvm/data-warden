"""R010 · ninguna escritura, en ninguna parte del árbol.

**Corre antes que R001 a propósito.** R001 mira qué clase de sentencia es la de
arriba; esta mira TODO el árbol, y por eso es la que atrapa el ataque real:

    WITH x AS (DELETE FROM dim_customer RETURNING 1) SELECT * FROM x

Eso es un `Select` en la raíz. Un validador que se conforme con mirar el nodo de
arriba lo acepta, y el motor borra la tabla. La CTE que esconde una escritura es el
caso que `PROJECT.md` nombra en su cuaderno de evasiones, y es la razón de que estas
dos reglas estén separadas: si fueran una, el mensaje diría «solo se admite SELECT»
sobre una consulta que empieza por SELECT, y nadie entendería el rechazo.

**No es reintentable.** Reformular un `DELETE` no lo convierte en una pregunta:
reintentarlo solo gasta tokens y contamina la métrica de recuperación de la fase 6.
"""

from __future__ import annotations

from typing import Final

from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.rule import PASS, PRE_QUALIFY, GuardContext, RuleResult, reject
from datawarden.guard.rules import messages

#: Escrituras de DATOS, y solo eso. `PRAGMA`, `SET`, `ATTACH` o `INSTALL` cambian
#: el estado del MOTOR y no los datos: los para R001 en la raíz —que es donde
#: sintácticamente pueden estar— y R002 en cualquier otro sitio. La separación no es
#: taxonómica: un `DELETE` merece el mensaje «este sistema es de solo lectura», y un
#: `PRAGMA` merece «esto no es una pregunta sobre datos». Meterlos en la misma regla
#: daba el mensaje equivocado a la mitad de los casos.
#:
#: Se nombra por CLASE de nodo y no por palabra clave:
#: `DeLeTe`, `/*x*/DELETE` y `delete` son el mismo nodo, y no hay nada que
#: normalizar. Es la diferencia entre esta regla y una lista negra de palabras.
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
    """Cero nodos de escritura en cualquier profundidad del árbol."""

    rule_id = "R010"
    code = "write_node_present"
    severity = Severity.SECURITY
    summary = "Ningún nodo de escritura aparece en el árbol, a ninguna profundidad"
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
                # LA DECISION: dónde estaba el nodo de escritura. Se calcula mirando
                # la ascendencia en el ÁRBOL y se devuelve como `Position`, que es lo
                # que el corpus asierta por su valor. Antes se derivaba de una cadena
                # de prosa —`Position.CTE if "CTE" in where`— y eso era decisión
                # disfrazada de texto: cambiar la frase cambiaba la posición.
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
