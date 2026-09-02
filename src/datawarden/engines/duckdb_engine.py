"""El motor local. Ejecuta el ÁRBOL re-serializado, jamás la cadena de entrada.

`execute()` recibe un `ValidatedQuery` y llama a `query.sql()`, que es
`ast.sql(dialect, comments=False)`. La consecuencia práctica es la que da sentido a
todo el anillo 3: **si sqlglot entendió mal la consulta, lo que llega a DuckDB es lo
que sqlglot entendió, no lo que el atacante escribió.** Eso elimina por construcción
la clase entera de ataques por diferencia de parser.

Régimen: **contrato y snapshot, no cobertura de línea** (`docs/RULES.md §2`).
"""

from __future__ import annotations

import pathlib
from typing import Any

from datawarden.domain.types import ResultSet, ValidatedQuery
from datawarden.engines.base import count_execution


class DuckDBEngine:
    """DuckDB embebido, en solo lectura."""

    name = "duckdb"

    def __init__(self, database: pathlib.Path) -> None:
        self._database = database
        self._connection: Any = None

    def _connect(self) -> Any:
        if self._connection is None:
            import duckdb

            self._connection = duckdb.connect(str(self._database), read_only=True)
        return self._connection

    def execute(self, query: ValidatedQuery) -> ResultSet:
        """Ejecuta y recorta. **El recorte lo hace el DOMINIO** (I-12).

        `max_rows` viaja dentro del árbol como `LIMIT` —lo inyecta R006— y además se
        aplica aquí sobre las filas devueltas. No es redundancia: es que el motor no
        puede ser la última palabra sobre cuántas filas ve un rol, porque entonces
        DuckDB y Athena darían resultados distintos para la misma pregunta.
        """
        count_execution()
        cursor = self._connect().execute(query.sql())
        columns = tuple(d[0] for d in (cursor.description or []))
        rows = cursor.fetchall()
        truncated = len(rows) > query.max_rows
        return ResultSet(
            columns=columns,
            rows=tuple(tuple(r) for r in rows[: query.max_rows]),
            truncated=truncated,
        )

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
