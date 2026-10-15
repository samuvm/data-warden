"""Local engine. Executes the re-serialized tree, never the input string.

`execute()` takes a `ValidatedQuery` and calls `query.sql()`, i.e.
`ast.sql(dialect, comments=False)`. If sqlglot misreads a query, DuckDB receives what
sqlglot understood, not what the attacker wrote, which removes parser-differential
attacks by construction.

Tested by contract and snapshot, not line coverage (`docs/RULES.md` section 2).
"""

from __future__ import annotations

import pathlib
import threading
from typing import Any

from datawarden.domain.types import ResultSet, ValidatedQuery
from datawarden.engines.base import count_execution


class DuckDBEngine:
    """Embedded DuckDB, read-only."""

    name = "duckdb"

    def __init__(self, database: pathlib.Path, setup_sql: tuple[str, ...] = ()) -> None:
        self._database = database
        # DDL run once when the connection opens; currently it installs the masking
        # macro (`mask/macro.py`). The engine does not know what it is: knowing would
        # require `engines` to import `mask`, which the layer contract forbids. `audit/`
        # decides what goes here.
        #
        # These strings are not audited and must not be logged: they install a key
        # and contain the pepper.
        self._setup_sql = setup_sql
        self._connection: Any = None
        # One lock and one connection. The MCP server runs tools in a worker thread,
        # and a DuckDB connection used from two threads at once is undefined behavior.
        #
        # A per-call `.cursor()` would not work: a DuckDB cursor opens a new session,
        # and the `warden_hash` macro installed by `setup_sql` is temporary and
        # session-scoped, so hashed columns would fail or go unmasked.
        self._lock = threading.Lock()

    def _connect(self) -> Any:
        if self._connection is None:
            import duckdb

            self._connection = duckdb.connect(str(self._database), read_only=True)
            for statement in self._setup_sql:
                # Temporary macros live in the session schema and do not touch the
                # file, so the connection stays read-only.
                self._connection.execute(statement)
        return self._connection

    def execute(self, query: ValidatedQuery) -> ResultSet:
        """Execute and truncate to the domain's `max_rows` (I-12).

        `max_rows` travels inside the tree as a `LIMIT` (injected by R006) and is also
        applied here to the returned rows, so the engine never has the last word on
        how many rows a role sees and DuckDB and Athena agree.
        """
        count_execution()
        with self._lock:
            # Connect and fetch under the lock: reading the cursor outside it would
            # touch the connection again.
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
