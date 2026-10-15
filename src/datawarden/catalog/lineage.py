"""Column-level lineage of every view, resolved once at catalog build time.

The signed policy protects `dim_customer.birth_date`, but the warehouse also has a
view `v_customer` that re-exposes that column under another table name, plus
`full_name = concat_ws(' ', first_name, last_name_1, last_name_2)`. That is the
derived-expression attack described in `docs/DESIGN.md`, served by the catalog
itself; a policy matching on `table.column` sees none of it.

Lineage is resolved at catalog time because it only changes when a view changes,
which already forces a catalog regeneration (`G-CATALOG-FRESH`). Per query it would
mean parsing 24 view definitions within a 25 ms budget.

Fail-closed: a column whose lineage cannot be resolved is marked unknown and
treated as the most restrictive column there is.
"""

from __future__ import annotations

from collections.abc import Mapping

import sqlglot
from sqlglot import expressions as exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, build_scope

#: Unresolvable-lineage marker. The guard treats it as "may come from anything"
#: and applies the most restrictive level.
UNKNOWN = "?"

#: Nesting cap when descending through subqueries and CTEs, so a malformed view
#: cannot hang the build.
_MAX_DEPTH = 12


def dependencies(sql: str, relations: frozenset[str], dialect: str) -> frozenset[str]:
    """Catalog relations referenced by this view definition.

    Only table names count: a file reader such as `read_parquet` is not a catalog
    relation, which is what distinguishes a base table from a derived view without
    relying on naming conventions.
    """
    try:
        tree = sqlglot.parse_one(sql, dialect=dialect)
    except sqlglot.ParseError:
        return frozenset()
    # The view itself appears as a table in its own `CREATE VIEW`. Excluding it
    # here keeps it from depending on itself and ending up last in topological
    # order with spuriously unknown lineage.
    created = {
        node.this.name.lower()
        for node in tree.find_all(exp.Create)
        if isinstance(node.this, exp.Table)
    }
    created |= {
        node.this.this.name.lower()
        for node in tree.find_all(exp.Create)
        if isinstance(node.this, exp.Schema) and isinstance(node.this.this, exp.Table)
    }
    return frozenset(
        t.name.lower()
        for t in tree.find_all(exp.Table)
        if t.name.lower() in relations and t.name.lower() not in created
    )


def topological_order(graph: Mapping[str, frozenset[str]]) -> list[str]:
    """Relations ordered from base to derived.

    A cycle does not raise: unorderable relations go last and their lineage ends up
    unknown, which is the safe answer.
    """
    ordered: list[str] = []
    placed: set[str] = set()
    pending = dict(graph)
    while pending:
        ready = sorted(name for name, deps in pending.items() if all(d in placed for d in deps))
        if not ready:
            ordered.extend(sorted(pending))
            break
        for name in ready:
            ordered.append(name)
            placed.add(name)
            del pending[name]
    return ordered


def resolve(
    columns_by_relation: Mapping[str, tuple[str, ...]],
    view_sql: Mapping[str, str],
    *,
    dialect: str,
) -> dict[str, tuple[str, ...]]:
    """`relation.column` -> the `base_table.column` refs it derives from.

    A base-table column derives from itself. A view column derives from the union
    of the lineage of every column its expression references, so
    `v_customer.full_name` derives from `first_name`, `last_name_1` and
    `last_name_2`, and is restricted if any one of them is.
    """
    relations = frozenset(columns_by_relation)
    graph = {
        name: (
            dependencies(view_sql[name], relations - {name}, dialect)
            if name in view_sql
            else frozenset()
        )
        for name in relations
    }
    schema: dict[str, object] = {
        name: dict.fromkeys(cols, "UNKNOWN") for name, cols in columns_by_relation.items()
    }

    lineage: dict[str, tuple[str, ...]] = {}
    for relation in topological_order(graph):
        if not graph[relation] or relation not in view_sql:
            # Base table: each column derives from itself.
            for column in columns_by_relation[relation]:
                lineage[f"{relation}.{column}"] = (f"{relation}.{column}",)
            continue
        resolved = _resolve_view(relation, view_sql[relation], schema, lineage, dialect)
        fallback = _closure_columns(relation, graph, columns_by_relation)
        for column in columns_by_relation[relation]:
            sources = resolved.get(column.lower(), (UNKNOWN,))
            if UNKNOWN in sources:
                # Targeted fail-closed. A column sqlglot cannot trace (in practice
                # a recursive CTE) may come from any column of the relations the
                # view uses, so all of them are attributed. Stricter than the truth,
                # looser than a blanket deny: the view stays queryable by whoever
                # can access everything underneath.
                sources = tuple(sorted(set(sources) - {UNKNOWN} | set(fallback)))
            lineage[f"{relation}.{column}"] = sources
    return lineage


