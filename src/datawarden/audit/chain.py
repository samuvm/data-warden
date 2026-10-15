"""Audit chain: canonicalize, link and verify. Pure.

No disk, no SQLite, no clock. This lets the `G-AUDIT-TAMPER` property run a thousand
Hypothesis examples on an in-memory chain instead of opening a thousand databases.

Scope of the hash chain: `h_n = sha256(h_{n-1} || jcs(record_n))` forces anyone who
rewrites a record to rewrite every later one. That detects tampering by someone who
cannot rebuild the whole store, and not by someone who can; `docs/threat-model.md`
states this limit, and `warden audit anchor` exists for it.

`AuditRecord` has no `hash` field on purpose. The contract defines the hash over the
record without that field, so the type is that record and `link()` returns the hash.
Storing it inside would require a half-built object with an empty field, which is
where an unhashed record slips through. It also avoids shadowing the `hash` builtin.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Final

from datawarden.domain.types import Role, RoleSource, Status

#: `prev_hash` of the first record: 64 zeros rather than `null`, a legal and
#: distinguishable value so the verifier needs no special case
#: (`docs/spec/audit-record.schema.json`).
#:
#: The same padding has a second meaning in `executor.NO_VALIDATED_TREE` ("no
#: validated tree to hash"). They live in different fields, so they do not collide,
#: but the first record of a chain that is also a pre-parse rejection carries 64
#: zeros in both `prev_hash` and `sql_digest`. Read them by field, never by value;
#: do not write a generic "is this a sentinel?" helper.
GENESIS: Final = "0" * 64

#: Schema version written on new records. It is the field's default, not a constant
#: injected at canonicalization time.
#:
#: Injecting it would mean a tampered `schema_version` in a stored record goes
#: undetected (it would be rebuilt from the constant), and bumping to 2 would make
#: every version-1 record fail verification. The contract hashes `schema_version` so
#: that mixed-version chains remain verifiable, which only works if each record keeps
#: its own.
SCHEMA_VERSION: Final = 1

#: Fields always emitted. Everything else is optional and omitted when empty: the
#: contract has `additionalProperties: false` and most fields are nullable, so
#: emitting thirteen `null`s per record adds size without information.
#:
#: `tables` and `columns_masked` are always emitted even when empty. An empty
#: `columns_masked` is evidence that nothing was masked, while a missing field is
#: ambiguous (nothing to mask, or written by a version without the field). The
#: contract defines it as evidence that masking happened, not a promise.
_ALWAYS: Final = (
    "seq",
    "recorded_at",
    "principal_id",
    "role",
    "role_source",
    "status",
    "question_digest",
    "sql_digest",
    "prev_hash",
    "schema_version",
    "tables",
    "columns_masked",
)


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """An audit record without its hash, conforming to `audit-record.schema.json`.

    The first ten fields are the schema's `required` ones; the rest are optional and
    emitted only when set.
    """

    seq: int
    recorded_at: str
    principal_id: str
    role: Role
    role_source: RoleSource
    status: Status
    question_digest: str
    sql_digest: str
    prev_hash: str = GENESIS
    #: Contract version this record was written with, not the current one. Defaults
    #: to the current version and is restored as stored when read from disk.
    schema_version: int = SCHEMA_VERSION

    question_preview: str | None = None
    sql: str | None = None
    tables: tuple[str, ...] = ()
    columns_masked: tuple[str, ...] = ()
    rejection: dict[str, Any] | None = None
    estimated_bytes: int | None = None
    scanned_bytes: int | None = None
    budget_bytes: int | None = None
    row_count: int | None = None
    truncated: bool | None = None
    duration_ms: float | None = None
    engine: str | None = None
    model: dict[str, Any] | None = field(default=None, repr=False)
    trace_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """The record as a dict, ready for canonicalization.

        `tables` and `columns_masked` must be sorted here. JCS sorts object keys but
        never array elements, so the emitter owns array order; otherwise the same
        query and role would hash differently depending on sqlglot's traversal order.
        """
        payload: dict[str, Any] = {
            "seq": self.seq,
            "recorded_at": self.recorded_at,
            "principal_id": self.principal_id,
            "role": str(self.role),
            "role_source": str(self.role_source),
            "status": str(self.status),
            "question_digest": self.question_digest,
            "sql_digest": self.sql_digest,
            "prev_hash": self.prev_hash,
            "schema_version": self.schema_version,
            "tables": sorted(self.tables),
            "columns_masked": sorted(self.columns_masked),
            "question_preview": self.question_preview,
            "sql": self.sql,
            "rejection": self.rejection,
            "estimated_bytes": self.estimated_bytes,
            "scanned_bytes": self.scanned_bytes,
            "budget_bytes": self.budget_bytes,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "duration_ms": self.duration_ms,
            "engine": self.engine,
            "model": self.model,
            "trace_id": self.trace_id,
        }
        return {
            name: value
            for name, value in payload.items()
            if name in _ALWAYS or value not in (None, [], ())
        }


def canonicalize(record: AuditRecord) -> str:
    """JCS (RFC 8785) of the record without its hash.

    Without canonicalization the hash depends on dict ordering, and two
    implementations would produce chains that do not verify against each other.

    Known limit of this implementation. JCS has three rules: keys sorted by UTF-16
    code units, no whitespace, and ECMAScript number formatting. `json.dumps` gives
    the first two exactly (all keys here are ASCII, where code point and UTF-16 order
    agree). The third matches only for integers, which is everything in this contract
    except `duration_ms`; that field is rounded to microseconds at construction, where
    Python's `repr` and ECMAScript agree. The golden vector in the tests makes this a
    checkable agreement.
    """
    return json.dumps(
        record.to_payload(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def link(record: AuditRecord) -> str:
    """`sha256(prev_hash_bytes || jcs(record_without_hash))`, hex-encoded.

    `prev_hash_bytes` are the 32 raw bytes, not the 64 hex characters: the contract
    names the bytes of the hash, not of its text. Security-wise it makes no
    difference (`prev_hash` is also inside the JCS), but interoperability depends on
    it. Pinned by a golden vector in `tests/unit/audit/test_chain.py`.
    """
    return hashlib.sha256(
        bytes.fromhex(record.prev_hash) + canonicalize(record).encode("utf-8")
    ).hexdigest()


def verify(rows: list[tuple[AuditRecord, str]]) -> tuple[bool, str | None]:
    """Walk the chain and report whether it is intact and, if not, where it breaks.

    Returns `(True, None)` or `(False, reason)`. The reason names the `seq` so nobody
    has to bisect a million-row store by hand.

    Four checks, one per tampering mode:

    1. Each record's hash matches its content: a field was edited.
    2. Each `prev_hash` equals the previous record's hash: a record and its hash were
       rewritten but the next one was not. This is the attack the chain exists for.
    3. The chain starts at genesis: history was deleted from the front.
    4. `seq` is consecutive: a record was deleted from the middle.

    An empty chain verifies: a freshly created store is not corrupt.
    """
    previous = GENESIS
    expected_seq = 1
    for record, stored_hash in rows:
        if record.seq != expected_seq:
            return False, (
                f"seq {record.seq} where {expected_seq} was expected: a gap in the "
                "numbering, i.e. a deleted record."
            )
        if record.prev_hash != previous:
            return False, (
                f"seq {record.seq}: its prev_hash is not the previous record's hash. "
                "A record and its hash were rewritten, but not the later ones."
            )
        recomputed = link(record)
        if recomputed != stored_hash:
            return False, (
                f"seq {record.seq}: the stored hash does not match the content. "
                "The record was edited after being written."
            )
        previous = stored_hash
        expected_seq += 1
    return True, None
