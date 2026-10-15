"""The control rings chained: validate, cost, budget and mask.

`cost/screen.py` chains the first three and stops there on purpose: the layer
contract places `datawarden.mask` above `datawarden.cost`, so `screen()` can never
import the masker. This module is the seam, and it lives here because `mask` may
depend downward.

Without it, `mask/` would be dead code: nothing would call it, `G-PII-LEAK` could
not be measured, and the seventeen `mask` columns would leak in clear with the guard
green. Every piece works and nothing connects them, which raises no error.

It does not execute, just like `screen()`. It returns the validated, budgeted and
masked tree; `AuditedExecutor`, the only path to `Engine.execute()`, runs it. This
keeps `G-BUDGET-ESCAPE` testable by counter: if the verdict is a rejection and the
engine counter did not move, the query never reached the engine.
"""

from __future__ import annotations

from dataclasses import dataclass

from datawarden.catalog.statistics import Statistics
from datawarden.catalog.types import CatalogSchema
from datawarden.cost.screen import ScreenResult, screen
from datawarden.domain.types import (
    CostEstimate,
    Principal,
    RejectionReason,
    ValidatedQuery,
)
from datawarden.guard.context import DEFAULT_TIMEOUT_MS
from datawarden.mask.config import MaskConfig
from datawarden.mask.rewrite import mask_query
from datawarden.principal.budgets import BudgetBook, Decision
from datawarden.principal.policy import AccessPolicy


@dataclass(frozen=True, slots=True)
class MaskedResult:
    """Verdict of the chained rings, with everything the audit record needs."""

    query: ValidatedQuery | None
    rejection: RejectionReason | None
    cost: CostEstimate | None = None
    decision: Decision | None = None
    warning: str | None = None

    @property
    def accepted(self) -> bool:
        return self.query is not None

    @property
    def masked_columns(self) -> tuple[str, ...]:
        """Columns the masker rewrote. Evidence, not a promise."""
        return () if self.query is None else self.query.masked_columns


def screen_and_mask(
    sql: str,
    *,
    principal: Principal,
    schema: CatalogSchema,
    policy: AccessPolicy,
    budgets: BudgetBook,
    stats: Statistics,
    config: MaskConfig,
    dialect: str = "duckdb",
    timeout_ms: float = DEFAULT_TIMEOUT_MS,
) -> MaskedResult:
    """Guard, cost, budget and masking. Never raises, never executes.

    Order is fixed. Masking before validation would operate on an unqualified tree,
    with unresolved aliases and unexpanded `SELECT *`, missing half the sensitive
    columns. Masking before budgeting would change the tree the estimator priced, so
    the published cost would not match what ran.
    """
    result: ScreenResult = screen(
        sql,
        principal=principal,
        schema=schema,
        policy=policy,
        budgets=budgets,
        stats=stats,
        dialect=dialect,
        timeout_ms=timeout_ms,
    )
    if result.query is None:
        return MaskedResult(
            query=None,
            rejection=result.rejection,
            cost=result.cost,
            decision=result.decision,
        )

    masked = mask_query(result.query, policy=policy, config=config)
    if isinstance(masked, RejectionReason):
        # The masker only rejects shapes the guard should not have let through.
        # Propagate as-is: downgrading to "accepted without masking" is the very
        # leak this step exists to prevent.
        return MaskedResult(
            query=None,
            rejection=masked,
            cost=result.cost,
            decision=result.decision,
        )

    return MaskedResult(
        query=masked,
        rejection=None,
        cost=result.cost,
        decision=result.decision,
        warning=result.warning,
    )
