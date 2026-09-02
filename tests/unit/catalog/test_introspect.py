"""El catálogo, contra un esquema fixture EN MEMORIA. Nunca contra DuckDB (I-13).

`docs/PLAN.md` lo exige literalmente en el criterio de salida de la fase 0: «unit de
`catalog/` contra esquema fixture en memoria (no contra DuckDB)». La razón es que un
catálogo que solo se pudiera probar con 7,4 GB delante no se probaría nunca, y la
lógica que importa —familias de tipo, orden determinista, exclusión, linaje— no
necesita ni una fila para equivocarse.
"""

from __future__ import annotations

import json

import pytest

from datawarden.catalog import (
    CatalogSchema,
    ColumnRow,
    ColumnSpec,
    TableSpec,
    build_schema,
    family_of,
    from_dict,
    to_json,
    unknown_columns,
)

_FILAS = (
    ColumnRow("dim_customer", "view", "customer_sk", "INTEGER", False, 1),
    ColumnRow("dim_customer", "view", "birth_date", "DATE", True, 2),
    ColumnRow("dim_customer", "view", "email", "VARCHAR", True, 3),
    ColumnRow("dim_merchant", "view", "merchant_sk", "INTEGER", False, 1),
    ColumnRow("dim_merchant", "view", "traffic_weight", "DOUBLE", True, 2),
)


def _esquema(**kwargs: object) -> CatalogSchema:
    return build_schema(_FILAS, dialect="duckdb", **kwargs)  # type: ignore[arg-type]


# ----------------------------------------------------------------- familias


@pytest.mark.parametrize(
    ("engine_type", "family"),
    [
        ("INTEGER", "integer"),
        ("BIGINT", "integer"),
        ("HUGEINT", "integer"),
        ("DECIMAL(18,2)", "decimal"),
        ("DOUBLE", "float"),
        ("VARCHAR", "text"),
        ("BOOLEAN", "boolean"),
        ("DATE", "date"),
        ("TIMESTAMP WITH TIME ZONE", "timestamp"),
        ("TIME", "time"),
        ("BLOB", "blob"),
        ("STRUCT(a INTEGER)", "other"),
    ],
)
def test_cada_tipo_de_motor_cae_en_su_familia(engine_type: str, family: str) -> None:
    assert family_of(engine_type) == family


def test_timestamp_no_se_clasifica_como_time() -> None:
    """`TIMESTAMP` empieza por `TIME`: el orden de los prefijos importa.

    Es la clase de error que no da síntoma hasta que una regla del guard decide
    sobre la familia, y entonces cuesta una tarde encontrarlo.
    """
    assert family_of("TIMESTAMP") == "timestamp"
    assert family_of("TIMESTAMP_NS") == "timestamp"


def test_un_tipo_desconocido_cae_en_other_y_no_revienta() -> None:
    """Un motor nuevo trae tipos nuevos: la respuesta segura es clasificar, no fallar."""
    assert family_of("GEOMETRY") == "other"


# --------------------------------------------------------------- estructura


def test_las_tablas_salen_en_orden_determinista() -> None:
    """El fichero se compara por sha256: un orden del motor rompería la meta."""
    assert _esquema().table_names == ("dim_customer", "dim_merchant")


def test_las_columnas_salen_por_ordinal_y_no_por_nombre() -> None:
    """El orden de columnas es parte del esquema: `SELECT *` se expande en él."""
    nombres = [c.name for c in _esquema().table("dim_customer").columns]
    assert nombres == ["customer_sk", "birth_date", "email"]


def test_las_filas_llegan_en_cualquier_orden_y_el_esquema_sale_igual() -> None:
    revueltas = tuple(reversed(_FILAS))
    assert to_json(build_schema(revueltas, dialect="duckdb")) == to_json(_esquema())


def test_una_tabla_se_busca_sin_distinguir_mayusculas() -> None:
    assert _esquema().table("DIM_CUSTOMER") is not None
    assert _esquema().table("no_existe") is None


