"""Where a column sits in the tree, which decides what the policy allows.

As the header of `docs/spec/policy.yaml` states, the three access levels are not a
trust scale but a scale of position in the tree. A `mask` column is legal in the
projection and forbidden in `WHERE`, and that asymmetry closes the predicate side
channel:

    SELECT count(*) FROM dim_customer WHERE birth_date BETWEEN '1985-01-01' AND '1985-12-31'

returns no birth date, yet reconstructs it one question at a time. The range 1930-2010
spans 29,220 days, so fifteen queries with different literals pin down a person's exact
date. A mask hides the value, not the answer; rejecting the position does.

R008 and R012 both need this, hence a separate module: two implementations of "where is
this column" would be two criteria for the same question.
"""

from __future__ import annotations

from sqlglot import expressions as exp

from datawarden.domain.types import Position


def position_of(column: exp.Expression) -> Position:
    """Position of a column, found by walking up its ancestors.

    The order of the checks is the semantics. A column inside a function inside a
    `WHERE` is in the `WHERE`: both positions are forbidden for a masked column, but the
    message must name the clause, since that is what the caller can change.

    A column inside a function in the projection is not "in the projection": it is a
    function argument, a distinct and forbidden position. `SELECT substr(national_id,
    1, 3)` returns three digits of a national ID without the column appearing bare.
    """
    node = column.parent
    inside_function = False
    while node is not None:
        if isinstance(node, exp.Where):
            return Position.WHERE
        if isinstance(node, exp.Having):
            return Position.HAVING
        if isinstance(node, exp.Qualify):
            return Position.QUALIFY
        if isinstance(node, exp.Group):
            return Position.GROUP_BY
        if isinstance(node, exp.Join):
            return Position.JOIN_ON
        if isinstance(node, exp.Window):
            return _window_position(column, node)
        if isinstance(node, exp.Order):
            # An `ORDER BY` inside a window orders the window, not the result, and is
            # handled by `_window_position`. Only the query-level one reaches here.
            return Position.ORDER_BY
        if isinstance(node, exp.Func):
            inside_function = True
        node = node.parent
    return Position.FUNCTION_ARGUMENT if inside_function else Position.PROJECTION


def _window_position(column: exp.Expression, window: exp.Window) -> Position:
    """Inside a window, `PARTITION BY` is the dangerous position.

    Partitioning by a sensitive column groups by it: it is a `GROUP BY` under another
    name and exposes the column through cardinality in the same way. Ordering by it
    inside the window also reveals its order, so it is treated as restrictively, and the
    position reported says which of the two it was.
    """
    for part in window.args.get("partition_by") or []:
        if column is part or _contains(part, column):
            return Position.WINDOW_PARTITION
    return Position.ORDER_BY


def _contains(haystack: exp.Expression, needle: exp.Expression) -> bool:
    return any(node is needle for node in haystack.walk())


def table_and_column(column: exp.Column) -> tuple[str, str]:
    """`(table, column)`, lowercased. After `qualify()` the table is always set.

    If it is not (a column `qualify()` could not resolve), an empty string is returned
    and R008 decides what to do with it. No table is invented here: inventing one would
    assign the column a policy that does not apply to it.
    """
    return (column.table or "").lower(), column.name.lower()
