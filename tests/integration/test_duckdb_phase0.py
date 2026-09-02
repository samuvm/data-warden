"""Nivel 2 · DuckDB de verdad. Criterio de salida de la fase 0.

`docs/PLAN.md` pide para esta fase «integración: DuckDB con 1 mes de datos y
`row_count`» y «tres consultas SQL a mano». Aquí están las dos cosas, y una
tercera que vale más que las dos: **las tres consultas comprueban los números que
el glosario declara**, en vez de limitarse a no dar error.

Una consulta que corre no demuestra nada. Una consulta que devuelve 86,2 % de
aprobación cuando el glosario dice 86-87 % demuestra que el dataset, el catálogo y
la definición de negocio dicen lo mismo. Y si un día dejan de decirlo, esto se
entera.

**Nunca en el gate rápido** (I-17 del stack, error 17 de RULES §7): nivel 2 corre
en `make done`, que es donde Samuel pidió que fuera exigible.
"""

from __future__ import annotations

import pathlib

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATABASE = ROOT / "datagen" / "out" / "cierzo-dev.duckdb"
QUERIES = pathlib.Path(__file__).parent / "queries"


@pytest.fixture(scope="module")
def con():
    import duckdb

    if not DATABASE.exists():
        pytest.fail(
            f"no existe {DATABASE}. El dataset se genera con `make dataset PROFILE=dev` "
            "y es reproducible byte a byte desde su semilla. Saltar el test porque "
            "falta el dato sería exactamente la señal verde falsa que el Makefile "
            "prohíbe, así que esto es rojo."
        )
    connection = duckdb.connect(str(DATABASE), read_only=True)
    yield connection
    connection.close()


def test_el_catalogo_de_duckdb_tiene_las_relaciones_que_el_esquema_declara(con) -> None:
    """El catálogo generado y el motor vivo, cara a cara."""
    from datawarden.catalog import SCHEMA_PATH, load_generated

    vivo = {
        r[0]
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
    }
    assert set(load_generated(SCHEMA_PATH).table_names) == vivo


def test_un_mes_de_datos_tiene_las_filas_que_debe(con) -> None:
    """`row_count` sobre una partición, que es lo que pide el criterio de salida.

    Se comprueba contra el MANIFIESTO del generador, no contra un número escrito a
    mano: un número a mano se queda obsoleto en cuanto alguien regenera con otro
    perfil, y entonces el test se «arregla» cambiándolo, que es como muere.
    """
    import json

    manifest = json.loads(
        (ROOT / "datagen" / "out" / "dev" / "MANIFEST.json").read_text(encoding="utf-8")
    )
    total = con.execute("SELECT count(*) FROM fact_payment_attempt").fetchone()[0]
    assert total == manifest["tables"]["fact_payment_attempt"]["rows"]

    ultimo_mes = con.execute(
        """
        WITH r AS (SELECT max(event_date) AS hasta FROM fact_payment_attempt)
        SELECT count(*) FROM fact_payment_attempt f, r
        WHERE f.event_date >= date_trunc('month', r.hasta)
        """
    ).fetchone()[0]
    assert 0 < ultimo_mes < total


def test_las_tres_consultas_de_la_fase_0_corren(con) -> None:
    """Cero IA. Son SQL escrito a mano contra el esquema real."""
    ficheros = sorted(QUERIES.glob("*.sql"))
    assert len(ficheros) == 3
    for fichero in ficheros:
        filas = con.execute(fichero.read_text(encoding="utf-8")).fetchall()
        assert filas, f"{fichero.name} no devolvió ni una fila"


def test_la_tasa_de_aprobacion_coincide_con_la_del_glosario(con) -> None:
    """El glosario dice 86-87 %. Si el dataset dejara de decirlo, hay que enterarse.

    Es la diferencia entre un test que comprueba que el SQL compila y uno que
    comprueba que el sistema, el dato y la definición de negocio dicen lo mismo.
    """
    filas = con.execute(
        (QUERIES / "01-tasa-de-aprobacion-por-esquema.sql").read_text(encoding="utf-8")
    ).fetchall()
    intentos = sum(f[1] for f in filas)
    aprobados = sum(f[2] for f in filas)
    tasa = 100.0 * aprobados / intentos
    assert 85.0 <= tasa <= 88.0, f"tasa de aprobación global {tasa:.2f} %"


def test_el_mdr_efectivo_coincide_con_el_del_glosario(con) -> None:
    """El glosario dice 2,1-2,4 %. Sale de `fee / gross` sobre la liquidación."""
    filas = con.execute(
        (QUERIES / "02-volumen-e-ingreso-del-mes.sql").read_text(encoding="utf-8")
    ).fetchall()
    assert filas
    mdr = filas[0][4]
    assert 2.0 <= mdr <= 2.5, f"MDR efectivo {mdr} %"


def test_el_margen_neto_es_menor_que_el_ingreso_bruto(con) -> None:
    """G-2 de la firma: son dos métricas distintas y una es aproximadamente la mitad.

    El `ojo:` del glosario avisaba de que restar interchange y scheme fee «cambia el
    resultado a la mitad». Si algún día dejaran de diferir, es que alguien juntó las
    dos métricas, y ese es justo el error que la corrección firmada evita.
    """
    filas = con.execute(
        (QUERIES / "02-volumen-e-ingreso-del-mes.sql").read_text(encoding="utf-8")
    ).fetchall()
    _, volumen, ingreso_bruto, margen_neto, _ = filas[0]
    assert 0 < margen_neto < ingreso_bruto < volumen


