"""Las estadísticas, en memoria. Zona test-after, cobertura 90 %.

Lo que se prueba es lo que hace útil al estimador y no lo que hace bonito al
fichero: que `bytes_of` cobre **la tabla entera cuando no se proyecta ninguna
columna** —el caso de `count(*)`, que es la mitad de las consultas de este almacén—
y que el JSON sobreviva a una vuelta completa, porque el estimador lo lee de disco
en cada arranque y un campo perdido ahí no daría error: daría un coste más bajo.
"""

from __future__ import annotations

import json

from datawarden.catalog.statistics import (
    Statistics,
    TableStats,
    from_dict,
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
    from datawarden.catalog.statistics import _partition_value

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
    from datawarden.catalog.statistics import _partition_value

    class _Record:
        def __init__(self, value: object) -> None:
            self._value = value

        def __iter__(self):
            return iter((self._value,))

    assert _partition_value(_Record("ES"), "identity") == "ES"
    assert _partition_value(_Record(7), "bucket[16]") == "7"
    assert _partition_value(_Record(None), "identity") == "None"