def unresolved(
    columns_by_relation: Mapping[str, tuple[str, ...]],
    view_sql: Mapping[str, str],
    *,
    dialect: str,
) -> tuple[str, ...]:
    """Columns whose lineage could not be traced.

    Reported in `docs/JOURNAL.md` and the threat model so the limitation is counted.
    """
    relations = frozenset(columns_by_relation)
    graph = {
        name: (
            dependencies(view_sql[name], relations - {name}, dialect)
            if name in view_sql
            else frozenset()
        )
        for name in relations
    }
    schema: dict[str, object] = {
        name: dict.fromkeys(cols, "UNKNOWN") for name, cols in columns_by_relation.items()
    }
    lineage: dict[str, tuple[str, ...]] = {}
    out: list[str] = []
    for relation in topological_order(graph):
        if not graph[relation] or relation not in view_sql:
            for column in columns_by_relation[relation]:
                lineage[f"{relation}.{column}"] = (f"{relation}.{column}",)
            continue
        resolved = _resolve_view(relation, view_sql[relation], schema, lineage, dialect)
        for column in columns_by_relation[relation]:
            sources = resolved.get(column.lower(), (UNKNOWN,))
            if UNKNOWN in sources:
                out.append(f"{relation}.{column}")
            lineage[f"{relation}.{column}"] = sources
    return tuple(out)


def _closure_columns(
    relation: str,
    graph: Mapping[str, frozenset[str]],
    columns_by_relation: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
    """All columns of the relations this one depends on, transitively."""
    seen: set[str] = set()
    stack = list(graph.get(relation, ()))
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(graph.get(current, ()))
    return tuple(
        sorted(f"{rel}.{col}" for rel in seen for col in columns_by_relation.get(rel, ()))
    )


def _resolve_view(
    relation: str,
    sql: str,
    schema: Mapping[str, object],
    lineage: Mapping[str, tuple[str, ...]],
    dialect: str,
) -> dict[str, tuple[str, ...]]:
    """Column lineage of a single view, or `{}` if it cannot be resolved.

    Uses sqlglot's `Scope` instead of an alias map: `v_attempt_dedup` projects from
    a subquery with `row_number()`, where the alias points to no table. In the scope
    tree every source is either a table or a child scope, and lineage is resolved by
    descending until tables are reached.
    """
    try:
        tree = qualify(
            sqlglot.parse_one(sql, dialect=dialect),
            schema=dict(schema),
            dialect=dialect,
        )
        root = build_scope(tree)
    except Exception:
        return {}
    if root is None:
        return {}

    select = root.expression
    if not isinstance(select, exp.Select):
        return {}

    out: dict[str, tuple[str, ...]] = {}
    for projection in select.expressions:
        name = projection.alias_or_name.lower()
        if not name or name == "*":
            continue
        sources = _sources_of(projection, root, lineage, depth=0)
        out[name] = tuple(sorted(sources)) if sources else (f"{relation}.{name}",)
    return out


def _sources_of(
    node: exp.Expression,
    scope: Scope,
    lineage: Mapping[str, tuple[str, ...]],
    *,
    depth: int,
) -> set[str]:
    """Base columns an expression depends on, descending through scopes.

    `depth` is a hard cap: a malformed view or a CTE cycle would otherwise recurse
    forever. When exhausted the result is unknown, which is the safe answer.
    """
    if depth > _MAX_DEPTH:
        return {UNKNOWN}
    sources: set[str] = set()
    for column in node.find_all(exp.Column):
        sources |= _column_sources(column, scope, lineage, depth=depth)
    return sources


def _column_sources(
    column: exp.Column,
    scope: Scope,
    lineage: Mapping[str, tuple[str, ...]],
    *,
    depth: int,
) -> set[str]:
    source = scope.sources.get(column.table) or scope.sources.get(column.table.lower())
    if source is None:
        return {UNKNOWN}
    if isinstance(source, exp.Table):
        key = f"{source.name.lower()}.{column.name.lower()}"
        return set(lineage.get(key, (UNKNOWN,)))
    inner = source
    if not isinstance(inner.expression, exp.Select):
        return {UNKNOWN}
    for projection in inner.expression.expressions:
        if projection.alias_or_name.lower() == column.name.lower():
            return _sources_of(projection, inner, lineage, depth=depth + 1)
    return {UNKNOWN}
