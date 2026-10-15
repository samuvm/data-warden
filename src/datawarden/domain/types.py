"""Frozen domain types: pure, immutable, with no knowledge of engine or transport.

Everything that crosses from one ring to the next is one of these types, so the
guarantees live in the type rather than in whoever constructs it:

- A `RejectionReason` without an actionable suggestion cannot be constructed (I-09).
  Validating at the call site would let the first forgotten path emit a silent
  rejection.
- `RoleSource` has no value meaning "the client said so" (I-05). The role comes from
  the server process, a server-minted `PrincipalToken` or a CLI flag; `_meta` and
  tool arguments are data.
- `ValidatedQuery` holds the tree, not the input string, and `sql()` re-serializes
  it (I-02). This removes parser-differential attacks: the engine runs what sqlglot
  understood.

import-linter keeps this package free of engine, transport and model imports.
`sqlglot` is allowed on purpose: the validated tree is the domain type, and storing
it as text is exactly what I-02 forbids.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

import sqlglot.expressions as exp

__all__ = [
    "CostEstimate",
    "Position",
    "Principal",
    "RejectionReason",
    "ResultSet",
    "Role",
    "RoleSource",
    "Severity",
    "Status",
    "ValidatedQuery",
    "Verdict",
]

_RULE_ID_RE: Final = re.compile(r"^(R[0-9]{3}|BUDGET|POLICY|INTERNAL)$")
_CODE_RE: Final = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_MIN_TEXT: Final = 10
_MAX_TEXT: Final = 500

#: Allowed provenance of a cost figure. Iceberg metadata and `EXPLAIN` are not
#: equivalent, so cost calibration must know which one produced each estimate.
COST_METHODS: Final = frozenset({"iceberg", "explain", "hybrid", "fixture"})


class Role(StrEnum):
    """The four roles of `docs/spec/policy.yaml`; an extra role would have no access matrix."""

    ANALYST = "analyst"
    OPS = "ops"
    FINANCE = "finance"
    ADMIN = "admin"


class RoleSource(StrEnum):
    """Where the role came from. There is deliberately no "client-asserted" value.

    I-05 as a type. MCP 2026-07-28 removed protocol sessions, so the role cannot come
    from the session; if it could come from `_meta` or a tool argument, any caller
    could grant itself `admin`.
    """

    SERVER_PROCESS = "server_process"
    # The name of an authority source, not a credential. The signing secret lives
    # in configuration, not in the domain.
    PRINCIPAL_TOKEN = "principal_token"  # noqa: S105
    CLI_FLAG = "cli_flag"


class Status(StrEnum):
    """The four possible outcomes of an invocation, as named in `audit-record.schema.json`.

    Every invocation is audited, not only executed ones: a guard rejection is a
    security event (`G-AUDIT-COV`). The two rejections are kept apart because one
    means the query was illegal and the other that it was expensive.
    """

    REJECTED_BY_GUARD = "rejected_by_guard"
    REJECTED_BY_BUDGET = "rejected_by_budget"
    EXECUTED = "executed"
    ERROR = "error"


class Severity(StrEnum):
    """Rejection class. High `POLICY` and high `SECURITY` rates call for different action."""

    SECURITY = "security"
    POLICY = "policy"
    BUDGET = "budget"
    MALFORMED = "malformed"
    INTERNAL = "internal"


class Position(StrEnum):
    """Location in the tree, in SQL vocabulary rather than sqlglot's.

    Position is what most rejections hinge on: a `mask` column is legal in the
    projection and forbidden in `WHERE`.
    """

    PROJECTION = "projection"
    WHERE = "where"
    JOIN_ON = "join_on"
    GROUP_BY = "group_by"
    ORDER_BY = "order_by"
    HAVING = "having"
    QUALIFY = "qualify"
    FUNCTION_ARGUMENT = "function_argument"
    WINDOW_PARTITION = "window_partition"
    SUBQUERY = "subquery"
    CTE = "cte"
    STATEMENT = "statement"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is asking, and with what authority."""

    id: str
    role: Role
    source: RoleSource

    def __post_init__(self) -> None:
        if not self.id.strip():
            message = (
                "empty principal_id. Without an identifier there is no audit trail and "
                "no non-repudiation, which is the only control over the admin role "
                "since admin has no masking."
            )
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class RejectionReason:
    """An actionable rejection, conforming to `docs/spec/rejection.schema.json`.

    Contract constraints are checked at construction, not at serialization: a
    malformed rejection that only fails when written to the audit log has already
    reached the client.
    """

    rule_id: str
    code: str
    message: str
    suggestion: str
    severity: Severity
    position: Position = Position.UNKNOWN
    subject: str | None = None
    alternative: str | None = None
    retryable: bool = True
    docs: str | None = None

    def __post_init__(self) -> None:
        if not _RULE_ID_RE.match(self.rule_id):
            msg = (
                f"rule_id {self.rule_id!r} is not a registered identifier. rule_ids "
                "are a closed registry (I-01): RNNN for guard rules and BUDGET / "
                "POLICY / INTERNAL for rejections that do not come from a rule. A "
                "free-form string would make it impossible to check that no rule "
                "disappears."
            )
            raise ValueError(msg)
        if not _CODE_RE.match(self.code):
            msg = (
                f"code {self.code!r} is not a stable lowercase identifier. code is "
                "the metrics grouping key: 'DeniedColumn' and 'denied_column' must "
                "not be two different codes."
            )
            raise ValueError(msg)
        _require_text("message", self.message)
        _require_text(
            "suggestion",
            self.suggestion,
            extra=(
                "A rejection with no way forward blocks the work instead of "
                "redirecting it, and a guard that blocks work gets disabled (I-09)."
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Contract shape. Absent optional fields are omitted, never written as `null`.

        These objects are hashed into the audit chain; emitting `null` for some and
        omitting for others would give identical rejections two representations.
        """
        payload: dict[str, Any] = {
            "rule_id": self.rule_id,
            "code": self.code,
            "message": self.message,
            "suggestion": self.suggestion,
            "severity": self.severity.value,
            "position": self.position.value,
            "retryable": self.retryable,
        }
        if self.subject is not None:
            payload["subject"] = self.subject
        if self.alternative is not None:
            payload["alternative"] = self.alternative
        if self.docs is not None:
            payload["docs"] = self.docs
        return payload


@dataclass(frozen=True, slots=True)
class ValidatedQuery:
    """The only thing an engine accepts. Carries the tree, never the input string.

    `Engine.execute()` takes this and never a `str`, enforced by
    `scripts/check_no_raw_sql.py`. If sqlglot misreads a query, the database receives
    what sqlglot understood, not what the attacker wrote.
    """

    ast: exp.Expression
    dialect: str
    principal: Principal
    tables: tuple[str, ...]
    columns: tuple[str, ...]
    max_rows: int
    masked_columns: tuple[str, ...] = ()
    rule_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_rows <= 0:
            msg = (
                f"max_rows={self.max_rows}. The row cap belongs to the domain, not the "
                "engine (I-12): otherwise each engine would apply its own and DuckDB "
                "and Athena would return different results for the same role."
            )
            raise ValueError(msg)

    def sql(self) -> str:
        """The SQL that runs: the tree re-serialized without comments (I-02).

        sqlglot keeps comments in the tree and re-emits them, so
        `SELECT /*+ hint */ a FROM t` would carry the attacker's comment to the
        engine. The guard validates structure, so only structure may execute. Also:

        - some engines read optimizer hints from comments, i.e. unvalidated text
          influencing the plan;
        - `sql_digest()` would vary with comments, giving identical queries two
          different audit records.
        """
        return self.ast.sql(dialect=self.dialect, comments=False)

    def sql_digest(self) -> str:
        """sha256 of the SQL that actually runs, for the audit chain.

        Hashing the input instead would make the audit log certify something other
        than what executed.
        """
        return hashlib.sha256(self.sql().encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CostEstimate:
    """What the query would cost, computed before execution."""

    estimated_bytes: int
    estimated_rows: int
    files_scanned: int
    method: str
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("estimated_bytes", self.estimated_bytes),
            ("estimated_rows", self.estimated_rows),
            ("files_scanned", self.files_scanned),
        ):
            if value < 0:
                msg = f"{name}={value}: a negative estimate is meaningless"
                raise ValueError(msg)
        if self.method not in COST_METHODS:
            msg = (
                f"method={self.method!r} is not one of {sorted(COST_METHODS)}. A cost "
                "without a declared method is not auditable: Iceberg metadata and "
                "EXPLAIN are not equivalent and G-COST-CALIB must tell them apart."
            )
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ResultSet:
    """Returned rows, with their shape and an honest truncation flag."""

    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    truncated: bool

    def __post_init__(self) -> None:
        width = len(self.columns)
        if any(len(row) != width for row in self.rows):
            msg = (
                f"some rows do not have {width} cells, one per name in columns. "
                "Ragged rows break the comparison in docs/spec/resultset-equality.md "
                "in the hardest place to debug."
            )
            raise ValueError(msg)

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def column_count(self) -> int:
        """Shape is known even with no rows (resultset-equality, decision 8)."""
        return len(self.columns)


#: Guard output: a validated query or the reason it was refused. An
#: `Optional[ValidatedQuery]` would lose the reason.
Verdict = ValidatedQuery | RejectionReason


def _require_text(name: str, value: str, *, extra: str = "") -> None:
    stripped = value.strip()
    if len(stripped) < _MIN_TEXT or len(stripped) > _MAX_TEXT:
        msg = (
            f"{name} has {len(stripped)} characters and the contract requires between "
            f"{_MIN_TEXT} and {_MAX_TEXT}. {extra}"
        ).strip()
        raise ValueError(msg)
