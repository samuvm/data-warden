"""The four operations and the shape of their results. Transport-neutral.

No policy decisions here. The role arrives in the constructor, never per call; the
engine is reached only through `AuditedExecutor`, the single path through all four
rings (I-06).

A rejection is an answer, not an error, decided here so both transports inherit it:
MCP returns it with `is_error: false` and HTTP with a 200. Treated as an error, a
client would retry blind instead of reading the rule, reason and alternative.
"""

from __future__ import annotations

from typing import Any

from datawarden.audit.executor import AuditedExecutor, RunResult
from datawarden.domain.types import Principal, RejectionReason
from datawarden.principal.budgets import Decision


def to_payload(result: RunResult) -> dict[str, Any]:
    """An invocation result in the shape published by `outputSchema`.

    `columns_masked` is sent to the client deliberately: masking is not a secret, and
    stating it keeps readers from mistaking `***` for real data.
    """
    if result.rejection is not None:
        return {"outcome": "rejected", "rejected": _rejection_dict(result.rejection)}
    rows = result.rows
    assert rows is not None
    return {
        "outcome": "rows",
        "result": {
            "columns": list(rows.columns),
            "rows": [list(row) for row in rows.rows],
            "row_count": len(rows.rows),
            "truncated": rows.truncated,
            "columns_masked": list(result.query.masked_columns) if result.query else [],
        },
    }


def _rejection_dict(rejection: RejectionReason) -> dict[str, Any]:
    return {
        "rule_id": rejection.rule_id,
        "code": rejection.code,
        "message": rejection.message,
        "suggestion": rejection.suggestion,
        "severity": rejection.severity.value,
        "position": rejection.position.value,
        "subject": rejection.subject,
        "alternative": rejection.alternative,
        "retryable": rejection.retryable,
    }


def _list_tables_payload(schema: Any) -> dict[str, Any]:
    """Published relations; the entry point into the warehouse."""
    published = schema.published()
    return {
        "outcome": "rows",
        "result": {
            "columns": ["table", "kind", "columns"],
            "rows": [[t.name, t.kind, len(t.columns)] for t in published.tables],
            "row_count": len(published.tables),
            "truncated": False,
            "columns_masked": [],
        },
    }


def _unknown_relation(table: str) -> dict[str, Any]:
    """Rejection for a table not in the catalog, with the same `rule_id` as R004.

    Same shape as the SQL path so clients handle one form for the same event.
    """
    return {
        "outcome": "rejected",
        "rejected": {
            "rule_id": "R004",
            "code": "relation_out_of_scope",
            "message": f"relation {table.lower()} is not in the generated catalog",
            # The suggestion names something every client can do. "Read the catalog
            # resource" is useless to a client without resource support (observed
            # with Claude Desktop as client).
            "suggestion": (
                "call describe_table with no arguments to see the tables that exist, "
                "then describe the one you need"
            ),
            "severity": "security",
            "position": "statement",
            "subject": table.lower(),
            "alternative": None,
            "retryable": True,
        },
    }


