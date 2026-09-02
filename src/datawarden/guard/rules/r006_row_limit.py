"""R006 · el `LIMIT` es del dominio, y si falta se inyecta.

`PROJECT.md` lo pide así —«`LIMIT` obligatorio; si falta, se inyecta»— y la
invariante I-12 dice por qué tiene que ponerlo el dominio y **nunca el motor**: si
cada engine aplicara el suyo, DuckDB y Athena devolverían resultados distintos para
el mismo rol, y el criterio de aceptación nº 5 de `PROJECT.md` fallaría por el
límite y no por la abstracción.

**Es la única regla del guard que reescribe casi siempre**, y por eso corre la
última: el `LIMIT` que inyecta se calcula sobre `max_rows` del rol, que es un dato
del presupuesto, y recortar antes habría hecho que las demás reglas razonaran sobre
un árbol que no es el que escribió quien preguntó.

Tres cosas SÍ se rechazan, y las tres por el mismo motivo: **no se puede saber
cuántas filas van a salir antes de ejecutar.**

- Un `LIMIT` que no es un literal —`LIMIT (SELECT count(*) ...)`— convierte el tope
  en algo que depende de los datos.
- Un `LIMIT` negativo no significa nada y cada motor hace una cosa distinta.
- Un `OFFSET` enorme obliga al motor a producir y tirar millones de filas: el coste
  es de escaneo completo aunque el `LIMIT` diga diez.
"""

from __future__ import annotations

from typing import Final

from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.rule import PASS, POST_QUALIFY, GuardContext, RuleResult, reject
from datawarden.guard.rules import messages

#: Desplazamiento máximo. Un `OFFSET 1000000` recorre un millón de filas para
#: tirarlas: el coste es el mismo que devolverlas y no aparece en el `LIMIT`.
MAX_OFFSET: Final = 100_000


class RowLimitRule:
    """Hay `LIMIT`, es un literal sano, y no pasa de `max_rows`."""

    rule_id = "R006"
    code = "row_limit"
    severity = Severity.POLICY
    summary = "El tope de filas lo pone el dominio; se inyecta si falta y se recorta"
    families: tuple[str, ...] = ("sin_limite", "limite_no_estatico", "desplazamiento_excesivo")
    phase = POST_QUALIFY

    def check(self, ctx: GuardContext) -> RuleResult:
        tree = ctx.tree

        # TODA subconsulta, no solo la raíz. LO ENCONTRÓ LA MUTACIÓN DE AST: envolver
        # la consulta en una CTE o en una subconsulta escondía el `OFFSET` del nodo de
        # arriba, y `SELECT * FROM (SELECT ... OFFSET 9000000) s` hacía que el motor
        # produjera y tirara nueve millones de filas sin que ninguna regla lo viera.
        # Es exactamente para esto que existe el corpus de mutantes: la regla estaba
        # bien escrita para el caso que alguien pensó, y mal para el que no.
        for select in tree.find_all(exp.Select, exp.Union, exp.Except, exp.Intersect):
            rejection = self._check_bounds(select)
            if rejection is not None:
                return rejection
        rejection = self._check_bounds(tree)
        if rejection is not None:
            return rejection

        limit = tree.args.get("limit") if hasattr(tree, "args") else None
        if limit is None:
            rewritten = tree.copy()
            rewritten.set("limit", exp.Limit(expression=exp.Literal.number(ctx.max_rows)))
            return RuleResult(
                tree=rewritten,
                notes=(f"R006 injected LIMIT {ctx.max_rows}",),
            )

        value = _int_literal(limit.expression)
        if value is not None and value > ctx.max_rows:
            rewritten = tree.copy()
            rewritten.set("limit", exp.Limit(expression=exp.Literal.number(ctx.max_rows)))
            return RuleResult(
                tree=rewritten,
                notes=(f"R006 clamped LIMIT {value} to {ctx.max_rows}",),
            )
        return PASS

    def _check_bounds(self, node: exp.Expression | exp.Query) -> RuleResult | None:
        """`LIMIT` y `OFFSET` sanos en ESTE nodo, esté donde esté en el árbol."""
        if not hasattr(node, "args"):
            return None

        offset = node.args.get("offset")
        if offset is not None:
            value = _int_literal(offset.expression)
            if value is None:
                return self._not_static(offset.expression, "OFFSET")
            if value < 0 or value > MAX_OFFSET:
                message, suggestion = messages.offset_too_large(value, MAX_OFFSET)
                return reject(
                    self,
                    message=message,
                    suggestion=suggestion,
                    position=Position.STATEMENT,
                    subject=f"OFFSET {value}",
                    retryable=True,
                )

        limit = node.args.get("limit")
        if limit is None:
            return None
        value = _int_literal(limit.expression)
        if value is None:
            return self._not_static(limit.expression, "LIMIT")
        if value <= 0:
            message, suggestion = messages.limit_asks_for_nothing(value)
            return reject(
                self,
                message=message,
                suggestion=suggestion,
                position=Position.STATEMENT,
                subject=f"LIMIT {value}",
                retryable=True,
            )
        return None

    def _not_static(self, node: exp.Expression | None, clause: str) -> RuleResult:
        rendered = node.__class__.__name__ if node is not None else "nothing"
        message, suggestion = messages.limit_not_literal(clause, rendered)
        return reject(
            self,
            message=message,
            suggestion=suggestion,
            position=Position.STATEMENT,
            subject=clause,
            retryable=True,
        )


def _int_literal(node: exp.Expression | None) -> int | None:
    if isinstance(node, exp.Literal) and node.is_int:
        return int(node.this)
    if isinstance(node, exp.Neg):
        inner = _int_literal(node.this)
        return None if inner is None else -inner
    return None


RULE = RowLimitRule()
