"""`AuditedExecutor`: the only path to `Engine.execute()` (I-06).

`G-AUDIT-COV` is an axiom: no invocation without its record. Invocation, not
execution: all four statuses are audited, and the most important one executes
nothing. A guard rejection is a security event, and someone systematically probing
the policy to discover columns would leave no trace if only successes were logged.

This module does not re-implement ring ordering. It wraps `screen_and_mask()`, which
already chains validate, estimate, budget and mask without executing; a second copy
of that order would be a second place to get it wrong.

The two rejections are told apart structurally: a guard rejection carries
`cost=None`, a budget rejection carries its computed cost. Classification does not
parse `rule_id` or message text, so it survives any rewording of the messages.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

from datawarden.audit.store import AuditStore, Entry
from datawarden.catalog.statistics import Statistics
from datawarden.catalog.types import CatalogSchema
from datawarden.domain.types import (
    Principal,
    RejectionReason,
    ResultSet,
    Status,
    ValidatedQuery,
)
from datawarden.engines.base import Engine
from datawarden.mask.config import MaskConfig
from datawarden.mask.pipeline import screen_and_mask
from datawarden.principal.budgets import BudgetBook
from datawarden.principal.policy import AccessPolicy

#: `sql_digest` when there is no validated tree, i.e. every `rejected_by_guard`
#: (R001 rejects input that does not even parse). The contract defines the field as
#: the sha256 of SQL re-serialized from the validated AST, never of the input string,
#: so hashing the input would certify something other than what ran. 64 zeros is a
#: legal, distinguishable value, the same convention as the `prev_hash` genesis.
#:
#: Same padding as `chain.GENESIS` with a different meaning ("first in chain" there,
#: "no validated tree" here). Different fields, so no collision, but a record that is
#: both first in the chain and a pre-parse rejection carries both. Read by field,
#: never by value; both contract field descriptions say so.
NO_VALIDATED_TREE = "0" * 64


@dataclass(frozen=True, slots=True)
class RunResult:
    """The outcome of an invocation, alongside its audit entry."""

    rows: ResultSet | None
    rejection: RejectionReason | None
    query: ValidatedQuery | None
    entry: Entry

    @property
    def executed(self) -> bool:
        return self.rows is not None


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class AuditedExecutor:
    """Validates, budgets, masks, executes if allowed, and records the outcome regardless."""

    def __init__(
        self,
        *,
        engine: Engine,
        store: AuditStore,
        schema: CatalogSchema,
        policy: AccessPolicy,
        budgets: BudgetBook,
        stats: Statistics,
        mask: MaskConfig,
        dialect: str = "duckdb",
    ) -> None:
        self.engine = engine
        self.store = store
        self.schema = schema
        self.policy = policy
        self.budgets = budgets
        self.stats = stats
        # `mask` is required and has no default. The executor once called `screen()`
        # and skipped masking, returning real names and emails to `analyst` while the
        # record said `columns_masked: []`. With no default, an executor that does
        # not mask cannot be constructed.
        self.mask = mask
        self.dialect = dialect

    def run(
        self,
        sql: str,
        *,
        principal: Principal,
        question: str | None = None,
        keep_question: bool = False,
        trace_id: str | None = None,
    ) -> RunResult:
        """One full invocation. Always leaves exactly one record.

        `question` is stored hashed: a natural-language question may contain personal
        data, and the digest still allows grouping and counting. Keeping the text
        requires an explicit `keep_question=True`.
        """
        question_digest = _digest(question if question is not None else sql)
        preview = question if (keep_question and question is not None) else None
        common: dict[str, Any] = {
            "principal_id": principal.id,
            "role": principal.role,
            "role_source": principal.source,
            "question_digest": question_digest,
            "question_preview": preview,
            "trace_id": trace_id,
        }

        # Validate, cost, budget and mask. Masking runs after budgeting on purpose:
        # rewriting earlier would change the tree the estimator priced, and the
        # published cost would no longer match what ran.
        result = screen_and_mask(
            sql,
            principal=principal,
            schema=self.schema,
            policy=self.policy,
            budgets=self.budgets,
            stats=self.stats,
            config=self.mask,
            dialect=self.dialect,
        )

        if result.rejection is not None:
            # Structural, not textual: the guard rejects before estimating (no cost),
            # the budget rejects after (cost present).
            by_guard = result.cost is None
            entry = self.store.append(
                status=Status.REJECTED_BY_GUARD if by_guard else Status.REJECTED_BY_BUDGET,
                sql_digest=NO_VALIDATED_TREE,
                rejection=_rejection_payload(result.rejection),
                estimated_bytes=None if result.cost is None else result.cost.estimated_bytes,
                budget_bytes=self.budgets.for_role(principal.role).hard_bytes,
                **common,
            )
            return RunResult(rows=None, rejection=result.rejection, query=None, entry=entry)

        query = result.query
        assert query is not None
        started = time.perf_counter()
        try:
            rows = self.engine.execute(query)
        except Exception:
            # Catch, write the record, re-raise. Swallowing would turn an engine
            # failure into an empty result indistinguishable from "no rows", and not
            # recording it would leave no trace of the event most worth investigating.
            # The bare `raise` keeps the original traceback.
            self.store.append(
                status=Status.ERROR,
                sql_digest=query.sql_digest(),
                sql=query.sql(),
                tables=query.tables,
                columns_masked=query.masked_columns,
                estimated_bytes=None if result.cost is None else result.cost.estimated_bytes,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                engine=self.engine.name,
                **common,
            )
            raise

        entry = self.store.append(
            status=Status.EXECUTED,
            sql_digest=query.sql_digest(),
            sql=query.sql(),
            tables=query.tables,
            columns_masked=query.masked_columns,
            estimated_bytes=None if result.cost is None else result.cost.estimated_bytes,
            budget_bytes=self.budgets.for_role(principal.role).hard_bytes,
            row_count=len(rows.rows),
            truncated=rows.truncated,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            engine=self.engine.name,
            **common,
        )
        return RunResult(rows=rows, rejection=None, query=query, entry=entry)


def _rejection_payload(rejection: RejectionReason) -> dict[str, Any]:
    """The rejection in the shape stored in the record.

    Stores the structure (who rejected, about what, where) rather than only the
    sentence. Message wording is tuned against `G-RECOVERY`; the fields used to group
    by cause must not change with it.
    """
    return {
        "rule_id": rejection.rule_id,
        "code": rejection.code,
        "severity": str(rejection.severity),
        "position": str(rejection.position),
        "subject": rejection.subject,
        "retryable": rejection.retryable,
    }
