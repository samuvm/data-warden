"""What a query would cost, computed before running it.

`docs/RULES.md §7`, error 11, rules out the obvious options: `EXPLAIN ANALYZE` runs
the query, so it is useless for a preventive guard; DuckDB's `EXPLAIN` gives
cardinality but not bytes scanned; Athena reports `DataScannedInBytes` only
afterwards. The estimator uses Iceberg metadata, which carries per-file
`column_sizes` and partition values, without reading a row, so it works for both
engines.

Two kinds of pruning, the two reasons a columnar lakehouse exists:

- Projection. Only the columns the query touches are charged, including those in
  `WHERE`, `JOIN` and `GROUP BY`: the engine reads them even if it does not return
  them.
- Partition. A predicate on the partition column reduces the files. It is read from
  the validated tree, not the text, so comments or odd casing do not change the count.

Rule for the whole module: when in doubt, overcharge. `G-COST-CALIB` requires
`p95(actual/estimated) <= 1.5` and zero cases with ratio > 3. Underestimating lets
an expensive query through; overestimating only makes someone narrow the question.
So an unreadable predicate does not prune, and an unknown table does not cost zero.
"""

from __future__ import annotations

from typing import Any, Final

from sqlglot import expressions as exp

from datawarden.catalog.statistics import Statistics, TableStats
from datawarden.domain.types import CostEstimate, ValidatedQuery

#: Assumed size of a table missing from the statistics. A deliberate penalty, not an
#: estimate: charging zero for the unknown is how expensive queries escape, and
#: `G-BUDGET-ESCAPE` is an axiom.
UNKNOWN_TABLE_BYTES: Final = 1_000_000_000


def estimate(query: ValidatedQuery, stats: Statistics) -> CostEstimate:
    """Bytes, rows and files the query would touch. Never executes anything."""
    tree = query.ast
    per_table: dict[str, dict[str, Any]] = {}
    unknown: list[str] = []
    total_bytes = 0
    total_rows = 0
    total_files = 0

    aliases = _alias_map(tree)
    for name in _tables_of(tree):
        table_stats = stats.table(name)
        columns = _columns_of(tree, name, aliases)
        if table_stats is None:
            unknown.append(name)
            total_bytes += UNKNOWN_TABLE_BYTES
            per_table[name] = {
                "known": False,
                "bytes": UNKNOWN_TABLE_BYTES,
                "columns": sorted(columns),
            }
            continue

        kept, kept_bytes, kept_rows, kept_files = _surviving_partitions(tree, table_stats)
        column_bytes = table_stats.bytes_of(tuple(sorted(columns)))
        share = kept_bytes / table_stats.bytes if table_stats.bytes else 1.0
        table_bytes = round(column_bytes * share)

        total_bytes += table_bytes
        total_rows += kept_rows
        total_files += kept_files
        per_table[name] = {
            "known": True,
            "bytes": table_bytes,
            "columns": sorted(columns),
            "column_bytes": column_bytes,
            "partitions_kept": kept,
            "partitions_total": len(table_stats.partitions) or 1,
            "rows": kept_rows,
            "files": kept_files,
        }

    return CostEstimate(
        estimated_bytes=total_bytes,
        estimated_rows=total_rows,
        files_scanned=total_files,
        method="iceberg",
        detail={"per_table": per_table, "unknown_tables": sorted(unknown)},
    )


def _tables_of(tree: exp.Expression) -> list[str]:
    """Real tables in the tree, excluding CTEs, which occupy no bytes on disk."""
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE) if c.alias_or_name}
    seen: list[str] = []
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if name and name not in cte_names and name not in seen:
            seen.append(name)
    return seen


def _alias_map(tree: exp.Expression) -> dict[str, str]:
    """Lowercase `alias -> real table` map.

    `qualify()` qualifies each column with the alias (`f.auth_status`), not the table
    name. Without this map, `FROM fact_payment_attempt AS f` attributes no columns and
    the whole table is charged: projection pruning would vanish whenever an alias is
    used.
    """
    out: dict[str, str] = {}
    for table in tree.find_all(exp.Table):
        real = table.name.lower()
        out[real] = real
        if table.alias:
            out[table.alias.lower()] = real
    return out


def _columns_of(tree: exp.Expression, table: str, aliases: dict[str, str]) -> set[str]:
    """Every column of the table that appears anywhere in the tree.

    Includes `WHERE`, `JOIN` and `GROUP BY` columns: the engine reads them even if it
    does not return them, and counting only the projection would underestimate
    exactly the queries with predicates. Qualification comes from `qualify()`, so no
    alias resolution happens here.
    """
    return {
        column.name.lower()
        for column in tree.find_all(exp.Column)
        if aliases.get((column.table or "").lower()) == table
    }


