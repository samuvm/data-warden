"""El estimador de coste, contra metadatos FIXTURE. Zona TDD obligatorio.

FASE ROJA. Escritos contra `docs/PLAN.md` fase 3 —«poda de particiones desde el
predicado del árbol validado, poda de columnas desde la proyección»— y contra la
nota de `G-COST-CALIB`, que es la que fija la dirección del error:

> El umbral es p95(real/estimado) <= 1,5, y cero casos con ratio > 3.

Ese cociente dice **hacia dónde tiene que equivocarse el estimador**: si subestima,
el ratio se dispara y una consulta cara se cuela; si sobreestima, el ratio baja y lo
único que pasa es que alguien tiene que acotar más su pregunta. **Ante la duda, el
estimador cobra de más.** Es la regla que gobierna cada caso de este fichero.

Y sin `G-COST-CALIB`, `G-BUDGET-ESCAPE` sería trivialmente cierto y a la vez inútil:
un estimador que devolviera siempre cero no dejaría escapar nada.
"""

from __future__ import annotations

import sqlglot

from datawarden.catalog.statistics import Statistics, TableStats
from datawarden.cost.estimator import estimate
from datawarden.domain.types import Principal, Role, RoleSource, ValidatedQuery

# Una tabla de hechos particionada por día y una dimensión sin particionar. Cuatro
# particiones de 100 bytes cada una: los números son redondos para que un fallo se
# lea sin calculadora.
_STATS = Statistics(
    profile="fixture",
    source="fixture",
    tables={
        "fact_payment_attempt": TableStats(
            name="fact_payment_attempt",
            rows=400,
            bytes=400,
            files=4,
            column_bytes={
                "event_date": 40,
                "amount_minor": 200,
                "auth_status": 60,
                "customer_sk": 100,
            },
            partition_column="event_date",
            partitions={
                "2026-08-01": {"rows": 100, "bytes": 100, "files": 1},
                "2026-08-02": {"rows": 100, "bytes": 100, "files": 1},
                "2026-08-03": {"rows": 100, "bytes": 100, "files": 1},
                "2026-08-04": {"rows": 100, "bytes": 100, "files": 1},
            },
        ),
        "dim_customer": TableStats(
            name="dim_customer",
            rows=50,
            bytes=1000,
            files=1,
            column_bytes={"customer_sk": 100, "country_code": 300, "email": 600},
        ),
    },
)

_SCHEMA = {
    "fact_payment_attempt": {
        "event_date": "DATE",
        "amount_minor": "BIGINT",
        "auth_status": "VARCHAR",
        "customer_sk": "INTEGER",
    },
    "dim_customer": {
        "customer_sk": "INTEGER",
        "country_code": "VARCHAR",
        "email": "VARCHAR",
    },
}


def _query(sql: str) -> ValidatedQuery:
    from sqlglot.optimizer.qualify import qualify

    tree = qualify(sqlglot.parse_one(sql, dialect="duckdb"), schema=_SCHEMA, dialect="duckdb")
    return ValidatedQuery(
        ast=tree,
        dialect="duckdb",
        principal=Principal(id="c", role=Role.ANALYST, source=RoleSource.CLI_FLAG),
        tables=(),
        columns=(),
        max_rows=1000,
    )


# --------------------------------------------------- poda por proyección


def test_una_sola_columna_no_cuesta_la_tabla_entera() -> None:
    """La poda de columnas es la mitad de la razón de ser de un almacén columnar."""
    coste = estimate(_query("SELECT auth_status FROM fact_payment_attempt"), _STATS)
    assert coste.estimated_bytes == 60


def test_dos_columnas_suman_las_dos() -> None:
    sql = "SELECT auth_status, amount_minor FROM fact_payment_attempt"
    assert estimate(_query(sql), _STATS).estimated_bytes == 260


def test_una_columna_del_predicado_tambien_se_lee() -> None:
    """`WHERE amount_minor > 1` no la devuelve y el motor tiene que leerla igual."""
    sql = "SELECT auth_status FROM fact_payment_attempt WHERE amount_minor > 1"
    assert estimate(_query(sql), _STATS).estimated_bytes == 260


def test_un_conteo_sin_columnas_cobra_la_tabla_entera() -> None:
    """`count(*)` no proyecta nada y el motor abre los ficheros igual.

    Cobrar cero aquí haría `G-BUDGET-ESCAPE` trivialmente cierto para toda consulta
    agregada, que es la mitad de las consultas de este almacén.
    """
    assert (
        estimate(_query("SELECT count(*) FROM fact_payment_attempt"), _STATS).estimated_bytes
        == 400
    )


# --------------------------------------------------- poda por partición


