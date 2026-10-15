"""Audit chain storage: SQLite in WAL mode, append-only enforced by triggers.

Append-only is enforced by the database, not by caller discipline: a check in this
module would be bypassed by the first script that opens the file with `sqlite3`.
Triggers travel with the data, so rewriting a record requires dropping a trigger
first, which is deliberate and leaves a trace.

WAL lets `warden audit verify` read while the writer keeps writing.

Known limit, documented in `docs/threat-model.md`: anyone with write access to the
file can drop the triggers and recompute the whole chain. The hash chain only
detects tampering by someone who cannot rewrite every later record.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Final

from datawarden.audit.chain import GENESIS, AuditRecord, link, verify
from datawarden.domain.types import Role, RoleSource, Status

#: Table columns, in read and write order. Iterating this tuple instead of indexing
#: by literal is required: `scripts/check_role_source.py` fails the gate on any
#: `x["role"]` or `x["principal_id"]` outside `principal/`, since that is how a role
#: leaks in from unauthenticated data (I-05). The role here comes from an already
#: built `Principal`, but the checker is static and rightly does not distinguish.
_COLUMNS: Final = (
    "seq",
    "recorded_at",
    "principal_id",
    "role",
    "role_source",
    "status",
    "question_digest",
    "question_preview",
    "sql_digest",
    "sql",
    "tables",
    "columns_masked",
    "rejection",
    "estimated_bytes",
    "scanned_bytes",
    "budget_bytes",
    "row_count",
    "truncated",
    "duration_ms",
    "engine",
    "model",
    "trace_id",
    "prev_hash",
    "schema_version",
    "chain_hash",
)

#: Fields stored as JSON because SQLite has no lists or objects. Read back through
#: the same tuple so there is a single list.
_JSON_FIELDS: Final = ("tables", "columns_masked", "rejection", "model")

_SCHEMA_SQL: Final = """
CREATE TABLE IF NOT EXISTS audit_log (
    seq              INTEGER PRIMARY KEY,
    recorded_at      TEXT    NOT NULL,
    principal_id     TEXT    NOT NULL,
    role             TEXT    NOT NULL,
    role_source      TEXT    NOT NULL,
    status           TEXT    NOT NULL,
    question_digest  TEXT    NOT NULL,
    question_preview TEXT,
    sql_digest       TEXT    NOT NULL,
    sql              TEXT,
    tables           TEXT    NOT NULL,
    columns_masked   TEXT    NOT NULL,
    rejection        TEXT,
    estimated_bytes  INTEGER,
    scanned_bytes    INTEGER,
    budget_bytes     INTEGER,
    row_count        INTEGER,
    truncated        INTEGER,
    duration_ms      REAL,
    engine           TEXT,
    model            TEXT,
    trace_id         TEXT,
    prev_hash        TEXT    NOT NULL,
    -- Contract version this row was written with. Stored, not derived: it is part
    -- of the hash, so injecting today's version would break verification of old
    -- rows as soon as the version is bumped.
    schema_version   INTEGER NOT NULL,
    chain_hash       TEXT    NOT NULL
);

