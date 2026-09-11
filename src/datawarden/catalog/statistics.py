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


def derive_views(stats: Statistics, schema: Any) -> Statistics:
    """Las estadísticas de las VISTAS, deducidas del linaje ya publicado.

    **Esto nace de P-012, y el defecto que arregla era grave.** Los manifiestos de
    Iceberg solo existen para las 24 tablas físicas, pero el catálogo publica 32
    relaciones: las 8 que faltaban son las vistas derivadas. Para el estimador eran
    tablas DESCONOCIDAS, así que les cobraba `UNKNOWN_TABLE_BYTES` —1 GB, un castigo
    deliberado y correcto para lo que de verdad no se conoce—. Solo que 1 GB está por
    encima del presupuesto duro de `analyst` (600 MB), de modo que **las ocho vistas
    eran inalcanzables para el rol principal, hicieran lo que hicieran**: se cobraba
    1 GB por lo que cuesta 7,5 MB.

    Y el glosario FIRMADO manda usarlas: `pago_valido` «se calcula» sobre
    `v_payment_intent`, `tasa_de_aprobacion` sobre `v_attempt_dedup`. El sistema le
    decía al modelo que usara un camino que él mismo cerraba. 20 de las 47 referencias
    escritas del banco las rechazaba el propio sistema.

    **La dirección del error es lo que decide el diseño.** Sobreestimar es un rechazo
    de más; subestimar deja pasar una consulta cara, y `G-BUDGET-ESCAPE` es un axioma.
    Así que todo lo dudoso se redondea hacia arriba:

    - `bytes` y `files`: la SUMA de las bases. Una vista no lee menos que sus bases.
    - `rows`: el MÁXIMO de las bases. Un `dedup` o un `group by` solo quitan filas.
    - `column_bytes`: la suma de las columnas de las que deriva cada una, que es
      exactamente lo que el motor abre para producirla.
    - `partitions`: **solo** se heredan cuando la vista tiene UNA base particionada y
      conserva su columna de partición con linaje de identidad. Es el único caso en
      que podar por predicado sobre la vista poda los mismos ficheros que sobre la
      base. En cualquier otro, sin particiones: no se poda y se cobra entero.

    Lo que NO se toca: una relación sin linaje sigue siendo desconocida y sigue
    pagando el castigo. El castigo no era el error; el error era aplicárselo a algo
    cuyo linaje está publicado columna a columna.
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
    """El índice de poda de la base, **solo si la vista lo conserva intacto**.

    Se exige que haya una sola base, que esté particionada, y que exista una columna
    de la vista cuyo linaje sea EXACTAMENTE esa columna de partición y nada más. Una
    columna agregada (`min(event_ts)`) o compuesta no vale: un predicado sobre ella
    no poda los mismos ficheros, y creer que sí es subestimar.
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
