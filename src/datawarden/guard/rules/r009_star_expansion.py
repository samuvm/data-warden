"""R009 · `SELECT *` no sobrevive al guard. Invariante I-10.

`qualify()` con el esquema del catálogo expande la estrella por sí solo: eso es lo
que hace que esta regla sea, casi siempre, una comprobación de que el trabajo se
hizo. Y esa comprobación es la que importa, porque si la estrella sobreviviera, el
guard estaría razonando sobre una lista de columnas que no ha visto.

**Por qué no basta con confiar en `qualify()`.** Porque hay un caso en que no
expande: cuando no sabe de qué relación tirar —una subconsulta sin esquema, una
función de tabla, un `UNION` mal formado—. En esos casos devuelve el árbol con la
estrella dentro y sin error. Que R008 mirase después ese árbol sería revisar la
política sobre cero columnas y darla por buena: **el `SELECT *` es exactamente cómo
una columna sensible entra en una respuesta sin que ninguna regla la nombre.**
"""

from __future__ import annotations

from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.rule import PASS, POST_QUALIFY, GuardContext, RuleResult, reject
from datawarden.guard.rules import messages


class StarExpansionRule:
    """Ninguna estrella queda en el árbol validado."""

    rule_id = "R009"
    code = "star_not_expanded"
    severity = Severity.SECURITY
    summary = "`SELECT *` se expande contra el catálogo o se rechaza"
    families: tuple[str, ...] = ("select_estrella",)
    phase = POST_QUALIFY

    def check(self, ctx: GuardContext) -> RuleResult:
        star = next(
            (
                node
                for node in ctx.tree.find_all(exp.Star)
                # `COUNT(*)` NO es una estrella sin expandir: es un agregado que
                # cuenta filas y no nombra ninguna columna. Rechazarlo aquí sería
                # rechazar la mitad de las consultas legítimas del almacén, y por el
                # motivo equivocado, que es peor.
                if not isinstance(node.parent, exp.Func)
            ),
            None,
        )
        if star is None:
            return PASS
        parent = star.parent
        where = parent.__class__.__name__ if parent is not None else "the query"
        message, suggestion = messages.star_survived(where)
        return reject(
            self,
            message=message,
            suggestion=suggestion,
            position=Position.PROJECTION,
            subject="*",
            retryable=True,
        )


RULE = StarExpansionRule()