class WardenTools:
    """The four tools on top of the audited executor. They decide nothing.

    The `principal` is fixed in the constructor and never read from call arguments
    (`G-ROLE-SPOOF`); no method may accept a `role=` parameter.
    """

    def __init__(self, *, executor: AuditedExecutor, principal: Principal) -> None:
        self._executor = executor
        self._principal = principal

    def run_query(
        self,
        question_sql: str,
        question: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Validate, budget, mask, execute if allowed, and audit in every case.

        `trace_id` comes from the validated `_meta` `traceparent`. It is data, not
        authority, and is recorded so security events correlate with their request.
        """
        return to_payload(
            self._executor.run(
                question_sql,
                principal=self._principal,
                question=question,
                trace_id=trace_id,
            )
        )

    def budget_decision(self, question_sql: str) -> Decision | None:
        """The budget decision without executing or auditing; `None` if the guard rejects.

        Used by MRTR to decide whether to ask before spending. Separate from
        `explain_cost`, which returns a human-facing answer.

        Uses `screen()`, which `check_mask_path.py` forbids in the executor: this
        returns no rows, so there is nothing to mask. The rule is that whatever
        returns data goes through ring 4.
        """
        from datawarden.cost.screen import screen

        result = screen(
            question_sql,
            principal=self._principal,
            schema=self._executor.schema,
            policy=self._executor.policy,
            budgets=self._executor.budgets,
            stats=self._executor.stats,
        )
        return result.decision

    def explain_cost(self, question_sql: str) -> dict[str, Any]:
        """What a query would cost, without executing it. Rings 2 and 3 only.

        Uses `screen()`, which `check_mask_path.py` forbids in the executor: this
        returns no rows, so there is nothing to mask.

        Known limitation: this leaves no audit record, since I-06 covers the path to
        the engine and this does not reach it. Repeated `explain_cost` calls with
        different predicates still reveal how the warehouse is partitioned. Auditing
        it would require a new status in `docs/spec/audit-record.schema.json` and a
        fifth state in `G-AUDIT-COV`, which is a contract change.
        """
        from datawarden.cost.screen import screen

        result = screen(
            question_sql,
            principal=self._principal,
            schema=self._executor.schema,
            policy=self._executor.policy,
            budgets=self._executor.budgets,
            stats=self._executor.stats,
        )
        if result.rejection is not None and result.cost is None:
            # Guard rejection: nothing was estimated, so there is no cost to report.
            # Returning zero here would understate the cost.
            return {"outcome": "rejected", "rejected": _rejection_dict(result.rejection)}

        cost = result.cost
        assert cost is not None
        budget = self._executor.budgets.for_role(self._principal.role)
        return {
            "outcome": "rows",
            "result": {
                "columns": ["estimated_bytes", "estimated_rows", "files", "budget", "decision"],
                "rows": [
                    [
                        cost.estimated_bytes,
                        cost.estimated_rows,
                        cost.files_scanned,
                        budget.hard_bytes,
                        (result.decision or Decision.EXECUTE).value,
                    ]
                ],
                "row_count": 1,
                "truncated": False,
                "columns_masked": [],
            },
        }

    def sample_table(self, table: str, limit: int = 10) -> dict[str, Any]:
        """Unfiltered sample rows. Everything interpolated comes from the catalog.

        This is the one place the server composes SQL, and injection is closed by
        construction rather than by escaping:

        1. The table name is resolved against the generated catalog first. What ends
           up in the `SELECT` is `found.name`, an identifier from
           `catalog/generated/schema.json`, not the client's string. Unknown names
           (including ones containing a subquery) are rejected before composing.
        2. Even so, what runs is the guard-validated tree, never this string: R004
           rejects relations outside the catalog and the allowlist rejects unknown
           nodes.
        """
        found = self._executor.schema.table(table.lower())
        if found is None:
            return _unknown_relation(table)
        columns = [c.name for c in found.columns if c.published]
        projection = ", ".join(columns) if columns else "*"
        # `found.name` and `columns` come from the catalog; `limit` goes through
        # `int()` and `inputSchema` bounds it to 1..100. Nothing comes from the request.
        sql = f"SELECT {projection} FROM {found.name} LIMIT {int(limit)}"  # noqa: S608
        return to_payload(self._executor.run(sql, principal=self._principal))

    def _list_tables(self) -> dict[str, Any]:
        """Published relations; the entry point when nothing is known yet."""
        return _list_tables_payload(self._executor.schema)

    def describe_table(self, table: str | None = None) -> dict[str, Any]:
        """Describe a table or, with no argument, list the tables that exist.

        Without the no-argument case the server was undiscoverable. Observed with
        Claude Desktop as client: the catalog was only an MCP resource, the client
        could not read resources, and the model guessed names (`customers`,
        `clientes`, `payments`, `pagos`) until giving up, while the rejection told it
        to "read the catalog resource", an action it could not perform.

        MCP resources are optional for clients; tools are not, so discovery must
        exist as a tool. The resource remains for clients that read it.
        """
        if table is None or not table.strip():
            return self._list_tables()
        found = self._executor.schema.table(table.lower())
        if found is None:
            return _unknown_relation(table)
        return {
            "outcome": "rows",
            "result": {
                "columns": ["column", "type", "derives_from"],
                "rows": [
                    [c.name, c.engine_type, ", ".join(c.derives_from)]
                    for c in found.columns
                    if c.published
                ],
                "row_count": sum(1 for c in found.columns if c.published),
                "truncated": False,
                "columns_masked": [],
            },
        }
