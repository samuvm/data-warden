"""El catálogo se GENERA desde el esquema vivo; nunca se escribe a mano (I-07).

`PROJECT.md` §2 lo exige —«catálogo autogenerado desde el esquema real, no escrito
a mano»— y `G-CATALOG-FRESH` lo hace comprobable: cero divergencias entre el sha de
`catalog/generated/schema.json` y el recomputado ahora mismo contra el motor.

**El módulo está partido en dos a propósito.** `build_schema()` es puro y no sabe
que DuckDB existe: recibe filas y devuelve un `CatalogSchema`. `introspect_duckdb()`
es la parte que toca disco. La razón es la invariante I-13: `tests/unit` no toca
disco, ni red, ni DuckDB, así que los tests unitarios del catálogo corren contra un
esquema fixture en memoria y solo la integración levanta el motor. Un catálogo que
solo se pudiera probar con 7,4 GB delante no se probaría nunca.
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

#: Prefijos de tipo de motor, del más específico al más general. El orden importa:
#: `TIMESTAMP` empieza por `TIME` y clasificarlo antes lo mandaría a la familia
#: equivocada. Es la clase de error que no da síntoma hasta que una regla del guard
#: decide sobre la familia.
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
    """Una fila de `information_schema.columns`, sin depender de qué motor la dio."""

    table_name: str
    table_kind: str
    column_name: str
    data_type: str
    is_nullable: bool
    ordinal: int


def family_of(engine_type: str) -> str:
    """Familia canónica de un tipo de motor.

    El guard y el estimador de coste razonan sobre la familia, no sobre el nombre
    del motor: `VARCHAR` y `string` son la misma cosa con dos nombres, y una regla
    que compare nombres de motor se rompe el día que entre Athena.
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
    """Construye el esquema. PURO: ni disco, ni red, ni motor.

    Args:
        rows: Las columnas del esquema vivo, en cualquier orden.
        dialect: El dialecto de sqlglot con el que se re-serializará el AST.
        excluded_columns: `tabla.columna` que NO se publican en el anillo 1. Salen
            de `docs/spec/policy.yaml` (cambio C-3 de la firma de Q-003).
        deprecated_columns: `tabla.columna` -> motivo. Salen de
            `docs/spec/catalog-overlay.yaml`: el motor no puede saber que una
            columna que sigue existiendo ya no debe usarse.
        view_sql: `vista` -> su definición SQL. Sin esto no hay linaje, y sin
            linaje la política se salta usando una vista: `v_customer.birth_date`
            es la misma columna que `dim_customer.birth_date` con otro nombre.

    Returns:
        El esquema con las tablas y columnas ordenadas de forma determinista. El
        orden no es cosmético: el fichero generado se compara por sha256 y un
        orden que dependiera del motor haría que `G-CATALOG-FRESH` fallara al
        cambiar de versión de DuckDB sin que el esquema hubiera cambiado.
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
            # Por ordinal y no por nombre: el orden de columnas de una tabla es
            # parte de su esquema, y `SELECT *` se expande en ese orden.
            for row in sorted(by_table[table_name], key=lambda r: (r.ordinal, r.column_name))
        )
        tables.append(TableSpec(name=table_name, kind=kinds[table_name], columns=columns))
    return CatalogSchema(dialect=dialect, tables=tuple(tables))


def _is_published(ref: str, derives_from: tuple[str, ...], excluded: set[str]) -> bool:
    """Una columna se publica salvo que ELLA o alguna de sus FUENTES esté excluida.

    **Lo encontró el subagente `qa-adversario` escribiendo la reserva**, y es
    exactamente la clase de agujero que la reserva existe para encontrar:
    `dim_merchant.traffic_weight` salía con `published: false`, como C-3 manda, y
    `v_merchant_current.traffic_weight` —la MISMA columna vista a través de la
    vista— salía con `published: true`. La exclusión del catálogo del anillo 1
    tenía una puerta trasera con nombre de vista.

    Propagar por linaje es la única forma de cerrarlo que no depende de que alguien
    se acuerde de excluir también la vista, y de la siguiente vista, y de la que se
    cree dentro de seis semanas.
    """
    if ref.lower() in excluded:
        return False
    return not any(source.lower() in excluded for source in derives_from)


def unknown_columns(schema: CatalogSchema, qualified: Iterable[str]) -> list[str]:
    """Las `tabla.columna` de `qualified` que NO existen en el esquema.

    Es el motor de la mitad de I-07: las claves de `docs/spec/policy.yaml` y del
    glosario tienen que ser un subconjunto exacto del catálogo generado. Una
    política que protege una columna que ya no existe da una falsa sensación de
    cobertura, y es un fallo silencioso: nada la ejercita nunca.
    """
    missing: list[str] = []
    for ref in qualified:
        table_name, _, column_name = ref.partition(".")
        table = schema.table(table_name)
        if table is None or table.column(column_name) is None:
            missing.append(ref)
    return missing


def to_json(schema: CatalogSchema) -> str:
    """Serialización canónica: claves ordenadas y un salto de línea final.

    Determinista por obligación, no por gusto: `G-CATALOG-FRESH` compara el sha256
    de este texto contra el recomputado, y un `json.dumps` sin `sort_keys` produce
    un sha distinto según la versión de Python.
    """
    return json.dumps(schema.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def introspect_duckdb(
    database: pathlib.Path,
    *,
    excluded_columns: Iterable[str] = (),
    deprecated_columns: Mapping[str, str] = {},
) -> CatalogSchema:
    """Lee el esquema vivo de un catálogo DuckDB. Es la única parte con I/O.

    `import duckdb` va DENTRO de la función y no arriba: el contrato de capas
    prohíbe que `datawarden.domain` toque el motor, y aunque `catalog` sí puede,
    mantener la importación local hace que `build_schema()` —lo que de verdad se
    prueba— siga siendo importable sin motor instalado.
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
    """Carga el catálogo ya generado. Lo que usa el guard en tiempo de ejecución."""
    from datawarden.catalog.types import from_dict

    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return from_dict(payload)