def test_una_columna_se_busca_sin_distinguir_mayusculas() -> None:
    tabla = _esquema().table("dim_customer")
    assert tabla.column("BIRTH_DATE") is not None
    assert tabla.column("no_existe") is None


# ------------------------------------------------------------ publicación


def test_una_columna_excluida_sigue_en_el_esquema_pero_no_se_publica() -> None:
    """C-3: el esquema describe lo que EXISTE; publicar es otra decisión.

    Tenerla permite que `qualify()` sepa a qué tabla pertenece, en vez de verla
    como una columna desconocida y rechazar por el motivo equivocado.
    """
    schema = _esquema(excluded_columns=["dim_merchant.traffic_weight"])
    assert schema.table("dim_merchant").column("traffic_weight").published is False
    publicado = schema.published()
    assert publicado.table("dim_merchant").column("traffic_weight") is None
    assert publicado.table("dim_merchant").column("merchant_sk") is not None


def test_el_esquema_para_qualify_incluye_las_columnas_no_publicadas() -> None:
    schema = _esquema(excluded_columns=["dim_merchant.traffic_weight"])
    assert "traffic_weight" in schema.sqlglot_schema()["dim_merchant"]


def test_una_columna_obsoleta_lleva_su_motivo() -> None:
    schema = _esquema(deprecated_columns={"dim_customer.email": "usa email_domain"})
    columna = schema.table("dim_customer").column("email")
    assert columna.deprecated is True
    assert columna.deprecated_reason == "usa email_domain"


def test_una_columna_normal_no_esta_obsoleta_ni_oculta() -> None:
    columna = _esquema().table("dim_customer").column("email")
    assert columna.deprecated is False
    assert columna.published is True
    assert columna.deprecated_reason is None


# ------------------------------------------------------- serialización ida y vuelta


def test_el_json_es_canonico_y_estable() -> None:
    """Sin `sort_keys` el sha cambia entre versiones de Python y la meta se cae."""
    rendered = to_json(_esquema())
    assert rendered.endswith("\n")
    assert json.loads(rendered)["dialect"] == "duckdb"
    assert rendered == to_json(_esquema())


def test_el_esquema_sobrevive_a_una_vuelta_por_json() -> None:
    original = _esquema(excluded_columns=["dim_merchant.traffic_weight"])
    recuperado = from_dict(json.loads(to_json(original)))
    assert recuperado == original


# ------------------------------------------------------------------- I-07


def test_una_clave_que_no_esta_en_el_catalogo_se_denuncia() -> None:
    """Una política que protege una columna que ya no existe no protege nada."""
    faltan = unknown_columns(
        _esquema(),
        ["dim_customer.email", "dim_customer.no_existe", "no_existe.x"],
    )
    assert faltan == ["dim_customer.no_existe", "no_existe.x"]


def test_todas_las_claves_presentes_no_denuncian_nada() -> None:
    assert unknown_columns(_esquema(), ["dim_customer.email"]) == []


# ------------------------------------------------------------------ tipos


def test_una_columna_se_serializa_con_su_linaje() -> None:
    columna = ColumnSpec(
        name="full_name",
        engine_type="VARCHAR",
        family="text",
        nullable=True,
        ordinal=1,
        derives_from=("dim_customer.first_name",),
    )
    payload = columna.to_dict()
    assert payload["derives_from"] == ["dim_customer.first_name"]
    assert payload["lineage_resolved"] is True


def test_una_tabla_se_serializa_con_sus_columnas() -> None:
    tabla = TableSpec(
        name="t",
        kind="table",
        columns=(
            ColumnSpec(
                name="a", engine_type="INTEGER", family="integer", nullable=True, ordinal=1
            ),
        ),
    )
    payload = tabla.to_dict()
    assert payload["kind"] == "table"
    assert [c["name"] for c in payload["columns"]] == ["a"]