-- Append-only, enforced by the engine. The message states the reason so nobody
-- mistakes it for a misconfigured permission.
CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: a written record cannot be updated');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: a written record cannot be deleted');
END;
"""


@dataclass(frozen=True, slots=True)
class Entry:
    """A written record, with its assigned `seq` and its hash."""

    record: AuditRecord
    chain_hash: str

    @property
    def seq(self) -> int:
        return self.record.seq


def _utc_now() -> str:
    """Contract timestamp: UTC with an explicit `Z`.

    `isoformat()` yields `+00:00`, which does not match the schema's `pattern: "Z$"`,
    and `utcnow()` is deprecated (the suite runs with `filterwarnings = ["error"]`).
    Microseconds are kept because same-millisecond records are normal under load;
    ordering comes from `seq` regardless.
    """
    return dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


class AuditStore:
    """The persisted chain. Use `":memory:"` in unit tests."""

    def __init__(self, path: str) -> None:
        # The lock is required by the chain itself, not just by threading: `append()`
        # reads the head (`seq`, `chain_hash`) and then writes linked to it. Two
        # concurrent writers would read the same `prev_hash` and fork the chain,
        # which `verify()` would only report afterwards.
        #
        # `check_same_thread=False` is needed because the MCP SDK runs synchronous
        # tools on a worker thread, and SQLite otherwise refuses objects created on
        # another thread. This only shows up with a real MCP client, not in-process.
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        # WAL does not apply to `:memory:` and SQLite silently ignores it; it is
        # requested anyway so tests and production run the same code path.
        if path != ":memory:":
            self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(_SCHEMA_SQL)

    # ----------------------------------------------------------------- write ---

    def append(
        self,
        *,
        principal_id: str,
        role: Role,
        role_source: RoleSource,
        status: Status,
        question_digest: str,
        sql_digest: str,
        **optional: Any,
    ) -> Entry:
        """Append a record to the end of the chain and return what was written.

        `seq` is assigned by the store, never by the caller. An external `seq` can
        repeat or skip, and a gap is indistinguishable from a deleted record, which
        would defeat the consecutiveness check in `verify()`.
        """
        with self._lock:
            return self._append_locked(
                principal_id=principal_id,
                role=role,
                role_source=role_source,
                status=status,
                question_digest=question_digest,
                sql_digest=sql_digest,
                **optional,
            )

    def _append_locked(
        self,
        *,
        principal_id: str,
        role: Role,
        role_source: RoleSource,
        status: Status,
        question_digest: str,
        sql_digest: str,
        **optional: Any,
    ) -> Entry:
        """Body of `append`, under the lock: read the head and link to it."""
        row = self.connection.execute(
            "SELECT seq, chain_hash FROM audit_log ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        next_seq = 1 if row is None else int(row[0]) + 1
        previous = GENESIS if row is None else str(row[1])

        duration = optional.pop("duration_ms", None)
        record = AuditRecord(
            seq=next_seq,
            recorded_at=_utc_now(),
            principal_id=principal_id,
            role=role,
            role_source=role_source,
            status=status,
            question_digest=question_digest,
            sql_digest=sql_digest,
            prev_hash=previous,
            # Rounded to microseconds, the precision at which Python's float `repr`
            # and ECMAScript number formatting agree, as JCS requires. See
            # `chain.canonicalize`.
            duration_ms=None if duration is None else round(float(duration), 3),
            **optional,
        )
        chain_hash = link(record)

        payload = record.to_payload()
        values: list[Any] = []
        for name in _COLUMNS:
            if name == "chain_hash":
                values.append(chain_hash)
                continue
            value = payload.get(name)
            if name in _JSON_FIELDS:
                values.append(json.dumps(value if value is not None else [], sort_keys=True))
            else:
                values.append(value)
        # S608: column names come from the module constant `_COLUMNS`, never from
        # input; values are bound as parameters. A single column list for reads and
        # writes prevents drift: a column read at the wrong position would change the
        # JCS and break the chain.
        self.connection.execute(
            f"INSERT INTO audit_log ({','.join(_COLUMNS)}) "  # noqa: S608
            f"VALUES ({','.join('?' * len(_COLUMNS))})",
            values,
        )
        return Entry(record=record, chain_hash=chain_hash)

    # ------------------------------------------------------------------ read ---

    def rows_as_entries(self) -> list[Entry]:
        """The whole chain, ordered by `seq`."""
        # S608: same as `append`; columns come from a constant, never from input.
        with self._lock:
            # `fetchall()` stays inside the lock: consuming the cursor outside would
            # touch the connection again, which is what the lock serializes.
            raws = self.connection.execute(
                f"SELECT {','.join(_COLUMNS)} FROM audit_log ORDER BY seq"  # noqa: S608
            ).fetchall()
        entries: list[Entry] = []
        for raw in raws:
            fields = dict(zip(_COLUMNS, raw, strict=True))
            chain_hash = str(fields.pop("chain_hash"))
            entries.append(Entry(record=_record_from(fields), chain_hash=chain_hash))
        return entries

    def rows(self) -> list[tuple[AuditRecord, str]]:
        """The chain in the shape `chain.verify()` consumes."""
        return [(e.record, e.chain_hash) for e in self.rows_as_entries()]

    def verify(self) -> tuple[bool, str | None]:
        """Whether the chain is intact. The verdict comes from the pure `chain.verify`."""
        return verify(self.rows())

    def count(self) -> int:
        with self._lock:
            row = self.connection.execute("SELECT count(*) FROM audit_log").fetchone()
        return int(row[0])

    def count_by_status(self) -> dict[Status, int]:
        """Record count per status. All four are present, including zeros.

        `warden audit reconcile` compares this against the engine counter; a missing
        status would leave readers unable to tell "zero" from "not measured".
        """
        counts = dict.fromkeys(Status, 0)
        with self._lock:
            filas = self.connection.execute(
                "SELECT status, count(*) FROM audit_log GROUP BY status"
            ).fetchall()
        for value, total in filas:
            counts[Status(value)] = int(total)
        return counts

    def close(self) -> None:
        with self._lock:
            self.connection.close()


def _record_from(fields: dict[str, Any]) -> AuditRecord:
    """Rebuild a record read from disk.

    Fields are indexed by variable, never by a literal like `fields["role"]` (see
    `_COLUMNS`). Types are restored explicitly because SQLite has no boolean:
    `truncated` comes back as 0 or 1, and `0` instead of `False` changes the JCS and
    therefore the hash, making an untouched chain look broken.
    """
    restored: dict[str, Any] = {}
    for name, value in fields.items():
        if name in _JSON_FIELDS:
            decoded = json.loads(value) if value else None
            if name in ("tables", "columns_masked"):
                restored[name] = tuple(decoded or ())
            else:
                restored[name] = decoded or None
        elif name == "truncated":
            restored[name] = None if value is None else bool(value)
        elif name == "role":
            restored[name] = Role(value)
        elif name == "role_source":
            restored[name] = RoleSource(value)
        elif name == "status":
            restored[name] = Status(value)
        else:
            restored[name] = value
    return AuditRecord(**restored)