def test_unir_el_scd2_por_la_clave_natural_sin_acotar_infla_las_filas(con) -> None:
    """La trampa 2 del glosario, MEDIDA en vez de citada.

    Es la comprobación que el glosario firmado pidió: «cada porcentaje se puede
    verificar con una consulta, y eso es exactamente lo que hay que hacer. Una
    trampa cuyo número no se ha vuelto a medir desde que se escribió es folclore».
    """
    bien = con.execute(
        """
        SELECT count(*) FROM fact_settlement_batch b
        JOIN v_merchant_current m ON m.merchant_id = b.merchant_id
        """
    ).fetchone()[0]
    mal = con.execute(
        """
        SELECT count(*) FROM fact_settlement_batch b
        JOIN dim_merchant m ON m.merchant_id = b.merchant_id
        """
    ).fetchone()[0]
    assert mal > bien
    inflacion = 100.0 * (mal - bien) / bien
    # MEDIDO, no citado: 37,9 % sobre `dev`, 30,3 % sobre `demo`, 31,9 % sobre `full`.
    # El glosario firmado dice «+53 %» y ese número NO reproduce sobre ninguno de los
    # tres perfiles. La trampa es real y grave —un ranking de comercios sale un tercio
    # inflado—; la cifra está vieja. Corregirla es tocar un contrato firmado, así que
    # va como propuesta P-003 en docs/PARA-SAMUEL.md y aquí se asierta la verdad.
    assert 25.0 <= inflacion <= 45.0, f"la inflación medida es {inflacion:.1f} %"


def test_el_catalogo_se_genera_desde_el_motor_y_es_reproducible(tmp_path) -> None:
    """`generate()` y `write()`, contra DuckDB de verdad. I-07 de punta a punta.

    Se genera dos veces y se compara: si el fichero no fuera byte a byte idéntico,
    `G-CATALOG-FRESH` fallaría de forma intermitente y acabaría desactivada, que es
    como muere una meta que sí importa.
    """
    from datawarden.catalog.build import generate, write

    primero = generate(DATABASE)
    segundo = generate(DATABASE)
    destino = tmp_path / "schema.json"
    assert write(primero, destino) == write(segundo, destino)
    assert destino.read_text(encoding="utf-8").startswith("{")
    assert primero.table("dim_customer") is not None


def test_la_introspeccion_falla_con_un_motivo_si_no_hay_base_de_datos(tmp_path) -> None:
    """Escribir el catálogo a mano es lo que I-07 prohíbe: sin motor, se dice y se para."""
    import pytest as _pytest

    from datawarden.catalog.introspect import introspect_duckdb

    with _pytest.raises(FileNotFoundError, match="no existe"):
        introspect_duckdb(tmp_path / "no-existe.duckdb")


def test_las_estadisticas_salen_de_iceberg_sin_leer_una_fila() -> None:
    """`build_from_iceberg` contra los manifiestos REALES del perfil `dev`.

    Es la propiedad que hace útil a un estimador preventivo: contar 3 millones de
    filas leyendo el manifiesto tarda milisegundos; escanearlas, no. Un guardián que
    para saber si una consulta es cara tuviera que ejecutarla no serviría de nada.
    """
    import json

    from datawarden.catalog.statistics import build_from_iceberg, to_json

    iceberg = ROOT / "datagen" / "out" / "dev" / "iceberg"
    if not iceberg.exists():
        pytest.fail(f"no existe {iceberg}: `make dataset PROFILE=dev`")

    stats = build_from_iceberg(iceberg, "dev")
    assert len(stats.tables) >= 20

    hechos = stats.table("fact_payment_attempt")
    assert hechos is not None
    assert hechos.partition_column == "event_date"
    assert hechos.rows > 0
    assert hechos.bytes > 0
    assert len(hechos.partitions) > 1
    # La suma de las particiones ES la tabla: si no, la poda mentiría en la misma
    # proporción en que las cuentas no cuadren.
    assert sum(p["rows"] for p in hechos.partitions.values()) == hechos.rows
    assert sum(p["bytes"] for p in hechos.partitions.values()) == hechos.bytes
    assert json.loads(to_json(stats))["profile"] == "dev"


def test_las_claves_de_particion_generadas_son_fechas_iso() -> None:
    """**REGRESIÓN, contra los manifiestos REALES.** Es donde ocurrió el fallo.

    El test unitario cubre la conversión; este cubre que lo que pyiceberg entrega de
    verdad pase por ella. La diferencia importa: el fallo no estuvo en la lógica,
    estuvo en el tipo que llegaba.
    """
    import re

    from datawarden.catalog.statistics import build_from_iceberg

    iceberg = ROOT / "datagen" / "out" / "dev" / "iceberg"
    if not iceberg.exists():
        pytest.fail(f"no existe {iceberg}: `make dataset PROFILE=dev`")

    hechos = build_from_iceberg(iceberg, "dev").table("fact_payment_attempt")
    assert hechos is not None
    assert hechos.partitions
    for clave in hechos.partitions:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", clave), (
            f"la clave de partición {clave!r} no es una fecha ISO: ningún literal de "
            "un WHERE casará con ella y la poda saldrá vacía, que es subestimar a cero"
        )
