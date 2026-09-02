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
