"""R013 · el árbol tiene un tamaño acotado.

**Corre la PRIMERA de las catorce, y no por importancia sino por aritmética.** Todas
las demás recorren el árbol; si el árbol puede ser arbitrariamente grande, el coste
del guard también lo es, y `G-GUARD-P95` —25 ms de p95, 250 ms de máximo absoluto—
deja de poder cumplirse por construcción. Un guardián que tarda diez segundos en
decidir no es lento: es un guardián caído, y una consulta que lo tumba pasa.

Es la única regla del conjunto que protege al propio guard en vez de a los datos, y
por eso su severidad es `SECURITY`: una bomba de AST es un intento de evasión por
agotamiento, no un error de escritura.
"""

from __future__ import annotations

from typing import Final

from datawarden.domain.types import Position, Severity
from datawarden.guard.rule import PASS, PRE_QUALIFY, GuardContext, RuleResult, reject
from datawarden.guard.rules import messages

#: Nodos máximos. Una consulta legítima de este almacén —cuatro tablas, una ventana
#: y un par de subconsultas— ronda los 200 nodos; las 60 preguntas del banco caben
#: de sobra. 4.000 deja margen de un orden de magnitud y sigue acotando el coste.
MAX_NODES: Final = 4_000

#: Caracteres máximos de la entrada. Se comprueba ANTES de parsear en el validador;
#: aquí se conserva como constante para que el número viva en un solo sitio.
MAX_SQL_CHARS: Final = 40_000


class TreeSizeRule:
    """El árbol cabe en el presupuesto del guard."""

    rule_id = "R013"
    code = "tree_too_large"
    severity = Severity.SECURITY
    summary = "El árbol sintáctico no supera el número máximo de nodos"
    families: tuple[str, ...] = ("bomba_de_ast", "entrada_desmesurada")
    phase = PRE_QUALIFY

    def check(self, ctx: GuardContext) -> RuleResult:
        nodes = sum(1 for _ in ctx.tree.walk())
        if nodes > MAX_NODES:
            message, suggestion = messages.tree_too_large(nodes, MAX_NODES)
            return reject(
                self,
                message=message,
                suggestion=suggestion,
                position=Position.STATEMENT,
                subject=f"{nodes} nodes",
                retryable=True,
            )
        return PASS


RULE = TreeSizeRule()
