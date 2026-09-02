"""R002 · el guard es una ALLOWLIST. Nodo desconocido, rechazo.

Es la regla que sostiene a las otras trece. Una denylist de construcciones
peligrosas se queda corta el día que DuckDB añade una extensión, **sin que nadie
toque una línea de este repositorio**; una allowlist se queda corta de más, y esa es
la clase de fallo con la que se puede vivir: alguien lee el rechazo, y la
construcción se añade como una decisión con su commit y su caso.

Y se compara por CLASE DE NODO, no por texto. `TABLESAMPLE`, `tablesample` y
`/*x*/TABLESAMPLE` son el mismo `exp.TableSample` después de parsear, así que no hay
mayúsculas raras que normalizar ni comentarios entre tokens que esquivar. Ahí está
la diferencia entera entre validar un árbol y buscar palabras.
"""

from __future__ import annotations

from datawarden.domain.types import Position, Severity
from datawarden.guard.allowlist import ALLOWED_NODES
from datawarden.guard.rule import PASS, PRE_QUALIFY, GuardContext, RuleResult, reject
from datawarden.guard.rules import messages


class NodeAllowlistRule:
    """Todo nodo del árbol está en la allowlist."""

    rule_id = "R002"
    code = "node_not_allowed"
    severity = Severity.SECURITY
    summary = "Todo tipo de nodo del árbol está en la allowlist"
    families: tuple[str, ...] = ("nodo_desconocido", "construccion_no_analitica")
    phase = PRE_QUALIFY

    def check(self, ctx: GuardContext) -> RuleResult:
        for node in ctx.tree.walk():
            name = node.__class__.__name__
            if name in ALLOWED_NODES or name == "Anonymous":
                # `Anonymous` tiene su propia regla y su propio mensaje: R003. Que
                # cayera aquí sería un rechazo por la regla equivocada, y eso es un
                # acierto por casualidad.
                continue
            message, suggestion = messages.node_not_allowed(name)
            return reject(
                self,
                message=message,
                suggestion=suggestion,
                position=Position.STATEMENT,
                subject=name,
                retryable=True,
            )
        return PASS


RULE = NodeAllowlistRule()
