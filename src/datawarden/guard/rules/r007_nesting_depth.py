"""R007 · profundidad máxima de anidamiento.

`PROJECT.md` la pide en la lista del anillo 3. Su valor no es tanto parar un ataque
—R013 ya acota el tamaño total— como parar una CLASE de consulta cuyo coste no se
puede estimar: cada nivel de subconsulta correlacionada multiplica el trabajo del
motor por el número de filas del nivel de arriba, y el estimador de la fase 3 mira
bytes escaneados, que en una correlacionada no capturan nada de eso.

**Ocho niveles no es una cifra redonda elegida al azar.** Las quince preguntas del
banco con «ventana o subconsulta correlacionada» que exige `docs/PLAN.md` llegan
como mucho a cuatro: dos de CTE, una de subconsulta y una de ventana. Ocho deja el
doble de margen y sigue acotando el peor caso.
"""

from __future__ import annotations

from typing import Final

from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.rule import PASS, POST_QUALIFY, GuardContext, RuleResult, reject
from datawarden.guard.rules import messages

MAX_DEPTH: Final = 8

#: Lo que cuenta como un nivel. Un `Paren` no lo es: agrupar una expresión
#: aritmética no anida nada, y contarlo haría que `((a+b)*c)` gastara niveles.
NESTING_NODES: Final = (exp.Select, exp.Subquery, exp.CTE)


class NestingDepthRule:
    """El anidamiento de consultas no pasa de `MAX_DEPTH`."""

    rule_id = "R007"
    code = "nesting_too_deep"
    severity = Severity.POLICY
    summary = "La profundidad de subconsultas y CTE no supera el máximo"
    families: tuple[str, ...] = ("anidamiento_excesivo",)
    phase = POST_QUALIFY

    def check(self, ctx: GuardContext) -> RuleResult:
        deepest = 0
        for node in ctx.tree.walk():
            if not isinstance(node, NESTING_NODES):
                continue
            depth = sum(
                1 for ancestor in _ancestors(node) if isinstance(ancestor, NESTING_NODES)
            )
            deepest = max(deepest, depth)
        if deepest > MAX_DEPTH:
            message, suggestion = messages.nesting_too_deep(deepest, MAX_DEPTH)
            return reject(
                self,
                message=message,
                suggestion=suggestion,
                position=Position.SUBQUERY,
                subject=f"depth {deepest}",
                retryable=True,
            )
        return PASS


def _ancestors(node: exp.Expression) -> list[exp.Expression]:
    """El nodo y toda su ascendencia, de dentro afuera.

    Se devuelve una lista en vez de subir con un bucle sobre `.parent` porque
    sqlglot tipa `.parent` con un self-type y el verificador estricto no deja
    reasignarlo a una variable de tipo `Expression`. Recorrerlo aquí, una vez,
    evita repetir el mismo apaño en cada regla que necesite ascendencia.
    """
    chain: list[exp.Expression] = []
    current: exp.Expression | None = node
    while current is not None:
        chain.append(current)
        parent = current.parent
        current = parent if isinstance(parent, exp.Expression) else None
    return chain


RULE = NestingDepthRule()
