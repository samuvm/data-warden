"""Bytes y filas por tabla, por columna y por partición. Sale de Iceberg.

**Por qué no está en `schema.json`.** Porque son dos cosas con dos ritmos de cambio
distintos: el esquema no cambia cuando se regeneran los datos, y estas cifras sí. Si
vivieran en el mismo fichero, el sha del catálogo dependería de cuántas filas se
generaron y `G-CATALOG-FRESH` dejaría de significar nada.

**Por qué de Iceberg y no de un `EXPLAIN`.** `docs/RULES.md §7`, error 11: `EXPLAIN
ANALYZE` **ejecuta la consulta**, así que es inútil para un guardián preventivo; y
el `EXPLAIN` de DuckDB da cardinalidad pero no bytes escaneados. Athena solo reporta
`DataScannedInBytes` **después** de ejecutar. El manifiesto de Iceberg, en cambio,
lleva `column_sizes` por fichero y el valor de partición de cada uno, **sin leer una
sola fila**. Y por eso mismo el estimador sirve para los dos motores.

Lo que se guarda, y por qué solo eso:

- **bytes por columna a nivel de tabla** — para podar por proyección;
- **bytes, filas y ficheros por VALOR de partición** — para podar por predicado.

No se guardan bytes por columna Y por partición: serían 730 particiones por 36
columnas solo en `fact_payment_attempt`, y el modelo proporcional —fracción de
columnas por fracción de particiones— da el mismo orden de magnitud con tres órdenes
de magnitud menos de fichero. La calibración de `G-COST-CALIB` dirá si basta; hasta
entonces, es una hipótesis declarada y no un hecho.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class TableStats:
    """Lo que se sabe de una tabla sin leer ni una fila."""

    name: str
    rows: int
    bytes: int
    files: int
    #: `columna` -> bytes que ocupa en toda la tabla.
    column_bytes: dict[str, int] = field(default_factory=dict)
    #: La columna por la que la tabla está particionada, si lo está.
    partition_column: str | None = None
    #: `valor de partición` -> `{rows, bytes, files}`.
    partitions: dict[str, dict[str, int]] = field(default_factory=dict)

    def bytes_of(self, columns: tuple[str, ...]) -> int:
        """Bytes de esas columnas. Sin columnas, la tabla entera.

        «Sin columnas» pasa con `count(*)`: no se proyecta ninguna, y aun así el
        motor tiene que abrir los ficheros. Cobrar cero ahí haría que
        `G-BUDGET-ESCAPE` fuera trivialmente cierto para toda consulta agregada.
        """
        if not columns:
            return self.bytes
        known = [self.column_bytes.get(c.lower(), 0) for c in columns]
        return sum(known) or self.bytes


@dataclass(frozen=True, slots=True)
class Statistics:
    """Las estadísticas de todo el almacén, con su procedencia."""

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
    """Lee los manifiestos de Iceberg. **No abre ni un fichero de datos.**

    Es la propiedad que hace útil a un estimador preventivo: contar los 66,6 M de
    filas leyendo el manifiesto tarda 0,02 s; escanearlas, minutos. Un guardián que
    para saber si una consulta es cara tuviera que ejecutarla no serviría de nada.
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
    """El valor de partición, como TEXTO ISO. Es la clave del índice de poda.

    **Y aquí hubo un fallo grave, que merece quedar escrito.** El `Record` de
    pyiceberg tiene un `repr` de la forma `Record[19967]`, y usarlo como clave
    producía particiones llamadas `Record[19967]` contra las que ningún literal de
    fecha casaba jamás. Consecuencia: la poda devolvía el conjunto VACÍO, el
    estimador cobraba **cero bytes** por la tabla de 4,1 GB, y `G-BUDGET-ESCAPE`
    —que es un axioma— habría dejado pasar cualquier consulta con un predicado de
    fecha. Subestimar a cero es exactamente la peor dirección posible.

    Lo encontró la calibración: `p95(real/estimado)` se disparó y al mirar el
    detalle salió `partitions_kept: 0`. Por eso `G-COST-CALIB` existe, y por eso
    `GOALS.yaml` dice que sin ella `G-BUDGET-ESCAPE` sería «trivialmente cierto y a
    la vez inútil». No era una frase retórica.

    Una partición `identity` sobre un `DATE` llega como días desde la época, así que
    se convierte; cualquier otra cosa se rinde y devuelve su texto, y quien se rinde
    es `_partition_filter`, que ante claves que no entiende no poda.
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
