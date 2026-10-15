"""Per-role budgets: the soft limit warns, the hard limit refuses, `max_rows` truncates.

These are three separate decisions; collapsing them into one "budget" number serves
none of them.

- `hard_bytes` backs `G-BUDGET-ESCAPE`, an axiom: zero queries above budget reach
  the engine. It is not negotiable at runtime.
- `soft_bytes` asks for confirmation instead of refusing, because a guard that can
  only say no gets disabled. Over MCP it becomes an MRTR `InputRequiredResult`.
- `max_rows` truncates in the domain, never in the engine (I-12): per-engine caps
  would make DuckDB and Athena return different results for the same role, breaking
  acceptance criterion 5 of `docs/DESIGN.md`.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from datawarden.domain.types import Role


class Decision(StrEnum):
    """What to do with a query given its estimated cost."""

    EXECUTE = "execute"
    CONFIRM = "confirm"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class Budget:
    """One role's budget, in bytes."""

    role: Role
    soft_bytes: int
    hard_bytes: int
    max_rows: int
    #: `False` while `soft_bytes` is a design assumption rather than a value measured
    #: by `make cost-calibration`, so it is never published as measured.
    soft_is_calibrated: bool


@dataclass(frozen=True, slots=True)
class BudgetBook:
    """The four role budgets, loaded once."""

    budgets: Mapping[Role, Budget]
    source_sha256: str = ""

    def for_role(self, role: Role) -> Budget:
        return self.budgets[role]

    def max_rows(self, role: Role) -> int:
        return self.budgets[role].max_rows

    def decide(self, role: Role, *, estimated_bytes: int) -> Decision:
        """The cost verdict.

        The hard limit is inclusive: the axiom forbids queries above budget, so
        spending exactly the budget is allowed. Fixing it here keeps layers from
        disagreeing and producing an off-by-one-byte `G-BUDGET-ESCAPE` failure.
        """
        if estimated_bytes < 0:
            msg = (
                f"estimated_bytes={estimated_bytes}. A negative cost is meaningless, "
                "and accepting it would let through any query whose estimator got "
                "the sign wrong."
            )
            raise ValueError(msg)
        budget = self.budgets[role]
        if estimated_bytes > budget.hard_bytes:
            return Decision.REJECT
        if estimated_bytes > budget.soft_bytes:
            return Decision.CONFIRM
        return Decision.EXECUTE


def budgets_from_dict(payload: dict[str, Any]) -> BudgetBook:
    """Build the budget book from the compiled JSON. Pure."""
    rows = payload["roles"]
    budgets: dict[Role, Budget] = {}
    for role in Role:
        if role.value not in rows:
            msg = (
                f"missing budget for role {role.value!r}. A role without a budget would "
                "run unbounded, which is exactly the hole G-BUDGET-ESCAPE exists to "
                "close, and nothing would flag it."
            )
            raise ValueError(msg)
        row = rows[role.value]
        soft = int(row["soft_bytes"])
        hard = int(row["hard_bytes"])
        if soft > hard:
            msg = (
                f"{role.value}: soft_bytes ({soft}) above hard_bytes ({hard}). A "
                "warning that fires after the rejection warns of nothing."
            )
            raise ValueError(msg)
        budgets[role] = Budget(
            role=role,
            soft_bytes=soft,
            hard_bytes=hard,
            max_rows=int(row["max_rows"]),
            soft_is_calibrated=bool(row.get("soft_is_calibrated", False)),
        )
    return BudgetBook(
        budgets=MappingProxyType(budgets),
        source_sha256=str(payload.get("source_sha256", "")),
    )


def load_budgets(path: pathlib.Path) -> BudgetBook:
    """Load the compiled budgets. The only function that touches disk."""
    return budgets_from_dict(json.loads(path.read_text(encoding="utf-8")))
