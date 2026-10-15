"""Bytes and rows per table, column and partition, read from Iceberg metadata.

Kept out of `schema.json` because these figures change when data is regenerated and
the schema does not; mixing them would tie the catalog sha to row counts and void
`G-CATALOG-FRESH`.

Sourced from Iceberg rather than `EXPLAIN` (`docs/RULES.md` section 7, error 11):
`EXPLAIN ANALYZE` executes the query, DuckDB's `EXPLAIN` gives cardinality but not
bytes scanned, and Athena reports `DataScannedInBytes` only after execution. Iceberg
manifests carry per-file `column_sizes` and partition values without reading a row,
so the same estimator works for both engines.

Stored:

- bytes per column at table level, for projection pruning;
- bytes, rows and files per partition value, for predicate pruning.

Bytes per column and partition are not stored: that would be 730 partitions times
36 columns for `fact_payment_attempt` alone, and the proportional model (column
fraction times partition fraction) gives the same order of magnitude. Whether that
suffices is measured by `G-COST-CALIB`.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class TableStats:
    """What is known about a table without reading a row."""

    name: str
    rows: int
    bytes: int
    files: int
    #: `column` -> bytes it occupies across the whole table.
    column_bytes: dict[str, int] = field(default_factory=dict)
    #: The partition column, if the table is partitioned.
    partition_column: str | None = None
    #: `partition value` -> `{rows, bytes, files}`.
    partitions: dict[str, dict[str, int]] = field(default_factory=dict)

    def bytes_of(self, columns: tuple[str, ...]) -> int:
        """Bytes of those columns; with no columns, the whole table.

        No columns happens with `count(*)`: nothing is projected but the engine
        still opens the files. Charging zero there would make `G-BUDGET-ESCAPE`
        trivially true for every aggregate query.
        """
        if not columns:
            return self.bytes
        known = [self.column_bytes.get(c.lower(), 0) for c in columns]
        return sum(known) or self.bytes


@dataclass(frozen=True, slots=True)
class Statistics:
    """Statistics for the whole warehouse, with their provenance."""

    profile: str
    source: str
    tables: dict[str, TableStats] = field(default_factory=dict)

    def table(self, name: str) -> TableStats | None:
        return self.tables.get(name.lower())

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "profile": self.profile,
            "source": self.source,
            "tables": {
                name: {
                    "rows": t.rows,
                    "bytes": t.bytes,
                    "files": t.files,
                    "column_bytes": dict(sorted(t.column_bytes.items())),
                    "partition_column": t.partition_column,
                    "partitions": dict(sorted(t.partitions.items())),
                }
                for name, t in sorted(self.tables.items())
            },
        }


def from_dict(payload: dict[str, Any]) -> Statistics:
    return Statistics(
        profile=payload["profile"],
        source=payload["source"],
        tables={
            name: TableStats(
                name=name,
                rows=int(spec["rows"]),
                bytes=int(spec["bytes"]),
                files=int(spec["files"]),
                column_bytes={k: int(v) for k, v in spec.get("column_bytes", {}).items()},
                partition_column=spec.get("partition_column"),
                partitions={
                    k: {kk: int(vv) for kk, vv in v.items()}
                    for k, v in spec.get("partitions", {}).items()
                },
            )
            for name, spec in payload["tables"].items()
        },
    )


def load(path: pathlib.Path) -> Statistics:
    return from_dict(json.loads(path.read_text(encoding="utf-8")))


def to_json(stats: Statistics) -> str:
    return json.dumps(stats.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def build_from_iceberg(iceberg_root: pathlib.Path, profile: str) -> Statistics:
    """Read Iceberg manifests without opening any data file.

    This is what makes a preventive estimator viable: counting 66.6 M rows from the
    manifest takes 0.02 s; scanning them takes minutes.
    """
    from pyiceberg.catalog.sql import SqlCatalog

    root = iceberg_root.resolve()
    catalog = SqlCatalog(
        "cierzo",
        **{"uri": f"sqlite:///{root}/catalog.db", "warehouse": f"file://{root}"},
    )

    tables: dict[str, TableStats] = {}
    for identifier in catalog.list_tables("cierzo"):
        name = identifier[-1].lower()
        table = catalog.load_table(identifier)
        by_id = {f.field_id: f.name.lower() for f in table.schema().fields}
        partition_fields = table.spec().fields
        partition_column = (
            by_id.get(partition_fields[0].source_id) if partition_fields else None
        )

        rows = 0
        total_bytes = 0
        files = 0
        column_bytes: dict[str, int] = {}
        partitions: dict[str, dict[str, int]] = {}

        for task in table.scan().plan_files():
            data_file = task.file
            files += 1
            rows += data_file.record_count
            total_bytes += data_file.file_size_in_bytes
            for field_id, size in (data_file.column_sizes or {}).items():
                column = by_id.get(field_id)
                if column is not None:
                    column_bytes[column] = column_bytes.get(column, 0) + size
            if partition_column is not None:
                value = _partition_value(
                    data_file.partition, str(partition_fields[0].transform)
                )
                bucket = partitions.setdefault(value, {"rows": 0, "bytes": 0, "files": 0})
                bucket["rows"] += data_file.record_count
                bucket["bytes"] += data_file.file_size_in_bytes
                bucket["files"] += 1

        tables[name] = TableStats(
            name=name,
            rows=rows,
            bytes=total_bytes,
            files=files,
            column_bytes=column_bytes,
            partition_column=partition_column,
            partitions=partitions,
        )

    return Statistics(
        profile=profile,
        source=f"iceberg · {root.name} · {len(tables)} tablas",
        tables=tables,
    )


def _partition_value(partition: Any, transform: str) -> str:
    """The partition value as ISO text; the key of the pruning index.

    pyiceberg's `Record` has a `repr` like `Record[19967]`. Using it as the key once
    produced partitions no date literal could match, so pruning returned the empty
    set and the estimator charged zero bytes for the 4.1 GB table, which would let
    any date-filtered query through `G-BUDGET-ESCAPE`. `G-COST-CALIB` caught it
    (`partitions_kept: 0`).

    An `identity` partition on a `DATE` arrives as days since the epoch and is
    converted; anything else falls back to its text, and `_partition_filter` does
    not prune on keys it does not understand.
    """
    values = tuple(partition)
    if not values:
        return ""
    raw = values[0]
    if transform == "identity" and isinstance(raw, int) and not isinstance(raw, bool):
        return (dt.date(1970, 1, 1) + dt.timedelta(days=raw)).isoformat()
    if isinstance(raw, dt.date):
        return raw.isoformat()
    return str(raw)


def derive_views(stats: Statistics, schema: Any) -> Statistics:
    """Statistics for views, derived from the published lineage.

    Iceberg manifests exist only for the 24 physical tables, but the catalog
    publishes 32 relations. Without this, the 8 views were unknown tables charged
    `UNKNOWN_TABLE_BYTES` (1 GB), above the `analyst` hard budget (600 MB), so the
    views the glossary prescribes (`v_payment_intent`, `v_attempt_dedup`) were
    unreachable for the main role: 1 GB charged for a 7.5 MB read.

    Overestimating costs an extra rejection; underestimating lets an expensive query
    through `G-BUDGET-ESCAPE`. Everything uncertain therefore rounds up:

    - `bytes` and `files`: the sum over bases. A view reads no less than its bases.
    - `rows`: the max over bases. Dedup and group-by only remove rows.
    - `column_bytes`: the sum of the source columns of each column, which is what
      the engine opens to produce it.
    - `partitions`: inherited only when the view has a single partitioned base and
      keeps its partition column with identity lineage, the only case where a
      predicate on the view prunes the same files. Otherwise no pruning.

    A relation without lineage stays unknown and keeps the penalty.
    """
    derived: dict[str, TableStats] = {}
    for table in schema.tables:
        name = table.name.lower()
        if name in stats.tables or table.kind != "view":
            continue

        bases: dict[str, TableStats] = {}
        column_bytes: dict[str, int] = {}
        for column in table.columns:
            total = 0
            for origin in column.derives_from or ():
                base_name, _, base_column = origin.partition(".")
                base = stats.table(base_name)
                if base is None or base_name.lower() == name:
                    continue
                bases[base_name.lower()] = base
                total += base.column_bytes.get(base_column.lower(), 0)
            if total:
                column_bytes[column.name.lower()] = total
        if not bases:
            continue

        derived[name] = TableStats(
            name=name,
            rows=max(b.rows for b in bases.values()),
            bytes=sum(b.bytes for b in bases.values()),
            files=sum(b.files for b in bases.values()),
            column_bytes=column_bytes,
            **_inherited_partitions(table, bases),
        )

    if not derived:
        return stats
    return Statistics(
        profile=stats.profile,
        source=f"{stats.source} + {len(derived)} vistas por linaje",
        tables={**stats.tables, **derived},
    )


def _inherited_partitions(table: Any, bases: dict[str, TableStats]) -> dict[str, Any]:
    """The base's pruning index, only if the view preserves it intact.

    Requires a single partitioned base and a view column whose lineage is exactly
    that partition column. An aggregated (`min(event_ts)`) or composite column does
    not qualify: a predicate on it does not prune the same files.
    """
    if len(bases) != 1:
        return {"partition_column": None, "partitions": {}}
    base = next(iter(bases.values()))
    if base.partition_column is None:
        return {"partition_column": None, "partitions": {}}

    wanted = f"{base.name}.{base.partition_column}"
    for column in table.columns:
        if tuple(o.lower() for o in (column.derives_from or ())) == (wanted,):
            return {
                "partition_column": column.name.lower(),
                "partitions": {k: dict(v) for k, v in base.partitions.items()},
            }
    return {"partition_column": None, "partitions": {}}
