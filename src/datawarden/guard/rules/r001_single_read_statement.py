"""R001 · una sola sentencia, y de lectura.

Dos comprobaciones que parecen una:

**Una sola.** `sqlglot.parse` devuelve una lista. `parse_one` habría devuelto la
primera y descartado el resto EN SILENCIO, que es exactamente cómo
`SELECT 1; DROP TABLE dim_customer` pasa por un validador escrito con prisa: la
validación mira el `SELECT` y el motor ejecuta las dos. El validador de este
proyecto llama a `parse`, no a `parse_one`, y esta regla es donde eso se comprueba.

**Y de lectura.** El nodo de arriba tiene que ser una consulta. `PRAGMA`, `SET`,
`EXPLAIN`, `INSTALL`, `ATTACH` y compañía no escriben datos —de eso se ocupa R010—
pero tampoco preguntan nada: cambian el estado del motor, cargan extensiones o
revelan su configuración. Un almacén que se consulta en lenguaje natural no
necesita ninguna de las tres cosas.
"""

from __future__ import annotations

from typing import Final

from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.rule import PASS, PRE_QUALIFY, GuardContext, RuleResult, reject
from datawarden.guard.rules import messages

#: Lo único que puede estar en la raíz. `Subquery` entra porque `(SELECT 1)` con
#: paréntesis es una consulta legítima y no un caso raro.
READ_ROOTS: Final = (exp.Select, exp.Union, exp.Except, exp.Intersect, exp.Subquery)

#: Operaciones de conjunto, cuyas RAMAS también tienen que ser consultas.
SET_OPERATIONS: Final = (exp.Union, exp.Except, exp.Intersect)


class SingleReadStatementRule:
    """La raíz del árbol es una consulta, y solo hay una."""

    rule_id = "R001"
    code = "not_a_read_statement"
    severity = Severity.SECURITY
    summary = "Una única sentencia, y su raíz es una consulta de lectura"
    families: tuple[str, ...] = (
        "sentencia_no_consulta",
        "sentencia_multiple",
        "comando_de_motor",
    )
    phase = PRE_QUALIFY

    def check(self, ctx: GuardContext) -> RuleResult:
        root = ctx.tree
        if isinstance(root, READ_ROOTS):
            return self._check_branches(ctx) or PASS
        kind = root.__class__.__name__
        message, suggestion = messages.not_a_query(kind)
        return reject(
            self,
            message=message,
            suggestion=suggestion,
            position=Position.STATEMENT,
            subject=kind,
            retryable=False,
        )

    def _check_branches(self, ctx: GuardContext) -> RuleResult | None:
        """Cada rama de un UNION es, también ella, una consulta.

        **ESTO NO ES PEDANTERÍA SINTÁCTICA: cierra un SIGSEGV.** Lo encontró la
        propiedad de fail-closed con 5.000 entradas casi-SQL. `x UNION SELECT 1`
        parsea sin error —la raíz es un `Union` perfectamente legal— y al llegar a
        `qualify()` **mata el proceso**: el `sqlglot[c]` compilado con mypyc revienta
        con una rama izquierda que no es una consulta.

        Y un segfault es la peor forma posible de romper el fail-closed, porque **no
        lo atrapa ningún `except`**: el guard no rechaza, el proceso muere. El único
        sitio donde se puede parar es AQUÍ, antes de cualificar, comprobando lo que
        de todos modos es verdad: una rama de un UNION tiene que ser una consulta.

        El límite honesto va al modelo de amenaza: una caída nativa dentro de una
        extensión compilada no se puede capturar en proceso, así que la defensa es
        no llegar a ella y, en un servidor de verdad, aislar el proceso.
        """
        for node in ctx.tree.walk():
            if not isinstance(node, SET_OPERATIONS):
                continue
            for side in (node.this, node.expression):
                if side is None or isinstance(side, READ_ROOTS):
                    continue
                kind = side.__class__.__name__
                message, suggestion = messages.branch_not_a_query(kind)
                return reject(
                    self,
                    message=message,
                    suggestion=suggestion,
                    position=Position.STATEMENT,
                    subject=kind,
                    retryable=True,
                )
        return None


RULE = SingleReadStatementRule()