def test_un_predicado_de_igualdad_sobre_la_particion_poda() -> None:
    """Una de cuatro particiones: una cuarta parte de los bytes."""
    sql = "SELECT auth_status FROM fact_payment_attempt WHERE event_date = DATE '2026-08-01'"
    # auth_status (60) + event_date (40), porque el motor LEE la columna del
    # predicado aunque no la devuelva; y una de cuatro particiones: 100 x 0,25.
    assert estimate(_query(sql), _STATS).estimated_bytes == 25


def test_un_in_sobre_la_particion_poda_a_los_valores_nombrados() -> None:
    sql = (
        "SELECT auth_status FROM fact_payment_attempt "
        "WHERE event_date IN (DATE '2026-08-01', DATE '2026-08-02')"
    )
    assert estimate(_query(sql), _STATS).estimated_bytes == 50


def test_un_between_sobre_la_particion_poda_al_rango() -> None:
    sql = (
        "SELECT auth_status FROM fact_payment_attempt "
        "WHERE event_date BETWEEN DATE '2026-08-02' AND DATE '2026-08-03'"
    )
    assert estimate(_query(sql), _STATS).estimated_bytes == 50


def test_un_rango_abierto_por_arriba_poda() -> None:
    sql = "SELECT auth_status FROM fact_payment_attempt WHERE event_date >= DATE '2026-08-03'"
    assert estimate(_query(sql), _STATS).estimated_bytes == 50


def test_sin_predicado_de_particion_se_cobran_todas() -> None:
    sql = "SELECT auth_status FROM fact_payment_attempt"
    assert estimate(_query(sql), _STATS).estimated_bytes == 60


def test_un_predicado_que_el_estimador_no_sabe_leer_no_poda() -> None:
    """ANTE LA DUDA, SE COBRA DE MÁS. Es la regla que gobierna todo el módulo.

    `date_trunc('month', event_date) = ...` acota de verdad, y el estimador no sabe
    cuánto. Suponer que poda sería subestimar, y subestimar es por donde se cuela una
    consulta cara: `G-COST-CALIB` mide exactamente eso con p95(real/estimado).
    """
    sql = (
        "SELECT auth_status FROM fact_payment_attempt "
        "WHERE date_trunc('month', event_date) = DATE '2026-08-01'"
    )
    # Las cuatro particiones enteras: 100 bytes de columnas x 1,0.
    assert estimate(_query(sql), _STATS).estimated_bytes == 100


def test_un_or_sobre_la_particion_no_poda() -> None:
    """Un `OR` con una rama que no habla de la partición no acota nada."""
    sql = (
        "SELECT auth_status FROM fact_payment_attempt "
        "WHERE event_date = DATE '2026-08-01' OR amount_minor > 1"
    )
    # Y las tres columnas que toca: 60 + 40 + 200.
    assert estimate(_query(sql), _STATS).estimated_bytes == 300


# ------------------------------------------------------------- varias tablas


def test_un_join_suma_los_bytes_de_las_dos_tablas() -> None:
    """Un join multiplica FILAS, no bytes escaneados. La métrica es bytes."""
    sql = (
        "SELECT f.auth_status, c.country_code "
        "FROM fact_payment_attempt AS f "
        "JOIN dim_customer AS c ON c.customer_sk = f.customer_sk"
    )
    # fact: auth_status 60 + customer_sk 100 = 160. dim: country_code 300 + sk 100 = 400.
    assert estimate(_query(sql), _STATS).estimated_bytes == 560


def test_una_tabla_desconocida_para_las_estadisticas_no_hace_cero_el_coste() -> None:
    """Cobrar cero por lo que no se conoce es la forma de dejar escapar lo caro."""
    stats = Statistics(profile="vacio", source="vacio", tables={})
    coste = estimate(_query("SELECT auth_status FROM fact_payment_attempt"), stats)
    assert coste.estimated_bytes > 0
    assert coste.detail["unknown_tables"] == ["fact_payment_attempt"]


# --------------------------------------------------------------- el veredicto


def test_la_estimacion_declara_su_metodo() -> None:
    """Un coste sin método no es auditable: Iceberg y EXPLAIN no valen lo mismo."""
    assert (
        estimate(_query("SELECT auth_status FROM fact_payment_attempt"), _STATS).method
        == "iceberg"
    )


def test_la_estimacion_cuenta_los_ficheros_que_sobreviven() -> None:
    sql = "SELECT auth_status FROM fact_payment_attempt WHERE event_date = DATE '2026-08-01'"
    coste = estimate(_query(sql), _STATS)
    assert coste.files_scanned == 1
    assert coste.estimated_rows == 100


