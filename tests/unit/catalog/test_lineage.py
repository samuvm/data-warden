"""El linaje de columnas, con vistas escritas a mano y sin motor.

**Lo que este módulo evita, dicho en un caso.** La política protege
`dim_customer.birth_date`. Existe una vista `v_customer` que la reexpone con otro
nombre de tabla, y una columna `full_name` que es `concat(first_name, last_name)`.
Sin linaje, una política que casa por `tabla.columna` no ve ninguna de las dos, y el
ataque por expresión derivada que `PROJECT.md` describe lo sirve el propio catálogo.
"""

from __future__ import annotations

from datawarden.catalog.lineage import (
    UNKNOWN,
    dependencies,
    resolve,
    topological_order,
    unresolved,
)

_COLUMNAS = {
    "dim_customer": ("customer_sk", "first_name", "last_name", "birth_date"),
    "v_customer": ("customer_sk", "full_name", "birth_date", "age_years"),
    "v_top": ("customer_sk", "full_name"),
    "v_recursiva": ("customer_sk",),
}

_VISTAS = {
    "v_customer": (
        "CREATE VIEW v_customer AS SELECT c.customer_sk, "
        "concat(c.first_name, ' ', c.last_name) AS full_name, "
        "c.birth_date, date_diff('year', c.birth_date, DATE '2026-08-31') AS age_years "
        "FROM dim_customer c"
    ),
    "v_top": "CREATE VIEW v_top AS SELECT customer_sk, full_name FROM v_customer",
    "v_recursiva": (
        "CREATE VIEW v_recursiva AS WITH RECURSIVE r(customer_sk) AS ("
        "SELECT customer_sk FROM dim_customer UNION ALL "
        "SELECT customer_sk FROM r) SELECT customer_sk FROM r"
    ),
}


def _linaje() -> dict[str, tuple[str, ...]]:
    return resolve(_COLUMNAS, _VISTAS, dialect="duckdb")


def test_una_columna_de_tabla_base_sale_de_si_misma() -> None:
    assert _linaje()["dim_customer.birth_date"] == ("dim_customer.birth_date",)


def test_una_vista_que_renombra_la_tabla_no_esconde_la_columna() -> None:
    """`v_customer.birth_date` ES `dim_customer.birth_date` con otro nombre delante."""
    assert _linaje()["v_customer.birth_date"] == ("dim_customer.birth_date",)


def test_una_expresion_derivada_arrastra_todas_sus_fuentes() -> None:
    """El ataque de `PROJECT.md`: `CONCAT(nombre, apellido)` sobre columnas marcadas.

    Basta con que UNA de las fuentes esté restringida para que la derivada lo esté,
    y por eso se guardan todas y no solo la primera.
    """
    assert _linaje()["v_customer.full_name"] == (
        "dim_customer.first_name",
        "dim_customer.last_name",
    )


def test_una_funcion_sobre_una_columna_protegida_tambien_la_arrastra() -> None:
    """`date_diff(..., birth_date, ...)` no deja de ser la fecha de nacimiento."""
    assert _linaje()["v_customer.age_years"] == ("dim_customer.birth_date",)


def test_una_vista_sobre_otra_vista_se_resuelve_hasta_la_tabla_base() -> None:
    """Dos saltos. Sin resolución transitiva, la segunda vista sería un agujero."""
    assert _linaje()["v_top.full_name"] == (
        "dim_customer.first_name",
        "dim_customer.last_name",
    )


def test_una_cte_recursiva_no_se_puede_seguir_y_se_dice() -> None:
    """FAIL-CLOSED CON PUNTERÍA, que es lo que hace usable el fail-closed.

    Lo que no se puede resolver no se da por seguro ni se deniega a ciegas: se le
    atribuye el CIERRE de dependencias, así que la vista sigue siendo consultable
    por quien tenga acceso a todo lo que hay debajo, y por nadie más.
    """
    sin_resolver = unresolved(_COLUMNAS, _VISTAS, dialect="duckdb")
    assert "v_recursiva.customer_sk" in sin_resolver
    fuentes = _linaje()["v_recursiva.customer_sk"]
    assert UNKNOWN not in fuentes
    assert "dim_customer.birth_date" in fuentes
    assert "dim_customer.first_name" in fuentes


def test_lo_que_si_se_resuelve_no_aparece_como_sin_resolver() -> None:
    sin_resolver = unresolved(_COLUMNAS, _VISTAS, dialect="duckdb")
    assert "v_customer.full_name" not in sin_resolver
    assert "v_top.full_name" not in sin_resolver


def test_las_dependencias_de_una_vista_son_las_relaciones_del_catalogo() -> None:
    """`read_parquet` no es una relación: es lo que distingue una tabla base."""
    relaciones = frozenset(_COLUMNAS)
    assert dependencies(_VISTAS["v_top"], relaciones, "duckdb") == frozenset({"v_customer"})
    assert (
        dependencies(
            "CREATE VIEW x AS SELECT * FROM read_parquet('a/*.parquet')",
            relaciones,
            "duckdb",
        )
        == frozenset()
    )


def test_un_sql_que_no_parsea_no_revienta_la_generacion_del_catalogo() -> None:
    assert dependencies("esto no es SQL ((", frozenset(_COLUMNAS), "duckdb") == frozenset()


def test_las_relaciones_se_ordenan_de_base_a_derivada() -> None:
    orden = topological_order(
        {
            "v_top": frozenset({"v_customer"}),
            "v_customer": frozenset({"dim_customer"}),
            "dim_customer": frozenset(),
        }
    )
    assert orden.index("dim_customer") < orden.index("v_customer") < orden.index("v_top")


def test_un_ciclo_no_cuelga_el_ordenamiento() -> None:
    """Un catálogo sano no tiene ciclos; uno enfermo no puede colgar el gate."""
    orden = topological_order({"a": frozenset({"b"}), "b": frozenset({"a"})})
    assert sorted(orden) == ["a", "b"]


def test_una_vista_sin_sql_se_trata_como_tabla_base() -> None:
    """Si el motor no da la definición, no se inventa: cada columna sale de sí misma."""
    linaje = resolve({"t": ("a",)}, {}, dialect="duckdb")
    assert linaje["t.a"] == ("t.a",)