def _surviving_partitions(tree: exp.Expression, stats: TableStats) -> tuple[int, int, int, int]:
    """`(partitions, bytes, rows, files)` that survive pruning."""
    if stats.partition_column is None or not stats.partitions:
        return (1, stats.bytes, stats.rows, stats.files)

    keep = _partition_filter(tree, stats.partition_column, set(stats.partitions))
    if keep is None:
        return (len(stats.partitions), stats.bytes, stats.rows, stats.files)

    kept_bytes = sum(stats.partitions[v]["bytes"] for v in keep)
    kept_rows = sum(stats.partitions[v]["rows"] for v in keep)
    kept_files = sum(stats.partitions[v]["files"] for v in keep)
    return (len(keep), kept_bytes, kept_rows, kept_files)


def _partition_filter(tree: exp.Expression, column: str, values: set[str]) -> set[str] | None:
    """Surviving partition values, or `None` if they cannot be determined.

    `None` does not mean "none survive"; it means the estimator cannot narrow, so
    every partition is charged.

    Only `AND`-joined predicates in `WHERE` are considered. An `OR` with a branch that
    does not mention the partition column narrows nothing, and treating it as if it
    did would underestimate.
    """
    where = tree.find(exp.Where)
    if where is None:
        return None

    kept: set[str] | None = None
    for predicate in _conjuncts(where.this):
        narrowed = _values_from(predicate, column, values)
        if narrowed is None:
            continue
        kept = narrowed if kept is None else kept & narrowed

    # An empty intersection is not trusted. Partition keys were once generated as
    # `Record[19967]` instead of ISO dates, so no literal matched, pruning came out
    # empty and a 4.1 GB table was estimated at zero bytes; every date-filtered query
    # would have escaped the budget.
    #
    # A legitimately empty range (a day with no data) is overestimated as a result,
    # which only costs a narrower question; underestimating to zero costs the axiom.
    if kept is not None and not kept:
        return None
    return kept


def _conjuncts(node: exp.Expression) -> list[exp.Expression]:
    """`AND`-joined predicates, flattened. An `OR` is returned whole."""
    if isinstance(node, exp.And):
        return [*_conjuncts(node.this), *_conjuncts(node.expression)]
    if isinstance(node, exp.Paren):
        return _conjuncts(node.this)
    return [node]


def _values_from(predicate: exp.Expression, column: str, values: set[str]) -> set[str] | None:
    """Partition values this predicate lets through, if determinable."""
    if isinstance(predicate, exp.EQ):
        for left, right in (
            (predicate.this, predicate.expression),
            (predicate.expression, predicate.this),
        ):
            if _is_partition_column(left, column):
                literal = _literal_text(right)
                return None if literal is None else values & {literal}
        return None

    if isinstance(predicate, exp.In) and _is_partition_column(predicate.this, column):
        wanted = {
            text for item in predicate.expressions if (text := _literal_text(item)) is not None
        }
        return values & wanted if wanted else None

    if isinstance(predicate, exp.Between) and _is_partition_column(predicate.this, column):
        low = _literal_text(predicate.args.get("low"))
        high = _literal_text(predicate.args.get("high"))
        if low is None or high is None:
            return None
        return {v for v in values if low <= v <= high}

    if isinstance(predicate, exp.GT | exp.GTE | exp.LT | exp.LTE):
        return _values_from_range(predicate, column, values)

    return None


def _values_from_range(
    predicate: exp.Expression, column: str, values: set[str]
) -> set[str] | None:
    """`>=`, `>`, `<=`, `<` on the partition column."""
    left, right = predicate.this, predicate.expression
    if _is_partition_column(left, column):
        bound, inclusive, lower = (
            _literal_text(right),
            isinstance(predicate, exp.GTE | exp.LTE),
            isinstance(predicate, exp.GT | exp.GTE),
        )
    elif _is_partition_column(right, column):
        # `DATE '2026-08-03' <= event_date`: same predicate with sides swapped.
        bound, inclusive, lower = (
            _literal_text(left),
            isinstance(predicate, exp.GTE | exp.LTE),
            isinstance(predicate, exp.LT | exp.LTE),
        )
    else:
        return None
    if bound is None:
        return None
    if lower:
        return {v for v in values if (v >= bound if inclusive else v > bound)}
    return {v for v in values if (v <= bound if inclusive else v < bound)}


def _is_partition_column(node: exp.Expression | None, column: str) -> bool:
    """The bare column only. `date_trunc('month', event_date)` is not, and does not prune."""
    return isinstance(node, exp.Column) and node.name.lower() == column.lower()


def _literal_text(node: exp.Expression | None) -> str | None:
    """The literal as text, or `None` if it is not a comparable literal.

    Partitions in this warehouse are dates keyed by ISO strings, so text comparison
    equals date comparison. This assumption breaks for numeric partitions.
    """
    if isinstance(node, exp.Cast):
        return _literal_text(node.this)
    if isinstance(node, exp.Literal):
        return str(node.this)
    if isinstance(node, exp.Paren):
        return _literal_text(node.this)
    if isinstance(node, exp.DateStrToDate | exp.TsOrDsToDate | exp.StrToDate):
        return _literal_text(node.this)
    return None
