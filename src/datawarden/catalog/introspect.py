"""The catalog is generated from the live schema, never hand-written (I-07).

Required by `docs/DESIGN.md` section 2 and made checkable by `G-CATALOG-FRESH`: zero
divergence between the sha of `catalog/generated/schema.json` and one recomputed
against the engine.

The module is split on purpose. `build_schema()` is pure and knows nothing about
DuckDB; `introspect_duckdb()` is the only part with I/O. Invariant I-13 keeps
`tests/unit` off disk, network and DuckDB, so unit tests run against an in-memory
fixture schema and only integration tests start the engine.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from datawarden.catalog.lineage import resolve as resolve_lineage
from datawarden.catalog.lineage import unresolved as unresolved_lineage_columns
from datawarden.catalog.types import (
    FAMILY_BLOB,
    FAMILY_BOOLEAN,
    FAMILY_DATE,
    FAMILY_DECIMAL,
    FAMILY_FLOAT,
    FAMILY_INTEGER,
    FAMILY_OTHER,
    FAMILY_TEXT,
    FAMILY_TIME,
    FAMILY_TIMESTAMP,
    KIND_TABLE,
    KIND_VIEW,
    CatalogSchema,
    ColumnSpec,
    TableSpec,
)

#: Engine type prefixes, most specific first. Order matters: `TIMESTAMP` starts
#: with `TIME`, and matching `TIME` first would assign the wrong family silently.
_FAMILY_BY_PREFIX: tuple[tuple[str, str], ...] = (
    ("TIMESTAMP", FAMILY_TIMESTAMP),
    ("DATETIME", FAMILY_TIMESTAMP),
    ("TIME", FAMILY_TIME),
    ("DATE", FAMILY_DATE),
    ("BOOL", FAMILY_BOOLEAN),
    ("DECIMAL", FAMILY_DECIMAL),
    ("NUMERIC", FAMILY_DECIMAL),
    ("DOUBLE", FAMILY_FLOAT),
    ("FLOAT", FAMILY_FLOAT),
    ("REAL", FAMILY_FLOAT),
    ("TINYINT", FAMILY_INTEGER),
    ("SMALLINT", FAMILY_INTEGER),
    ("BIGINT", FAMILY_INTEGER),
    ("HUGEINT", FAMILY_INTEGER),
    ("INTEGER", FAMILY_INTEGER),
    ("INT", FAMILY_INTEGER),
    ("UTINYINT", FAMILY_INTEGER),
    ("USMALLINT", FAMILY_INTEGER),
    ("UINTEGER", FAMILY_INTEGER),
    ("UBIGINT", FAMILY_INTEGER),
    ("VARCHAR", FAMILY_TEXT),
    ("CHAR", FAMILY_TEXT),
    ("TEXT", FAMILY_TEXT),
    ("STRING", FAMILY_TEXT),
    ("UUID", FAMILY_TEXT),
    ("BLOB", FAMILY_BLOB),
    ("BYTEA", FAMILY_BLOB),
    ("VARBINARY", FAMILY_BLOB),
)


@dataclass(frozen=True, slots=True)
class ColumnRow:
    """An engine-agnostic row of `information_schema.columns`."""

    table_name: str
    table_kind: str
    column_name: str
    data_type: str
    is_nullable: bool
    ordinal: int


def family_of(engine_type: str) -> str:
    """Canonical family of an engine type.

    The guard and the cost estimator reason over families, not engine type names:
    `VARCHAR` and `string` are the same type, and a rule comparing engine names
    would break when another engine (e.g. Athena) is added.
    """
    upper = engine_type.strip().upper()
    for prefix, family in _FAMILY_BY_PREFIX:
        if upper.startswith(prefix):
            return family
    return FAMILY_OTHER


def build_schema(
    rows: Iterable[ColumnRow],
    *,
    dialect: str,
    excluded_columns: Iterable[str] = (),
    deprecated_columns: Mapping[str, str] = {},
    view_sql: Mapping[str, str] = {},
) -> CatalogSchema:
    """Build the schema. Pure: no disk, network or engine.

    Args:
        rows: Live schema columns, in any order.
        dialect: sqlglot dialect the AST will be re-serialized with.
        excluded_columns: `table.column` refs not published in ring 1, from
            `docs/spec/policy.yaml`.
        deprecated_columns: `table.column` -> reason, from
            `docs/spec/catalog-overlay.yaml`.
        view_sql: `view` -> its SQL definition. Without it there is no lineage, and
            the policy can be bypassed through a view: `v_customer.birth_date` is
            `dim_customer.birth_date` under another name.

    Returns:
        The schema with tables and columns in deterministic order. The generated
        file is compared by sha256, so engine-dependent ordering would fail
        `G-CATALOG-FRESH` on a DuckDB upgrade with an unchanged schema.
    """
    excluded = {c.strip().lower() for c in excluded_columns}
    deprecated = {k.strip().lower(): v for k, v in deprecated_columns.items()}

    by_table: dict[str, list[ColumnRow]] = {}
    kinds: dict[str, str] = {}
    for row in rows:
        by_table.setdefault(row.table_name, []).append(row)
        kinds[row.table_name] = row.table_kind

    columns_by_relation = {
        name: tuple(r.column_name for r in sorted(rs, key=lambda r: r.ordinal))
        for name, rs in by_table.items()
    }
    lineage = resolve_lineage(columns_by_relation, view_sql, dialect=dialect)
    unresolved_lineage = frozenset(
        unresolved_lineage_columns(columns_by_relation, view_sql, dialect=dialect)
    )

    tables: list[TableSpec] = []
    for table_name in sorted(by_table):
        columns = tuple(
            ColumnSpec(
                name=row.column_name,
                engine_type=row.data_type,
                family=family_of(row.data_type),
                nullable=row.is_nullable,
                ordinal=row.ordinal,
                published=_is_published(
                    f"{table_name}.{row.column_name}",
                    lineage.get(f"{table_name}.{row.column_name}", ()),
                    excluded,
                ),
                deprecated=f"{table_name}.{row.column_name}".lower() in deprecated,
                deprecated_reason=deprecated.get(f"{table_name}.{row.column_name}".lower()),
                derives_from=lineage.get(
                    f"{table_name}.{row.column_name}",
                    (f"{table_name}.{row.column_name}",),
                ),
                lineage_resolved=f"{table_name}.{row.column_name}" not in unresolved_lineage,
            )
            # By ordinal, not name: column order is part of the schema and
            # `SELECT *` expands in that order.
            for row in sorted(by_table[table_name], key=lambda r: (r.ordinal, r.column_name))
        )
        tables.append(TableSpec(name=table_name, kind=kinds[table_name], columns=columns))
    return CatalogSchema(dialect=dialect, tables=tuple(tables))


def _is_published(ref: str, derives_from: tuple[str, ...], excluded: set[str]) -> bool:
    """A column is published unless it or any of its sources is excluded.

    Found by the attack holdout: `dim_merchant.traffic_weight` was unpublished as
    the policy requires, but `v_merchant_current.traffic_weight` (the same column
    through a view) was published. Propagating exclusion through lineage closes
    that path without relying on every future view being excluded by hand.
    """
    if ref.lower() in excluded:
        return False
    return not any(source.lower() in excluded for source in derives_from)


def unknown_columns(schema: CatalogSchema, qualified: Iterable[str]) -> list[str]:
    """The `table.column` refs in `qualified` that do not exist in the schema.

    Backs half of I-07: keys in `docs/spec/policy.yaml` and the glossary must be a
    subset of the generated catalog. A policy protecting a column that no longer
    exists gives false coverage and fails silently.
    """
    missing: list[str] = []
    for ref in qualified:
        table_name, _, column_name = ref.partition(".")
        table = schema.table(table_name)
        if table is None or table.column(column_name) is None:
            missing.append(ref)
    return missing


def to_json(schema: CatalogSchema) -> str:
    """Canonical serialization: sorted keys and a trailing newline.

    `G-CATALOG-FRESH` compares the sha256 of this text against a recomputed one, so
    the output must not depend on dict ordering.
    """
    return json.dumps(schema.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def introspect_duckdb(
    database: pathlib.Path,
    *,
    excluded_columns: Iterable[str] = (),
    deprecated_columns: Mapping[str, str] = {},
) -> CatalogSchema:
    """Read the live schema of a DuckDB catalog. The only part with I/O.

    `duckdb` is imported locally so `build_schema()`, which is what the unit tests
    exercise, stays importable without the engine installed.
    """
    import duckdb

    if not database.exists():
        message = (
            f"no existe {database}. El catálogo se GENERA desde el esquema vivo: "
            "sin base de datos no hay esquema, y escribirlo a mano es justo lo que "
            "I-07 prohíbe. Genera el dataset con `make dataset PROFILE=dev`."
        )
        raise FileNotFoundError(message)

    con = duckdb.connect(str(database), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT c.table_name, t.table_type, c.column_name, c.data_type,
                   c.is_nullable, c.ordinal_position
            FROM information_schema.columns c
            JOIN information_schema.tables t
              ON t.table_name = c.table_name AND t.table_schema = c.table_schema
            WHERE c.table_schema = 'main'
            """
        ).fetchall()
        views = con.execute(
            """
            SELECT view_name, sql FROM duckdb_views() WHERE schema_name = 'main'
            """
        ).fetchall()
    finally:
        con.close()

    known = {str(r[0]) for r in rows}
    return build_schema(
        (
            ColumnRow(
                table_name=str(r[0]),
                table_kind=KIND_VIEW if str(r[1]).upper().endswith("VIEW") else KIND_TABLE,
                column_name=str(r[2]),
                data_type=str(r[3]),
                is_nullable=str(r[4]).upper() in {"YES", "TRUE", "1"},
                ordinal=int(r[5]),
            )
            for r in rows
        ),
        dialect="duckdb",
        excluded_columns=excluded_columns,
        deprecated_columns=deprecated_columns,
        view_sql={str(v[0]): str(v[1]) for v in views if str(v[0]) in known},
    )


def load_generated(path: pathlib.Path) -> CatalogSchema:
    """Load the generated catalog; this is what the guard uses at runtime."""
    from datawarden.catalog.types import from_dict

    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return from_dict(payload)