# =============================================================================
# Añadido el 2026-09-02 para cerrar el hueco de `G-MUTATION`: `catalog.introspect`
# tenía 108 mutantes vivos de 212 (49,06 %) con la cobertura de línea al 99 %. Los
# tests de arriba fijan el COMPORTAMIENTO feliz; lo que faltaba era el borde —la
# normalización de las claves, el desempate del orden y, sobre todo, la
# propagación por linaje que cerró la puerta trasera de C-3—.
# =============================================================================


# --------------------------------------------------- normalización de tipos ---


@pytest.mark.parametrize(
    "escrito",
    ["varchar", "VarChar", "  VARCHAR  ", "\tvarchar\n"],
    ids=["minusculas", "mezclado", "con_espacios", "con_tabuladores"],
)
def test_la_familia_no_depende_de_como_escriba_el_motor_el_tipo(escrito: str) -> None:
    """`.strip().upper()` es comportamiento, no aseo.

    DuckDB devuelve `VARCHAR`, Athena devuelve `string` y un `information_schema`
    ajeno puede devolver cualquiera de las dos con espacios. Una familia que
    dependiera de eso haría que la MISMA columna cambiara de familia al cambiar de
    motor, y `G-ENGINE-PARITY` de la fase 9 existe justamente para que no pase.
    """
    assert family_of(escrito) == family_of("VARCHAR")


def test_un_tipo_con_precision_conserva_su_familia() -> None:
    """`DECIMAL(18,2)` es un decimal: la tabla casa por PREFIJO, no por igualdad."""
    assert family_of("DECIMAL(18,2)") == family_of("DECIMAL")
    assert family_of("VARCHAR(64)") == family_of("VARCHAR")


# ------------------------------------------- normalización de los contratos ---


@pytest.mark.parametrize(
    "clave",
    [
        "dim_merchant.traffic_weight",
        "DIM_MERCHANT.TRAFFIC_WEIGHT",
        "  dim_merchant.traffic_weight  ",
    ],
    ids=["tal_cual", "mayusculas", "con_espacios"],
)
def test_la_exclusion_no_depende_de_como_se_escriba_en_el_contrato(clave: str) -> None:
    """El contrato lo escribe un humano y `policy.yaml` está FIRMADO.

    Si la exclusión distinguiera mayúsculas, una fila escrita con otra caja
    dejaría de excluir y la columna se publicaría. El fallo sería silencioso: el
    catálogo saldría con `published: true` y nadie miraría.
    """
    schema = _esquema(excluded_columns=[clave])
    assert schema.table("dim_merchant").column("traffic_weight").published is False


def test_la_marca_de_obsoleta_no_depende_de_la_caja_del_contrato() -> None:
    schema = _esquema(deprecated_columns={"DIM_CUSTOMER.EMAIL": "usa email_domain"})
    columna = schema.table("dim_customer").column("email")
    assert columna.deprecated is True
    assert columna.deprecated_reason == "usa email_domain"


# ------------------------------------------------------------- orden estable ---


def test_dos_columnas_con_el_mismo_ordinal_desempatan_por_nombre() -> None:
    """El desempate no es cosmético: sin él el orden lo decide el motor.

    `information_schema` puede entregar dos columnas con el mismo `ordinal` (pasa
    con vistas construidas por unión). Si el orden dependiera de cuál llegó antes,
    el sha256 de `schema.json` cambiaría entre ejecuciones sin que el esquema
    hubiera cambiado, y `G-CATALOG-FRESH` fallaría sin motivo real.
    """
    filas = (
        ColumnRow("t", "view", "zeta", "INTEGER", False, 1),
        ColumnRow("t", "view", "alfa", "INTEGER", False, 1),
    )
    nombres = tuple(c.name for c in build_schema(filas, dialect="duckdb").table("t").columns)
    invertido = tuple(
        c.name for c in build_schema(filas[::-1], dialect="duckdb").table("t").columns
    )

    assert nombres == ("alfa", "zeta")
    assert nombres == invertido


# ---------------------------------------------------------------- linaje ---


