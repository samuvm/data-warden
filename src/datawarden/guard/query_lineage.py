"""Base columns behind each column of this query, including query-local aliases.

    WITH c AS (SELECT birth_date AS b, customer_sk FROM dim_customer)
    SELECT customer_sk FROM c WHERE b > '1990-01-01'

`birth_date` is masked for `analyst`, and a masked column is forbidden in a `WHERE`, but
the `WHERE` says `b`. Catalog lineage cannot help, because `c` is not a catalog
relation: the query itself just defined it. Without this module R008 would look at
`c.b`, find no policy, apply the default `allow` and accept, leaving the predicate side
channel open with two lines of SQL.

The approach mirrors `catalog/lineage.py`: walk sqlglot's scope tree down to real
tables and join there with the catalog lineage. An alias does not change which column a
value comes from, whether a view introduces it or the query does.

Fail-closed: a column whose origin cannot be traced is marked `UNKNOWN`, and R008
treats unknown as the most restrictive case.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from sqlglot import expressions as exp
from sqlglot.optimizer.scope import Scope, build_scope

#: Unresolvable origin. R008 treats it as the most restrictive position.
UNKNOWN: Final = "?"

_MAX_DEPTH: Final = 12


def resolve(
    tree: exp.Expression, catalog_lineage: Mapping[str, tuple[str, ...]]
) -> dict[int, tuple[str, ...]]:
    """`id(Column node)` -> catalog base columns it derives from.

    Indexed by node identity rather than `table.column` because two columns with the
    same name in different scopes are different things, and conflating them is exactly
    the error this module exists to avoid.
    """
    try:
        root = build_scope(tree)
    except Exception:
        return {}
    if root is None:
        return {}

    out: dict[int, tuple[str, ...]] = {}
    for scope in root.traverse():
        for column in scope.columns:
            out[id(column)] = tuple(sorted(_sources(column, scope, catalog_lineage, 0)))
    _index_alias_references(root, out)
    return out


def _index_alias_references(root: Scope, out: dict[int, tuple[str, ...]]) -> None:
    """Index the columns that `Scope.columns` does not return.

    `SELECT first_name AS n FROM dim_customer ORDER BY n LIMIT 5` must be rejected for
    `analyst`, since `first_name` is masked for that role. Three sqlglot behaviours
    combine to hide it:

    1. `qualify()` expands output aliases in `GROUP BY` and `HAVING` but not in
       `ORDER BY`, where it leaves a column without a table. It also converts ordinals,
       so `ORDER BY 1` ends up in the same place.
    2. `Scope.columns` deliberately excludes references to output aliases, so the main
       loop never indexes that node.
    3. R008 does see it (it walks `exp.Column`), looks up `.n`, finds no policy row and
       applies the default `allow`.

    `ORDER BY n LIMIT 1` combined with predicates on allowed columns is a binary search
    over a masked value, and the policy explicitly forbids a column reached through an
    alias.

    This closes the class, not the case. Every column the main loop did not index is
    resolved here or marked `UNKNOWN`: if its name matches an output alias of its
    `SELECT`, it inherits that projection's lineage; otherwise its origin is unknown,
    which is fail-closed by definition. Fixing only `ORDER BY` would leave open the next
    clause sqlglot chooses not to expand.
    """
    for scope in root.traverse():
        select = scope.expression
        if not isinstance(select, exp.Select):
            continue
        by_alias: dict[str, tuple[str, ...]] = {}
        for projection in select.expressions:
            sources: set[str] = set()
            for inner in projection.find_all(exp.Column):
                sources |= set(out.get(id(inner), (UNKNOWN,)))
            by_alias[projection.alias_or_name.lower()] = tuple(sorted(sources))
        for column in select.find_all(exp.Column):
            if id(column) in out or column.table:
                continue
            out[id(column)] = by_alias.get(column.name.lower(), (UNKNOWN,))


def _sources(
    column: exp.Column,
    scope: Scope,
    catalog_lineage: Mapping[str, tuple[str, ...]],
    depth: int,
) -> set[str]:
    if depth > _MAX_DEPTH:
        return {UNKNOWN}
    table = (column.table or "").lower()
    source = scope.sources.get(column.table) or scope.sources.get(table)
    if source is None:
        # A column with no known source in its scope. Rare after `qualify()`, and in a
        # guard the rare case is treated as the worst case.
        return {UNKNOWN}
    if isinstance(source, exp.Table):
        key = f"{source.name.lower()}.{column.name.lower()}"
        return set(catalog_lineage.get(key, (key,)))
    inner = source
    if not isinstance(inner.expression, exp.Select):
        return {UNKNOWN}
    for projection in inner.expression.expressions:
        if projection.alias_or_name.lower() != column.name.lower():
            continue
        found: set[str] = set()
        for inner_column in projection.find_all(exp.Column):
            found |= _sources(inner_column, inner, catalog_lineage, depth + 1)
        # An empty set is not unknown. `count(*)` or a literal derive from no column, so
        # there is no policy to apply. Returning `UNKNOWN` here would reject an
        # ordinary CTE that projects a count.
        return found
    return {UNKNOWN}
