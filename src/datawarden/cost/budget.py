"""Budget enforcement. `G-BUDGET-ESCAPE` is an axiom.

No query above budget reaches the engine: zero, not "almost none" and not "warn and
run". The goal has `propuesta_admisible: false`, and this module has no escape hatch
or bypass flag.

Rejections must also be actionable. "Over budget" without the cost, the limit and
what to cut teaches the model to retry the same query, and `G-RECOVERY` measures
whether the model can self-correct from the message alone. The suggestion is built
from the estimator's detail: if the query scanned all 730 partitions, the advice is
"add a date predicate", not "make it smaller".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from datawarden.domain.types import (
    CostEstimate,
    Position,
    RejectionReason,
    Role,
    Severity,
)
from datawarden.principal.budgets import BudgetBook, Decision


@dataclass(frozen=True, slots=True)
class BudgetVerdict:
    """What to do with the query, and why.

    `warning` covers the middle band: above the soft limit the query is not rejected,
    but the caller is told it will be expensive. Over MCP this becomes an MRTR
    `InputRequiredResult`.
    """

    decision: Decision
    rejection: RejectionReason | None = None
    warning: str | None = None


def enforce(cost: CostEstimate, book: BudgetBook, role: Role) -> BudgetVerdict:
    """Apply the role's budget to the estimated cost."""
    budget = book.for_role(role)
    decision = book.decide(role, estimated_bytes=cost.estimated_bytes)

    if decision is Decision.EXECUTE:
        return BudgetVerdict(decision=decision)

    if decision is Decision.CONFIRM:
        return BudgetVerdict(
            decision=decision,
            warning=(
                f"this query is estimated at {_human(cost.estimated_bytes)}, above the "
                f"{_human(budget.soft_bytes)} advisory limit for role {role.value} and "
                f"below the {_human(budget.hard_bytes)} hard limit"
            ),
        )

    return BudgetVerdict(
        decision=decision,
        rejection=RejectionReason(
            rule_id="BUDGET",
            code="over_budget",
            message=(
                f"the query would scan about {_human(cost.estimated_bytes)} "
                f"({cost.estimated_bytes} bytes across {cost.files_scanned} files) and "
                f"role {role.value} may scan at most "
                f"{_human(budget.hard_bytes)} ({budget.hard_bytes} bytes)"
            ),
            suggestion=_suggestion(cost, role),
            severity=Severity.BUDGET,
            position=Position.STATEMENT,
            subject=f"{cost.estimated_bytes} bytes",
            # Unlike a write attempt, narrowing the question does fix this, so a
            # retry is exactly what is wanted.
            retryable=True,
        ),
    )


def _suggestion(cost: CostEstimate, role: Role) -> str:
    """Suggestion derived from the estimator's detail rather than generic text."""
    unpruned = _table_without_pruning(cost.detail)
    if unpruned is not None:
        table, column = unpruned
        return (
            f"add a predicate on {table}.{column} so the engine can skip partitions: "
            f"`WHERE {column} >= DATE '...' AND {column} < DATE '...'`. Scanning every "
            "partition is what makes the query expensive, not the number of columns"
        )
    wide = _widest_table(cost.detail)
    if wide is not None:
        return (
            f"name fewer columns from {wide}: this warehouse is columnar, so every "
            "column you drop is bytes the engine never reads"
        )
    return (
        f"narrow the question: add a date range, aggregate earlier, or ask for fewer "
        f"columns. Role {role.value} is meant for that shape of query"
    )


def _table_without_pruning(detail: dict[str, Any]) -> tuple[str, str] | None:
    """First partitioned table whose partitions were not pruned."""
    for name, spec in (detail.get("per_table") or {}).items():
        total = spec.get("partitions_total", 1)
        kept = spec.get("partitions_kept", 1)
        if total > 1 and kept >= total:
            # `event_date` is the partition column of the warehouse's two large
            # tables, so the message can name it.
            return (name, "event_date")
    return None


def _widest_table(detail: dict[str, Any]) -> str | None:
    tables: dict[str, Any] = detail.get("per_table") or {}
    if not tables:
        return None
    widest: str = max(tables, key=lambda name: int(tables[name].get("bytes", 0)))
    return widest


def _human(nbytes: int) -> str:
    """Bytes in a unit a person can compare at a glance.

    Decimal GB, as declared in `docs/spec/budgets.yaml`, so rejection messages never
    mix GB and GiB.
    """
    for unit, size in (("GB", 1_000_000_000), ("MB", 1_000_000), ("kB", 1_000)):
        if nbytes >= size:
            return f"{nbytes / size:.2f} {unit}"
    return f"{nbytes} B"
