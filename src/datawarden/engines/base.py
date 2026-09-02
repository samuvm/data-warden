"""El puerto del motor. **Acepta un `ValidatedQuery`, jamás un `str`** (I-02).

Es el invariante I-01 de `docs/RULES.md` y lo comprueba `scripts/check_no_raw_sql.py`
sobre el AST de este paquete. La diferencia no es estilística: si el motor aceptara
texto, existiría un camino por el que una consulta llega a la base de datos sin
haber pasado por el guard, y entonces los cinco anillos serían decorativos.

**El contador de ejecuciones es lo que hace comprobable `G-BUDGET-ESCAPE`.** La
meta dice «ninguna consulta por encima del presupuesto llega al motor», y eso no se
puede probar mirando el veredicto: hay que mirar si el motor se movió. Con un
contador de proceso, la propiedad es exacta —`delta == 0` cuando el coste estimado
supera el presupuesto— en vez de una promesa sobre el flujo de control.

Régimen de este paquete: **contrato y snapshot, NO cobertura de línea**
(`docs/RULES.md §2`). Perseguir el 100 % en un adaptador premia envolverlo en tests
que no prueban nada.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from datawarden.domain.types import ResultSet, ValidatedQuery

#: Ejecuciones que han llegado al motor en este proceso. Lo lee la propiedad de
#: `G-BUDGET-ESCAPE`; lo incrementa **toda** implementación de `Engine`, en su
#: primera línea y antes de cualquier trabajo.
_EXECUTIONS = 0


def executions() -> int:
    """Cuántas veces ha entrado algo en un motor en este proceso."""
    return _EXECUTIONS


def count_execution() -> None:
    """Lo llama cada motor al entrar. No hay motor exento: ese es el punto."""
    global _EXECUTIONS
    _EXECUTIONS += 1


@runtime_checkable
class Engine(Protocol):
    """Lo que un motor tiene que saber hacer. Nada más, y con este tipo de entrada."""

    name: str

    def execute(self, query: ValidatedQuery) -> ResultSet:
        """Ejecuta el ÁRBOL re-serializado. Nunca una cadena."""
        ...


class RecordingEngine:
    """Un motor de mentira que no ejecuta nada y se acuerda de todo.

    Existe para la propiedad de `G-BUDGET-ESCAPE` y para la de I-12: con un motor
    real no se puede afirmar «esta consulta NO llegó», solo «no dio resultados», que
    son cosas distintas. Y devuelve 10.000 filas a propósito, para comprobar que el
    recorte a `max_rows` lo hace el DOMINIO y no el motor.
    """

    name = "recording"

    def __init__(self, rows: int = 0) -> None:
        self.seen: list[str] = []
        self._rows = rows

    def execute(self, query: ValidatedQuery) -> ResultSet:
        count_execution()
        self.seen.append(query.sql())
        columns = query.columns or ("x",)
        return ResultSet(
            columns=columns,
            rows=tuple(tuple(i for _ in columns) for i in range(self._rows)),
            truncated=False,
        )
