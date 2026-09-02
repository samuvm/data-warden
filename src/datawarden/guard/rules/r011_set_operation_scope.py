"""R011 · las ramas de un UNION son consultas, y no son muchas.

**El ataque que esta regla para** está en el cuaderno de evasiones de `PROJECT.md`:
«`UNION` contra una tabla fuera de ámbito». R004 ya recorre TODO el árbol, así que
la tabla fuera de ámbito la caza igual; lo que R011 añade es lo que R004 no ve:

1. **Que cada rama sea una consulta**, y no un `VALUES` con datos inyectados. Un
   `SELECT a FROM t UNION ALL VALUES ('...')` mete filas que no salen del almacén y
   que luego aparecen en la respuesta como si fueran datos: es inyección de
   contenido con forma de consulta legítima.
2. **Que las ramas sean pocas.** Cien ramas unidas son cien escaneos, y el
   estimador de coste de la fase 3 suma por rama: el número crece rápido y sin que
   ninguna rama parezca cara por separado.
"""

from __future__ import annotations

from typing import Final

from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.rule import PASS, POST_QUALIFY, GuardContext, RuleResult, reject

MAX_BRANCHES: Final = 10

SET_OPERATIONS: Final = (exp.Union, exp.Except, exp.Intersect)


class SetOperationScopeRule:
    """Toda rama de una operación de conjunto es una consulta del catálogo."""

    rule_id = "R011"
    code = "set_operation_branch"
    severity = Severity.SECURITY
    summary = "Las ramas de UNION/EXCEPT/INTERSECT son consultas, y son pocas"
    families: tuple[str, ...] = ("union_con_datos_inyectados", "demasiadas_ramas")
    phase = POST_QUALIFY

    def check(self, ctx: GuardContext) -> RuleResult:
        branches = 0
        for node in ctx.tree.walk():
            if not isinstance(node, SET_OPERATIONS):
                continue
            branches += 1
            for side in (node.this, node.expression):
                if isinstance(side, exp.Values) or (
                    side is not None and side.find(exp.Values) is not None
                ):
                    return reject(
                        self,
                        message=(
                            "one branch of the set operation is a VALUES list, so it "
                            "would add rows that do not come from the warehouse"
                        ),
                        suggestion=(
                            "every branch has to read from a catalog relation. If you "
                            "need a constant, put it in the projection of a branch "
                            "that reads real rows"
                        ),
                        position=Position.SUBQUERY,
                        subject="VALUES",
                        retryable=False,
                    )
        if branches >= MAX_BRANCHES:
            return reject(
                self,
                message=(
                    f"the query chains {branches + 1} set-operation branches and the "
                    f"limit is {MAX_BRANCHES}; each branch is another scan"
                ),
                suggestion=(
                    "use a single query with an IN or a JOIN against a dimension "
                    "instead of unioning one branch per value"
                ),
                position=Position.SUBQUERY,
                subject=f"{branches + 1} branches",
                retryable=True,
            )
        return PASS


RULE = SetOperationScopeRule()
