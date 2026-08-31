#!/usr/bin/env python
"""Materializar el dataset como tablas Apache Iceberg, **spec v2**.

    uv run --with-requirements datagen/requirements.txt \
        --with 'pyiceberg[sql-sqlite,pyarrow]==0.11.1' \
        python datagen/build_iceberg.py --data datagen/out/dev

QUÉ AÑADE ICEBERG QUE NO TENÍA EL PARQUET SUELTO. Hasta aquí el almacén era «una
carpeta con ficheros»: para leer una tabla había que confiar en un `glob`, y
cualquiera que dejara un fichero de más la cambiaba sin querer. Iceberg pone un
MANIFIESTO por encima: la lista exacta de ficheros que forman la tabla en cada
instante. De ahí salen las cuatro cosas que `PROJECT.md` llama lakehouse —
evolución de esquema, viaje en el tiempo, instantáneas atómicas y poda de
particiones fiable— y ninguna de ellas es posible sobre un `glob`.

**SPEC V2, NO V3, Y NO ES UN DETALLE.** `docs/STACK.md` lo dice: Athena no
soporta v3. El criterio de aceptación nº 5 de `PROJECT.md` es que el mismo caso
funcione en DuckDB y en Athena; si el dataset se escribe en v3, ese criterio falla
por el FORMATO y no por la abstracción de motor, que es justo lo que se quería
demostrar. El script lo comprueba y aborta si no sale v2.

NO SE COPIA UN SOLO BYTE. `add_files` registra el Parquet que ya existe. Un
`full` son 7,46 GB: reescribirlos costaría minutos y espacio para obtener
exactamente los mismos ficheros.
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import time
import warnings

import pyarrow.parquet as pq
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.transforms import IdentityTransform

NAMESPACE = "cierzo"

# Tablas particionadas por día y por qué columna. El resto son dimensiones y
# tablas de referencia, que caben en un fichero y no ganan nada al partirse.
PARTITIONED = {
    "fact_payment_attempt": "event_date",
    "fact_order_line": "event_date",
}


def parquet_files(table_dir: pathlib.Path) -> list[pathlib.Path]:
    """Los ficheros de una tabla, particionada o no, en orden estable."""
    return sorted(table_dir.rglob("*.parquet"))


def build(data_dir: pathlib.Path, rebuild: bool) -> int:
    t0 = time.time()
    # ABSOLUTO. Iceberg guarda en el metadato la ruta literal de cada fichero, así
    # que una ruta relativa produce un catálogo que solo funciona desde el
    # directorio en que se creó -- y falla en silencio, devolviendo cero filas.
    data_dir = data_dir.resolve()
    ice_root = data_dir / "iceberg"
    if ice_root.exists():
        if not rebuild:
            print(f"{ice_root} ya existe; usa --rebuild para rehacerlo")
            return 1
        shutil.rmtree(ice_root)
    (ice_root / "warehouse").mkdir(parents=True)

    catalog = SqlCatalog(
        "cierzo",
        **{
            "uri": f"sqlite:///{ice_root}/catalog.db",
            "warehouse": f"file://{ice_root}/warehouse",
        },
    )
    catalog.create_namespace(NAMESPACE)

    tables = sorted(
        d.name
        for d in data_dir.iterdir()
        if d.is_dir() and d.name != "iceberg" and parquet_files(d)
    )
    print(f"{len(tables)} tablas · catálogo en {ice_root}/catalog.db\n")

    total_rows = 0
    failures = 0
    for name in tables:
        files = parquet_files(data_dir / name)
        schema = pq.read_schema(files[0])
        # El generador escribe varias columnas con codificación de diccionario,
        # que Iceberg no tiene como tipo. Se leen como `string`, que es
        # equivalente para cualquier consulta; el aviso se silencia porque
        # aparecería una vez por columna y por tabla.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                tbl = catalog.create_table(
                    f"{NAMESPACE}.{name}", schema=schema, properties={"format-version": "2"}
                )

                part_col = PARTITIONED.get(name)
                if part_col:
                    with tbl.update_spec() as spec:
                        spec.add_field(part_col, IdentityTransform(), part_col)
                    tbl = catalog.load_table(f"{NAMESPACE}.{name}")

                tbl.add_files([f"file://{f.resolve()}" for f in files])
            except Exception as exc:
                print(f"  FALLO  {name:32s} {type(exc).__name__}: {exc}")
                failures += 1
                continue

        # SPEC V2 O NADA. Se comprueba en el metadato, no en las propiedades:
        # `properties['format-version']` vuelve como None porque la versión vive
        # en el metadato de la tabla, y comprobarla ahí sería comprobar nada.
        version = tbl.metadata.format_version
        if version != 2:
            print(
                f"  FALLO  {name}: format-version = {version}, se exige 2 "
                "(Athena no soporta v3 y el criterio de paridad de motor "
                "fallaría por el formato, no por la abstracción)"
            )
            failures += 1
            continue

        expected = sum(pq.read_metadata(f).num_rows for f in files)
        tbl.scan().count() if hasattr(tbl.scan(), "count") else None
        total_rows += expected
        part = (
            f"· {len(files):4d} ficheros, particionada por {PARTITIONED[name]}"
            if name in PARTITIONED
            else f"· {len(files)} fichero"
        )
        print(f"  ok     {name:32s} {expected:>12,d} filas  v{version}  {part}")

    # Un script de vistas para DuckDB, con la ruta EXACTA del metadato vigente de
    # cada tabla.
    #
    # DuckDB sabe leer Iceberg pero no sabe preguntarle a un catálogo SQL cuál es
    # la instantánea actual: intenta adivinar el nombre del fichero de metadatos y
    # falla, porque PyIceberg los nombra `00001-<uuid>.metadata.json`. Adivinar
    # además sería lo contrario de lo que Iceberg aporta -- el catálogo existe
    # justamente para que nadie tenga que mirar la carpeta. Así que se le pregunta
    # al catálogo aquí, una vez, y se escribe la respuesta.
    def current_metadata(table_name: str) -> str | None:
        """Ruta del metadato vigente, o None diciendo por qué no la hay.

        Devolver `None` en vez de saltar con un `continue` no es cuestión de
        estilo: una tabla ausente del script de vistas sin explicación es justo el
        hueco silencioso que este proyecto existe para no tener.
        """
        try:
            return catalog.load_table(f"{NAMESPACE}.{table_name}").metadata_location
        except Exception as exc:
            print(
                f"  aviso  {table_name}: fuera de duckdb-views.sql "
                f"({type(exc).__name__}: {exc})"
            )
            return None

    lines = [
        "-- generado por datagen/build_iceberg.py; no editar a mano",
        "INSTALL iceberg; LOAD iceberg;",
    ]
    for name in tables:
        loc = current_metadata(name)
        if loc is not None:
            lines.append(
                f"CREATE OR REPLACE VIEW {name} AS "
                f"SELECT * FROM iceberg_scan('{loc.removeprefix('file://')}');"
            )

    (ice_root / "duckdb-views.sql").write_text("\n".join(lines) + "\n")

    print(
        f"\n{total_rows:,} filas registradas en {time.time() - t0:.1f}s, "
        f"sin copiar un byte · {failures} fallos"
    )
    if failures == 0:
        print(
            f"\nAbrir con DuckDB:\n"
            f'  duckdb -c ".read {ice_root}/duckdb-views.sql" '
            f'-c "SELECT count(*) FROM fact_payment_attempt;"'
        )
    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=pathlib.Path, default="datagen/out/dev")
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    return 1 if build(a.data, a.rebuild) else 0


if __name__ == "__main__":
    raise SystemExit(main())
