"""Guard and cost chained: validate first, then estimate.

Order matters. Estimating before validating would price a query that may not even be
executable, and would force the estimator to reason over an unqualified tree with
unresolved aliases and unexpanded `SELECT *`, so projection pruning would count the
wrong columns.

This module does not execute. It returns a `ValidatedQuery` or a rejection, and
`AuditedExecutor`, the only path to `Engine.execute()`, runs it. That separation
makes `G-BUDGET-ESCAPE` testable by counter: if the verdict is a rejection and the
engine counter did not move, the expensive query never reached the engine.
"""

from __future__ import annotations

from dataclasses import dataclass

from datawarden.catalog.statistics import Statistics
from datawarden.catalog.types import CatalogSchema
from datawarden.cost.budget import enforce
from datawarden.cost.estimator import estimate
from datawarden.domain.types import (
    CostEstimate,
    Principal,
    RejectionReason,
    ValidatedQuery,
)
from datawarden.guard.context import DEFAULT_TIMEOUT_MS
from datawarden.guard.validator import validate
from datawarden.principal.budgets import BudgetBook, Decision
from datawarden.principal.policy import AccessPolicy


@dataclass(frozen=True, slots=True)
class ScreenResult:
    """Verdict of guard and cost, with everything the audit record needs.

    Carries the estimated cost even on rejection: `G-COST-CALIB` compares estimated
    against actual, and without the rejected estimates calibration would only see
    cheap queries.
    """

    query: ValidatedQuery | None
    rejection: RejectionReason | None
    cost: CostEstimate | None = None
    decision: Decision | None = None
    warning: str | None = None

    @property
    def accepted(self) -> bool:
        return self.query is not None


def screen(
    sql: str,
    *,
    principal: Principal,
    schema: CatalogSchema,
    policy: AccessPolicy,
    budgets: BudgetBook,
    stats: Statistics,
    dialect: str = "duckdb",
    timeout_ms: float = DEFAULT_TIMEOUT_MS,
) -> ScreenResult:
    """Guard ring and cost ring. Never raises, never executes."""
    verdict = validate(
        sql,
        principal=principal,
        schema=schema,
        policy=policy,
        max_rows=budgets.max_rows(principal.role),
        dialect=dialect,
        timeout_ms=timeout_ms,
    )
    if isinstance(verdict, RejectionReason):
        return ScreenResult(query=None, rejection=verdict)

    cost = estimate(verdict, stats)
    budget = enforce(cost, budgets, principal.role)
    if budget.rejection is not None:
        return ScreenResult(
            query=None,
            rejection=budget.rejection,
            cost=cost,
            decision=budget.decision,
        )
    return ScreenResult(
        query=verdict,
        rejection=None,
        cost=cost,
        decision=budget.decision,
        warning=budget.warning,
    )
