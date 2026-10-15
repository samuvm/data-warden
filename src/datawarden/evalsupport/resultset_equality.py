"""When two resultsets are "the same". Specification: docs/spec/resultset-equality.md.

This function turns `G-EXEC-ACC` into a reproducible number; without it, "execution
accuracy >= 0.80" means whatever each reader assumes.

Three deliberate non-goals:

- It never compares SQL. Two very different queries may produce the same result, and
  that is precisely what is being measured.
- It does not invent the shape of an empty resultset. An empty row list has no
  arity; when one must be compared, pass a `Table`, which does (spec P-2).
- It does not treat a timeout or a rejection as "zero rows". They affect the
  numerator and denominator differently.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Final

Row = tuple[Any, ...]

#: Tolerances from decision 4. A `sum()` over 66 M rows accumulates different
#: rounding depending on aggregation order, which the plan decides, not the query's
#: correctness.
REL_TOL: Final = 1e-9
ABS_TOL: Final = 1e-12

# Type classes from spec P-1. Text must not match number; INTEGER may match DOUBLE.
_NUMBER: Final = "number"
_BOOL: Final = "bool"
_TEXT: Final = "text"
_BYTES: Final = "bytes"
_NULL: Final = "null"
_DATE: Final = "date"
_DATETIME: Final = "datetime"
_TIME: Final = "time"
_OTHER: Final = "other"


class _Outcome:
    """An outcome that is not a set of rows.

    Timeouts and guard rejections are legitimate responses. Representing them as an
    empty list would make them indistinguishable from each other and from a query
    that returned nothing, yet the spec scores them differently.
    """

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        self._name = name

    def __repr__(self) -> str:
        return f"<{self._name}>"


#: The query did not finish. Counts as a failure and stays in the denominator.
TIMEOUT = _Outcome("timeout")
#: The guard rejected the query. Pass or fail depending on what the case expected.
REJECTED = _Outcome("rejected")


@dataclass(frozen=True, slots=True)
class Table:
    """A resultset that also carries its shape.

    Needed where a `list[tuple]` falls short: decision 8 compares the column count of
    two empty results, and `strict_names` needs the names. When shape is not in
    question, a plain list still works.
    """

    columns: tuple[str, ...]
    rows: Sequence[Row]


ResultSet = Sequence[Row] | Table | _Outcome


@dataclass(frozen=True, slots=True)
class Comparison:
    """The verdict and its reason.

    Without `reason`, sixty failing cases say nothing about how they differ.
    """

    equal: bool
    reason: str


@dataclass(frozen=True, slots=True)
class _Shape:
    """Normalized shape of one side of the comparison."""

    names: tuple[str, ...] | None
    n_cols: int | None
    rows: list[Row]


def compare(
    actual: ResultSet,
    expected: ResultSet,
    *,
    ordered: bool = False,
    strict_names: bool = False,
) -> Comparison:
    """Compare two resultsets per `docs/spec/resultset-equality.md`.

    Args:
        actual: Result of the generated query.
        expected: Result of the reference SQL.
        ordered: The reference has `ORDER BY`, so row order matters.
        strict_names: The question asked for a specific column name.

    Returns:
        The verdict, with one of the reasons declared in the spec.
    """
    # Two separate `if`s instead of an `or` so the type checker narrows `actual` and
    # `expected` to row resultsets below.
    if isinstance(expected, _Outcome):
        return _outcome_verdict(actual, expected)
    if isinstance(actual, _Outcome):
        return _outcome_verdict(actual, expected)

    left = _shape_of(actual)
    right = _shape_of(expected)
    if left is None or right is None:
        return Comparison(equal=False, reason="column_count")

    if left.n_cols is not None and right.n_cols is not None and left.n_cols != right.n_cols:
        return Comparison(equal=False, reason="column_count")
    if len(left.rows) != len(right.rows):
        return Comparison(equal=False, reason="row_count")

    if strict_names:
        aligned = _align_by_name(left, right)
        if aligned is None:
            return Comparison(equal=False, reason="column_names")
        left, right = aligned
    else:
        left = _align_by_content(left)
        right = _align_by_content(right)

    if not ordered:
        return _compare_rows(_sorted_rows(left.rows), _sorted_rows(right.rows))

    verdict = _compare_rows(left.rows, right.rows)
    if verdict.equal:
        return verdict
    # An ordered mismatch may be "same rows, different order", a very different
    # diagnosis from "a wrong cell", so it gets its own reason.
    if _compare_rows(_sorted_rows(left.rows), _sorted_rows(right.rows)).equal:
        return Comparison(equal=False, reason="row_order")
    return verdict


def _outcome_verdict(actual: ResultSet, expected: ResultSet) -> Comparison:
    """Verdict when either side is not a set of rows."""
    # P-4: timeout wins over everything; a query that does not finish is no answer.
    if actual is TIMEOUT or expected is TIMEOUT:
        return Comparison(equal=False, reason="timeout")
    # P-5: `expected_rejection` is the case's reason; `equal` says whether it happened.
    if expected is REJECTED:
        return Comparison(equal=actual is REJECTED, reason="expected_rejection")
    return Comparison(equal=False, reason="rejected")


def _shape_of(rs: Sequence[Row] | Table) -> _Shape | None:
    """Normalize any resultset form. `None` if rows are ragged."""
    if isinstance(rs, Table):
        rows = [tuple(r) for r in rs.rows]
        names: tuple[str, ...] | None = tuple(rs.columns)
        n_cols: int | None = len(rs.columns)
    else:
        rows = [tuple(r) for r in rs]
        names = None
        n_cols = len(rows[0]) if rows else None
    if any(len(r) != n_cols for r in rows):
        return None
    return _Shape(names=names, n_cols=n_cols, rows=rows)


def _align_by_name(left: _Shape, right: _Shape) -> tuple[_Shape, _Shape] | None:
    """Pair columns by name. `None` if names cannot be verified."""
    if left.names is None or right.names is None:
        return None
    if sorted(left.names) != sorted(right.names):
        return None
    return _reorder(left, _order_by(left.names)), _reorder(right, _order_by(right.names))


def _order_by(names: tuple[str, ...]) -> list[int]:
    return sorted(range(len(names)), key=lambda i: (names[i], i))


def _align_by_content(shape: _Shape) -> _Shape:
    """Canonicalize column order by content (spec P-3).

    Each column's key is its multiset of values, so it is independent of row order,
    and reordering permutes whole columns, so no row is split.
    """
    if shape.n_cols is None or shape.n_cols <= 1:
        return shape
    keys = [(sorted(_cell_key(row[i]) for row in shape.rows), i) for i in range(shape.n_cols)]
    return _reorder(shape, [i for _, i in sorted(keys)])


def _reorder(shape: _Shape, order: list[int]) -> _Shape:
    names = tuple(shape.names[i] for i in order) if shape.names is not None else None
    return _Shape(
        names=names,
        n_cols=shape.n_cols,
        rows=[tuple(row[i] for i in order) for row in shape.rows],
    )


def _sorted_rows(rows: list[Row]) -> list[Row]:
    return sorted(rows, key=lambda r: tuple(_cell_key(c) for c in r))


def _compare_rows(left: list[Row], right: list[Row]) -> Comparison:
    for lrow, rrow in zip(left, right, strict=True):
        for lcell, rcell in zip(lrow, rrow, strict=True):
            reason = _cell_mismatch(lcell, rcell)
            if reason is not None:
                return Comparison(equal=False, reason=reason)
    return Comparison(equal=True, reason="equal")


def _cell_mismatch(left: Any, right: Any) -> str | None:
    """Reason two cells differ, or `None` if they are equal."""
    lclass = _cell_class(left)
    rclass = _cell_class(right)
    if lclass != rclass:
        # P-6: NULL vs empty string gets its own reason (a semantic error); NULL vs
        # a number is simply a different class.
        if {lclass, rclass} == {_NULL, _TEXT} and "" in (left, right):
            return "null_vs_empty"
        return "cell_type"
    if lclass == _NULL:
        return None
    if lclass == _NUMBER:
        return None if _numbers_close(left, right) else "cell_value"
    if lclass == _DATETIME:
        return None if _normalize_temporal(left) == _normalize_temporal(right) else "cell_value"
    if lclass == _OTHER and type(left) is not type(right):
        return "cell_type"
    return None if _safe_eq(left, right) else "cell_value"


def _cell_class(value: Any) -> str:
    if value is None:
        return _NULL
    if isinstance(value, bool):
        return _BOOL
    if isinstance(value, int | float | Decimal):
        return _NUMBER
    if isinstance(value, str):
        return _TEXT
    if isinstance(value, bytes | bytearray | memoryview):
        return _BYTES
    if isinstance(value, dt.datetime):
        return _DATETIME
    if isinstance(value, dt.date):
        return _DATE
    if isinstance(value, dt.time):
        return _TIME
    return _OTHER


def _numbers_close(left: Any, right: Any) -> bool:
    """Decision 4, plus the edge case tolerance alone misses.

    `float(Decimal("1e400"))` does not raise; it returns `inf`, and
    `isclose(inf, inf)` is true, so two different amounts beyond `float` range would
    compare equal. Non-finite values are therefore compared in their original type.
    """
    lf = _as_float(left)
    rf = _as_float(right)
    if lf is None or rf is None or not (math.isfinite(lf) and math.isfinite(rf)):
        if _is_nan(left) and _is_nan(right):
            # Two NaNs are the same answer even though SQL says NaN != NaN: this
            # compares structure, it does not evaluate a predicate.
            return True
        return _safe_eq(left, right)
    return math.isclose(lf, rf, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def _safe_eq(left: Any, right: Any) -> bool:
    """Equality that cannot crash the whole comparison.

    `Decimal("sNaN") == 1` raises `InvalidOperation`. Losing the accuracy metric to a
    single cell is worse than declaring it different, so incomparable values are
    declared different.
    """
    try:
        return bool(left == right)
    except (ArithmeticError, TypeError, ValueError):
        return False


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (OverflowError, ValueError, InvalidOperation):
        return None


def _is_nan(value: Any) -> bool:
    as_float = _as_float(value)
    return as_float is not None and math.isnan(as_float)


def _normalize_temporal(value: dt.datetime) -> dt.datetime:
    """Decision 10: a naive `TIMESTAMP` is UTC; an aware one is converted to UTC.

    Takes only `datetime`: a `DATE` is a civil date and is not converted, and a
    `TIME` is compared as-is.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def _cell_key(value: Any) -> tuple[str, str]:
    """Total, deterministic sort key for rows and columns.

    Numbers are rounded to 12 significant digits before forming the key; otherwise
    `0.1 + 0.2` and `0.3` would sort apart and the multiset comparison would pair the
    wrong rows in exactly the case decision 4's tolerance exists for.
    """
    cls = _cell_class(value)
    if cls == _NULL:
        return (cls, "")
    if cls == _NUMBER:
        as_float = _as_float(value)
        if as_float is None or not math.isfinite(as_float):
            return (cls, str(value))
        return (cls, f"{as_float:.12g}")
    if cls == _DATETIME:
        return (cls, str(_normalize_temporal(value)))
    if cls in (_DATE, _TIME):
        return (cls, str(value))
    if cls == _TEXT:
        return (cls, str(value))
    return (cls, repr(value))