def test_la_estimacion_dice_que_podo_y_con_que() -> None:
    """Sin esto, una calibración mala no se puede depurar: solo se sabe que falló."""
    sql = "SELECT auth_status FROM fact_payment_attempt WHERE event_date = DATE '2026-08-01'"
    detalle = estimate(_query(sql), _STATS).detail
    assert detalle["per_table"]["fact_payment_attempt"]["partitions_kept"] == 1
    assert detalle["per_table"]["fact_payment_attempt"]["partitions_total"] == 4
    assert detalle["per_table"]["fact_payment_attempt"]["columns"] == [
        "auth_status",
        "event_date",
    ]


def test_la_particion_a_la_izquierda_del_operador_poda_igual() -> None:
    """`DATE '...' <= event_date` es lo mismo con los lados cambiados."""
    sql = "SELECT auth_status FROM fact_payment_attempt WHERE DATE '2026-08-03' <= event_date"
    assert estimate(_query(sql), _STATS).estimated_bytes == 50


def test_la_igualdad_con_la_particion_a_la_derecha_poda_igual() -> None:
    sql = "SELECT auth_status FROM fact_payment_attempt WHERE DATE '2026-08-01' = event_date"
    assert estimate(_query(sql), _STATS).estimated_bytes == 25


def test_un_between_con_un_extremo_que_no_es_literal_no_poda() -> None:
    """Ante la duda, se cobra de más. Un extremo que depende de datos es duda."""
    sql = (
        "SELECT auth_status FROM fact_payment_attempt "
        "WHERE event_date BETWEEN event_date AND DATE '2026-08-03'"
    )
    assert estimate(_query(sql), _STATS).estimated_bytes == 100


def test_un_in_sin_literales_no_poda() -> None:
    sql = (
        "SELECT auth_status, amount_minor FROM fact_payment_attempt "
        "WHERE event_date IN (event_date)"
    )
    assert estimate(_query(sql), _STATS).estimated_bytes == 300


def test_una_comparacion_que_no_toca_la_particion_no_poda() -> None:
    sql = "SELECT auth_status FROM fact_payment_attempt WHERE amount_minor > 1"
    assert estimate(_query(sql), _STATS).estimated_bytes == 260


def test_dos_predicados_de_particion_se_cruzan() -> None:
    """`AND` acota por los dos lados: la intersección, no la unión."""
    sql = (
        "SELECT auth_status FROM fact_payment_attempt "
        "WHERE event_date >= DATE '2026-08-02' AND event_date < DATE '2026-08-04'"
    )
    assert estimate(_query(sql), _STATS).estimated_bytes == 50


def test_un_predicado_estricto_excluye_el_extremo() -> None:
    sql = "SELECT auth_status FROM fact_payment_attempt WHERE event_date > DATE '2026-08-03'"
    assert estimate(_query(sql), _STATS).estimated_bytes == 25


def test_una_cte_no_ocupa_bytes_en_disco() -> None:
    """Una CTE es una relación que la consulta inventa: no hay ficheros que leer."""
    sql = "WITH x AS (SELECT auth_status FROM fact_payment_attempt) SELECT auth_status FROM x"
    detalle = estimate(_query(sql), _STATS).detail
    assert "x" not in detalle["per_table"]
    assert "fact_payment_attempt" in detalle["per_table"]


def test_un_rango_que_no_casa_con_ninguna_particion_no_reduce_a_cero() -> None:
    """**REGRESIÓN DE UN FALLO GRAVE.** Subestimar a cero es la peor dirección.

    Las claves de partición se estaban generando como `Record[19967]` en vez de como
    fechas ISO, así que ningún literal casaba, la poda salía VACÍA y el estimador
    cobraba cero bytes por una tabla de 4,1 GB. `G-BUDGET-ESCAPE`, que es un axioma,
    habría dejado pasar cualquier consulta con un predicado de fecha.

    Lo encontró `G-COST-CALIB`: el p95 se disparó a 50 y el detalle decía
    `partitions_kept: 0`. Por eso existe esa meta y por eso `GOALS.yaml` dice que sin
    ella `G-BUDGET-ESCAPE` sería «trivialmente cierto y a la vez inútil».

    Un rango legítimamente vacío existe, y con esto se sobreestima. Sobreestimar
    cuesta que alguien acote su pregunta; subestimar a cero cuesta el axioma.
    """
    sql = "SELECT auth_status FROM fact_payment_attempt WHERE event_date = DATE '1999-01-01'"
    coste = estimate(_query(sql), _STATS)
    assert coste.estimated_bytes > 0
    assert coste.detail["per_table"]["fact_payment_attempt"]["partitions_kept"] == 4


def test_dos_predicados_incompatibles_tampoco_reducen_a_cero() -> None:
    """`>= '2026-08-04' AND <= '2026-08-01'` es vacío, y aun así se cobra."""
    sql = (
        "SELECT auth_status FROM fact_payment_attempt "
        "WHERE event_date >= DATE '2026-08-04' AND event_date <= DATE '2026-08-01'"
    )
    assert estimate(_query(sql), _STATS).estimated_bytes == 100
