"""Las estadísticas, en memoria. Zona test-after, cobertura 90 %.

Lo que se prueba es lo que hace útil al estimador y no lo que hace bonito al
fichero: que `bytes_of` cobre **la tabla entera cuando no se proyecta ninguna
columna** —el caso de `count(*)`, que es la mitad de las consultas de este almacén—
y que el JSON sobreviva a una vuelta completa, porque el estimador lo lee de disco
en cada arranque y un campo perdido ahí no daría error: daría un coste más bajo.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from datawarden.catalog.statistics import (
    Statistics,
    TableStats,
    _partition_value,
    from_dict,
    load,
    to_json,
)

_STATS = Statistics(
    profile="fixture",
    source="fixture",
    tables={
        "fact": TableStats(
            name="fact",
            rows=400,
            bytes=400,
            files=4,
            column_bytes={"a": 100, "b": 300},
            partition_column="event_date",
            partitions={"2026-08-01": {"rows": 100, "bytes": 100, "files": 1}},
        ),
        "dim": TableStats(name="dim", rows=10, bytes=50, files=1, column_bytes={"x": 50}),
    },
)


def test_una_tabla_se_busca_sin_distinguir_mayusculas() -> None:
    assert _STATS.table("FACT") is not None
    assert _STATS.table("no_existe") is None


def test_los_bytes_de_unas_columnas_son_la_suma_de_esas_columnas() -> None:
    assert _STATS.table("fact").bytes_of(("a",)) == 100
    assert _STATS.table("fact").bytes_of(("a", "b")) == 400


def test_sin_columnas_se_cobra_la_tabla_entera() -> None:
    """`count(*)` no proyecta nada y el motor abre los ficheros igual.

    Cobrar cero aquí haría `G-BUDGET-ESCAPE` trivialmente cierto para toda consulta
    agregada, que es justo la mitad que más se escribe contra un almacén analítico.
    """
    assert _STATS.table("fact").bytes_of(()) == 400


def test_una_columna_desconocida_no_hace_cero_el_coste() -> None:
    """Ante la duda, se cobra de más: es la regla que gobierna el estimador entero."""
    assert _STATS.table("fact").bytes_of(("no_existe",)) == 400


def test_el_json_es_canonico_y_sobrevive_a_la_vuelta() -> None:
    rendered = to_json(_STATS)
    assert rendered.endswith("\n")
    recuperado = from_dict(json.loads(rendered))
    assert recuperado.profile == "fixture"
    assert recuperado.table("fact").bytes == 400
    assert recuperado.table("fact").partition_column == "event_date"
    assert recuperado.table("fact").partitions["2026-08-01"]["rows"] == 100
    assert recuperado.table("dim").column_bytes == {"x": 50}


def test_el_diccionario_lleva_la_procedencia() -> None:
    """Un número sin procedencia no se puede auditar: `full` y `dev` no valen igual."""
    payload = _STATS.to_dict()
    assert payload["profile"] == "fixture"
    assert payload["source"] == "fixture"
    assert set(payload["tables"]) == {"fact", "dim"}


def test_una_particion_de_fecha_se_guarda_como_texto_iso() -> None:
    """**REGRESIÓN.** El `repr` de un `Record` de pyiceberg es `Record[19967]`.

    Usarlo como clave hacía que ningún literal de fecha casara, que la poda saliera
    vacía y que el estimador cobrara CERO por una tabla de 4,1 GB. La clave tiene
    que ser comparable con lo que un `WHERE event_date = DATE '...'` escribe.
    """

    class _Record:
        def __init__(self, value: int) -> None:
            self._value = value

        def __iter__(self):
            return iter((self._value,))

        def __repr__(self) -> str:
            return f"Record[{self._value}]"

    # 19967 días desde 1970-01-01 son el 2024-09-01, el primer día del almacén.
    assert _partition_value(_Record(19967), "identity") == "2024-09-01"
    assert _partition_value(_Record(20000), "identity") == "2024-10-04"


def test_una_particion_que_no_es_una_fecha_se_guarda_tal_cual() -> None:
    """Y quien se rinde es la poda, que ante claves que no entiende no poda."""

    class _Record:
        def __init__(self, value: object) -> None:
            self._value = value

        def __iter__(self):
            return iter((self._value,))

    assert _partition_value(_Record("ES"), "identity") == "ES"
    assert _partition_value(_Record(7), "bucket[16]") == "7"
    assert _partition_value(_Record(None), "identity") == "None"


# =============================================================================
# Lo de abajo se añade el 2026-09-02 para cerrar el hueco que `G-MUTATION`
# destapó: `catalog.statistics` tenía 133 mutantes vivos de 204 (34,80 %) con la
# cobertura de línea al 99 %. Los tests de arriba EJECUTAN el módulo entero; lo
# que no hacían era asertar sobre los bordes, que es donde un mutante se esconde:
# el `or` que rescata un coste de cero, el `.lower()` del índice, el `int()` que
# convierte lo que llega del JSON, y el `bool` que se cuela por ser un `int`.
# =============================================================================

# ------------------------------------------------------------------ bytes_of ---


def test_una_columna_conocida_y_otra_desconocida_cobran_solo_la_conocida() -> None:
    """La desconocida aporta 0 y NO dispara el rescate de la tabla entera.

    Es el borde entre los dos tests que ya había: con `sum() == 0` se cobra la
    tabla, pero con `sum() > 0` se cobra lo sumado aunque falte una columna. Un
    mutante que cambie el `0` por defecto de `.get(c.lower(), 0)` sobrevive a los
    dos tests anteriores y muere en este.
    """
    assert _STATS.table("fact").bytes_of(("a", "no_existe")) == 100


def test_los_bytes_de_una_columna_se_buscan_en_minusculas() -> None:
    """`SELECT A FROM fact` proyecta `A`; el índice está en minúsculas.

    Sin el `.lower()`, toda consulta escrita en mayúsculas —que es la mitad del
    SQL que escribe un humano— caería al rescate y cobraría la tabla entera. No
    sería una fuga de presupuesto, sería lo contrario: cobrar de más siempre. Pero
    dejaría el estimador ciego a la proyección, que es media razón de que exista.
    """
    assert _STATS.table("fact").bytes_of(("A",)) == 100
    assert _STATS.table("fact").bytes_of(("A", "B")) == 400


def test_una_columna_que_pesa_cero_cobra_la_tabla_entera() -> None:
    """El `or` del rescate, aislado. Cobrar cero es la única dirección prohibida."""
    vacia = TableStats(name="vacia", rows=1, bytes=999, files=1, column_bytes={"nula": 0})
    assert vacia.bytes_of(("nula",)) == 999


def test_una_tabla_sin_column_bytes_cobra_la_tabla_entera() -> None:
    """Sin índice de columnas no se puede podar por proyección: se cobra todo."""
    opaca = TableStats(name="opaca", rows=1, bytes=777, files=1)
    assert opaca.bytes_of(("cualquiera",)) == 777


# --------------------------------------------------------------------- table ---


def test_la_tabla_se_busca_en_minusculas_tambien_desde_el_esquema() -> None:
    assert _STATS.table("Fact") is _STATS.table("fact")


# ------------------------------------------------------------------- to_dict ---


def test_el_diccionario_sale_ordenado_aunque_entre_desordenado() -> None:
    """Determinismo por obligación: el fichero se compara por sha.

    `sorted()` en los tres sitios no es estética. `statistics.json` se versiona y
    se compara; si el orden dependiera del orden de inserción, el mismo almacén
    produciría dos ficheros distintos y cualquier check de frescura mentiría.
    """
    desordenado = Statistics(
        profile="p",
        source="s",
        tables={
            "z_tabla": TableStats(
                name="z_tabla",
                rows=1,
                bytes=1,
                files=1,
                column_bytes={"z_col": 1, "a_col": 2},
                partitions={"2026-12-31": {"rows": 1}, "2026-01-01": {"rows": 2}},
            ),
            "a_tabla": TableStats(name="a_tabla", rows=1, bytes=1, files=1),
        },
    )

    payload = desordenado.to_dict()

    assert list(payload["tables"]) == ["a_tabla", "z_tabla"]
    assert list(payload["tables"]["z_tabla"]["column_bytes"]) == ["a_col", "z_col"]
    assert list(payload["tables"]["z_tabla"]["partitions"]) == [
        "2026-01-01",
        "2026-12-31",
    ]


def test_el_diccionario_declara_su_version_de_esquema() -> None:
    """Sin `version` no se puede migrar el fichero el día que cambie de forma."""
    assert _STATS.to_dict()["version"] == 1


def test_el_json_ordena_las_claves_y_termina_en_salto_de_linea() -> None:
    rendered = to_json(_STATS)
    assert rendered.endswith("\n")
    assert not rendered.endswith("\n\n")
    # `sort_keys=True` pone `bytes` antes que `column_bytes` antes que `files`.
    fact = json.loads(rendered)["tables"]["fact"]
    assert list(fact) == sorted(fact)


# ----------------------------------------------------------------- from_dict ---


def test_los_numeros_del_json_se_convierten_a_entero() -> None:
    """JSON no distingue `1` de `1.0`, y un `float` en los bytes rompe la suma.

    Peor: `partitions` viene de un fichero que un humano puede haber tocado. Los
    `int()` de `from_dict` son la frontera donde eso se normaliza.
    """
    recuperado = from_dict(
        {
            "profile": "p",
            "source": "s",
            "tables": {
                "t": {
                    "rows": 10.0,
                    "bytes": "400",
                    "files": 4.0,
                    "column_bytes": {"a": "100"},
                    "partitions": {"2026-08-01": {"rows": "5", "bytes": 7.0}},
                }
            },
        }
    )

    t = recuperado.table("t")
    assert t is not None
    assert (t.rows, t.bytes, t.files) == (10, 400, 4)
    assert t.column_bytes == {"a": 100}
    assert t.partitions == {"2026-08-01": {"rows": 5, "bytes": 7}}


def test_una_tabla_sin_columnas_ni_particiones_se_lee_vacia_y_no_falla() -> None:
    """`column_bytes` y `partitions` son opcionales; `rows`/`bytes`/`files` no.

    La asimetría es deliberada: una tabla sin índice de columnas se puede estimar
    (se cobra entera), pero una tabla sin `bytes` no se puede estimar en absoluto,
    y degradar eso a cero es la fuga que `G-BUDGET-ESCAPE` prohíbe.
    """
    recuperado = from_dict(
        {
            "profile": "p",
            "source": "s",
            "tables": {"t": {"rows": 1, "bytes": 2, "files": 3}},
        }
    )

    t = recuperado.table("t")
    assert t is not None
    assert t.column_bytes == {}
    assert t.partitions == {}
    assert t.partition_column is None


@pytest.mark.parametrize("falta", ["rows", "bytes", "files"])
def test_una_tabla_sin_su_tamano_falla_ruidosamente(falta: str) -> None:
    spec = {"rows": 1, "bytes": 2, "files": 3}
    del spec[falta]

    with pytest.raises(KeyError):
        from_dict({"profile": "p", "source": "s", "tables": {"t": spec}})


def test_el_fichero_sobrevive_a_la_vuelta_completa_por_disco(tmp_path) -> None:
    """`load()` es lo que el estimador ejecuta en cada arranque."""
    destino = tmp_path / "statistics.json"
    destino.write_text(to_json(_STATS), encoding="utf-8")

    recuperado = load(destino)

    assert recuperado.to_dict() == _STATS.to_dict()


# ---------------------------------------------------------- _partition_value ---


def test_una_particion_vacia_devuelve_texto_vacio() -> None:
    """Una tabla sin campos de partición no puede producir una clave inventada."""

    class _Vacio:
        def __iter__(self):
            return iter(())

    assert _partition_value(_Vacio(), "identity") == ""


def test_un_booleano_no_se_convierte_en_fecha() -> None:
    """`bool` es subclase de `int`, y `True` daría `1970-01-02`.

    Esa es la clase de conversión silenciosa que produjo el bug del `Record`: una
    clave con pinta de fecha contra la que ningún predicado casa, poda vacía, y el
    estimador cobrando cero. El `and not isinstance(raw, bool)` está por esto.
    """

    class _Record:
        def __init__(self, value: object) -> None:
            self._value = value

        def __iter__(self):
            return iter((self._value,))

    assert _partition_value(_Record(True), "identity") == "True"
    assert _partition_value(_Record(False), "identity") == "False"


def test_una_fecha_ya_construida_se_publica_en_iso() -> None:
    """pyiceberg puede entregar un `date` en vez de días desde la época."""

    class _Record:
        def __init__(self, value: object) -> None:
            self._value = value

        def __iter__(self):
            return iter((self._value,))

    assert _partition_value(_Record(dt.date(2026, 8, 1)), "identity") == "2026-08-01"
    # Y con cualquier otra transformación sigue siendo una fecha, no su `repr`.
    assert _partition_value(_Record(dt.date(2026, 8, 1)), "day") == "2026-08-01"


def test_solo_la_transformacion_identity_interpreta_el_entero_como_fecha() -> None:
    """Un `bucket[16]` que valga 19967 es el cubo 19967, no el 2024-09-01."""

    class _Record:
        def __init__(self, value: object) -> None:
            self._value = value

        def __iter__(self):
            return iter((self._value,))

    assert _partition_value(_Record(19967), "identity") == "2024-09-01"
    assert _partition_value(_Record(19967), "bucket[16]") == "19967"
    assert _partition_value(_Record(19967), "truncate[4]") == "19967"


def test_la_epoca_de_la_conversion_es_1970_01_01() -> None:
    """El día 0 es el 1 de enero de 1970 y no otro: un desfase de un día aquí
    desplaza TODAS las claves de partición y vacía la poda entera."""

    class _Record:
        def __init__(self, value: int) -> None:
            self._value = value

        def __iter__(self):
            return iter((self._value,))

    assert _partition_value(_Record(0), "identity") == "1970-01-01"
    assert _partition_value(_Record(1), "identity") == "1970-01-02"