def test_una_columna_sin_vista_deriva_de_si_misma() -> None:
    """El valor por defecto de `derives_from` no puede ser la tupla vacía.

    Con `()` el `any()` de `_is_published` no recorrería nada y una columna
    excluida a través de su fuente se publicaría. El defecto correcto es ella
    misma, que es lo que hace que la regla se lea igual para tablas y vistas.
    """
    columna = _esquema().table("dim_customer").column("email")
    assert columna.derives_from == ("dim_customer.email",)


def test_una_vista_no_abre_una_puerta_trasera_a_una_columna_excluida() -> None:
    """**REGRESIÓN · la encontró el subagente `qa-adversario` en la fase 2.**

    `dim_merchant.traffic_weight` salía con `published: false`, como C-3 manda, y
    `v_merchant_current.traffic_weight` —la MISMA columna a través de la vista—
    salía con `published: true`. La exclusión del anillo 1 tenía una puerta
    trasera con nombre de vista, y bastaba `CREATE VIEW` para cruzarla.

    Propagar por linaje es lo único que la cierra sin depender de que alguien se
    acuerde de excluir también la vista, y la siguiente, y la de dentro de seis
    semanas.
    """
    filas = (
        ColumnRow("dim_merchant", "table", "merchant_sk", "INTEGER", False, 1),
        ColumnRow("dim_merchant", "table", "traffic_weight", "DOUBLE", True, 2),
        ColumnRow("v_merchant", "view", "merchant_sk", "INTEGER", False, 1),
        ColumnRow("v_merchant", "view", "traffic_weight", "DOUBLE", True, 2),
    )
    schema = build_schema(
        filas,
        dialect="duckdb",
        excluded_columns=["dim_merchant.traffic_weight"],
        view_sql={"v_merchant": "SELECT merchant_sk, traffic_weight FROM dim_merchant"},
    )

    assert schema.table("dim_merchant").column("traffic_weight").published is False
    assert schema.table("v_merchant").column("traffic_weight").published is False
    # Y la vecina, que no está excluida, sigue publicándose: la propagación no es
    # un apagón que oculte la vista entera.
    assert schema.table("v_merchant").column("merchant_sk").published is True


def test_un_alias_en_la_vista_tampoco_abre_la_puerta() -> None:
    """Renombrar la columna en la vista es la variante obvia del mismo ataque."""
    filas = (
        ColumnRow("dim_merchant", "table", "traffic_weight", "DOUBLE", True, 1),
        ColumnRow("v_merchant", "view", "peso", "DOUBLE", True, 1),
    )
    schema = build_schema(
        filas,
        dialect="duckdb",
        excluded_columns=["dim_merchant.traffic_weight"],
        view_sql={"v_merchant": "SELECT traffic_weight AS peso FROM dim_merchant"},
    )

    assert schema.table("v_merchant").column("peso").published is False


# -------------------------------------------------------- unknown_columns ---


def test_una_referencia_sin_punto_se_denuncia_en_vez_de_colarse() -> None:
    """`partition('.')` deja la columna vacía; una tabla sin columna no existe.

    Una clave mal escrita en `policy.yaml` —`birth_date` en vez de
    `dim_customer.birth_date`— tiene que salir denunciada. Si se colara, la
    política protegería una columna que el catálogo no conoce y nadie ejercitaría
    nunca esa fila.
    """
    assert unknown_columns(_esquema(), ["birth_date"]) == ["birth_date"]


def test_una_tabla_que_no_existe_se_denuncia_entera() -> None:
    assert unknown_columns(_esquema(), ["no_existe.columna"]) == ["no_existe.columna"]


def test_las_referencias_se_denuncian_en_el_orden_en_que_llegaron() -> None:
    """El informe de I-07 lo lee un humano: un orden aleatorio lo hace inútil."""
    faltan = unknown_columns(
        _esquema(),
        ["z.z", "dim_customer.email", "a.a", "dim_customer.no_existe"],
    )
    assert faltan == ["z.z", "a.a", "dim_customer.no_existe"]
