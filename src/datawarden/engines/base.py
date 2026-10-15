"""Engine port. Accepts a `ValidatedQuery`, never a `str` (I-02).

Checked by `scripts/check_no_raw_sql.py` over this package's AST. If the engine
accepted text, a query could reach the database without passing the guard.

The execution counter makes `G-BUDGET-ESCAPE` checkable: "no over-budget query
reaches the engine" cannot be proven from the verdict, only by observing whether
the engine was entered. With a process counter the property is exact (`delta == 0`
when the estimated cost exceeds the budget).

Tested by contract and snapshot, not line coverage (`docs/RULES.md` section 2).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from datawarden.domain.types import ResultSet, ValidatedQuery

#: Executions that reached an engine in this process. Read by the `G-BUDGET-ESCAPE`
#: property; every `Engine` implementation increments it first, before any work.
_EXECUTIONS = 0


def executions() -> int:
    """How many times an engine has been entered in this process."""
    return _EXECUTIONS


def count_execution() -> None:
    """Called by every engine on entry. No engine is exempt."""
    global _EXECUTIONS
    _EXECUTIONS += 1


@runtime_checkable
class Engine(Protocol):
    """What an engine must implement, with exactly this input type."""

    name: str

    def execute(self, query: ValidatedQuery) -> ResultSet:
        """Execute the re-serialized tree. Never a raw string."""
        ...


class RecordingEngine:
    """Fake engine that executes nothing and records everything.

    Backs the `G-BUDGET-ESCAPE` and I-12 properties: with a real engine one can only
    say a query returned nothing, not that it never arrived. It can return many rows
    (e.g. 10,000) to check that the domain, not the engine, applies `max_rows`.
    """

    name = "recording"

    def __init__(self, rows: int = 0) -> None:
        self.seen: list[str] = []
        self._rows = rows

    def execute(self, query: ValidatedQuery) -> ResultSet:
        count_execution()
        self.seen.append(query.sql())
        columns = query.columns or ("x",)
        return ResultSet(
            columns=columns,
            rows=tuple(tuple(i for _ in columns) for i in range(self._rows)),
            truncated=False,
        )
