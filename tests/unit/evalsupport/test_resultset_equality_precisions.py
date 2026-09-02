"""Las seis precisiones P-1..P-6 de `docs/spec/resultset-equality.md`.

Los catorce casos de `test_resultset_equality.py` son las doce decisiones de la
especificación y se escribieron ANTES del código. Estos son los huecos que solo
aparecieron al implementar: se escribieron en la especificación antes de tocar el
código, pero sus tests son posteriores, y eso está declarado en `JOURNAL.md` en vez
de presentarse como TDD.

Aquí se ejercita además la superficie pública que los catorce no tocan —`Table`— y
los cinco motivos que ningún caso de la especificación producía: `column_count`,
`column_names`, `cell_type` por clase temporal, `cell_value` con `ordered`, y el
camino en que un vacío SÍ lleva su forma.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from datawarden.evalsupport.resultset_equality import (
    REJECTED,
    TIMEOUT,
    Comparison,
    Table,
    compare,
)

# --------------------------------------------------------------------------- P-1


def test_p1_un_booleano_no_es_el_entero_uno() -> None:
    """DuckDB devuelve BOOLEAN donde una consulta mal escrita devuelve 1."""
    r = compare([(True,)], [(1,)])
    assert not r.equal
    assert r.reason == "cell_type"


def test_p1_dos_booleanos_iguales_son_iguales() -> None:
    assert compare([(True,), (False,)], [(False,), (True,)]) == Comparison(True, "equal")


def test_p1_int_y_float_son_la_misma_clase() -> None:
    """`count(*)` devuelve INTEGER y una división DOUBLE: misma respuesta."""
    r = compare([(42,)], [(42.0,)])
    assert r.equal, r.reason


def test_p1_decimal_y_entero_son_la_misma_clase() -> None:
    assert compare([(Decimal("7"),)], [(7,)]).equal


def test_p1_una_fecha_no_es_una_marca_de_tiempo() -> None:
    r = compare([(dt.date(2026, 8, 31),)], [(dt.datetime(2026, 8, 31, 0, 0),)])
    assert not r.equal
    assert r.reason == "cell_type"


def test_p1_dos_fechas_civiles_se_comparan_sin_convertir() -> None:
    assert compare([(dt.date(2026, 8, 31),)], [(dt.date(2026, 8, 31),)]).equal


def test_p1_dos_horas_se_comparan_por_igualdad() -> None:
    r = compare([(dt.time(12, 0),)], [(dt.time(12, 1),)])
    assert not r.equal
    assert r.reason == "cell_value"


def test_p1_bytes_se_comparan_exactos() -> None:
    assert compare([(b"abc",)], [(b"abc",)]).equal
    assert compare([(b"abc",)], [(b"abd",)]).reason == "cell_value"


def test_p1_texto_distinto_es_cell_value_no_cell_type() -> None:
    r = compare([("a",)], [("b",)])
    assert not r.equal
    assert r.reason == "cell_value"


def test_p1_dos_objetos_de_tipos_distintos_fuera_de_las_clases() -> None:
    """La clase `other` compara por `==` y además exige el mismo tipo exacto."""
    r = compare([({"a": 1},)], [([1],)])
    assert not r.equal
    assert r.reason == "cell_type"


def test_p1_dos_objetos_del_mismo_tipo_fuera_de_las_clases() -> None:
    assert compare([({"a": 1},)], [({"a": 1},)]).equal
    assert compare([({"a": 1},)], [({"a": 2},)]).reason == "cell_value"


def test_p1_un_decimal_fuera_del_rango_de_float_no_revienta() -> None:
    """`float(Decimal('1e400'))` desborda; el contrato cae a igualdad exacta."""
    grande = Decimal("1e400")
    assert compare([(grande,)], [(grande,)]).equal
    assert not compare([(grande,)], [(Decimal("1e401"),)]).equal


# --------------------------------------------------------------------------- P-2


def test_p2_dos_vacios_sin_forma_son_iguales_porque_no_hay_forma_que_comparar() -> None:
    assert compare([], []).equal


def test_p2_dos_vacios_con_la_misma_forma_son_iguales() -> None:
    assert compare(Table(("a", "b"), []), Table(("x", "y"), [])).equal


def test_p2_un_vacio_de_tres_columnas_no_es_un_vacio_de_una() -> None:
    """La decisión 8 en su caso interesante: la segunda perdió una columna."""
    r = compare(Table(("a", "b", "c"), []), Table(("x",), []))
    assert not r.equal
    assert r.reason == "column_count"


def test_p2_una_tabla_con_filas_se_compara_como_una_lista() -> None:
    assert compare(Table(("a",), [(1,)]), [(1,)]).equal


def test_p2_distinto_numero_de_columnas_con_filas() -> None:
    r = compare([(1, 2)], [(1,)])
    assert not r.equal
    assert r.reason == "column_count"


def test_p2_filas_irregulares_son_un_resultset_malformado() -> None:
    r = compare([(1, 2), (3,)], [(1, 2), (3, 4)])
    assert not r.equal
    assert r.reason == "column_count"


# --------------------------------------------------------------------------- P-3


def test_p3_el_orden_de_columnas_se_canonicaliza_con_varias_filas() -> None:
    izquierda = [("a", 1), ("b", 2)]
    derecha = [(1, "a"), (2, "b")]
    assert compare(izquierda, derecha).equal


def test_p3_la_reordenacion_no_desempareja_filas() -> None:
    """El fallo que este diseño existe para evitar: mismas columnas, otro emparejado."""
    r = compare([(1, "a"), (2, "b")], [(1, "b"), (2, "a")])
    assert not r.equal
    assert r.reason == "cell_value"


def test_p3_la_clave_de_columna_no_depende_del_orden_de_filas() -> None:
    assert compare([("a", 1), ("b", 2)], [(2, "b"), (1, "a")]).equal


def test_p3_una_sola_columna_no_se_reordena() -> None:
    assert compare([(1,), (2,)], [(2,), (1,)]).equal


# --------------------------------------------------------------------------- P-5


def test_p5_esperar_rechazo_y_recibir_filas_es_fallo_con_el_mismo_motivo() -> None:
    r = compare([(1,)], REJECTED)
    assert not r.equal
    assert r.reason == "expected_rejection"


def test_p5_un_timeout_gana_al_rechazo_esperado() -> None:
    r = compare(TIMEOUT, REJECTED)
    assert not r.equal
    assert r.reason == "timeout"


def test_p5_un_timeout_en_la_referencia_tambien_es_timeout() -> None:
    assert compare([(1,)], TIMEOUT).reason == "timeout"


# --------------------------------------------------------------------------- P-6


def test_p6_nulo_frente_a_un_numero_es_cell_type_no_null_vs_empty() -> None:
    r = compare([(None,)], [(42,)])
    assert not r.equal
    assert r.reason == "cell_type"


def test_p6_nulo_frente_a_texto_no_vacio_es_cell_type() -> None:
    r = compare([(None,)], [("x",)])
    assert not r.equal
    assert r.reason == "cell_type"


def test_p6_cadena_vacia_frente_a_nulo_en_el_otro_orden() -> None:
    r = compare([("",)], [(None,)])
    assert not r.equal
    assert r.reason == "null_vs_empty"


# ------------------------------------------------------- nombres y orden de fila


def test_strict_names_empareja_por_nombre_y_no_por_contenido() -> None:
    izquierda = Table(("total", "pais"), [(10, "ES")])
    derecha = Table(("pais", "total"), [("ES", 10)])
    assert compare(izquierda, derecha, strict_names=True).equal


def test_strict_names_detecta_un_alias_distinto() -> None:
    izquierda = Table(("n",), [(10,)])
    derecha = Table(("ingresos",), [(10,)])
    r = compare(izquierda, derecha, strict_names=True)
    assert not r.equal
    assert r.reason == "column_names"


def test_strict_names_sin_nombres_no_se_puede_verificar() -> None:
    """Una lista pelada no lleva nombres: se dice, no se finge que coinciden."""
    r = compare([(10,)], Table(("ingresos",), [(10,)]), strict_names=True)
    assert not r.equal
    assert r.reason == "column_names"


def test_strict_names_pilla_un_valor_mal_aunque_los_nombres_casen() -> None:
    izquierda = Table(("a", "b"), [(1, 2)])
    derecha = Table(("a", "b"), [(1, 3)])
    r = compare(izquierda, derecha, strict_names=True)
    assert not r.equal
    assert r.reason == "cell_value"


def test_ordered_con_una_celda_mal_no_es_row_order() -> None:
    """`row_order` es un diagnóstico, no un cajón de sastre."""
    r = compare([(1,), (2,)], [(1,), (3,)], ordered=True)
    assert not r.equal
    assert r.reason == "cell_value"


def test_ordered_en_verde_devuelve_equal() -> None:
    assert compare([(1,), (2,)], [(1,), (2,)], ordered=True).equal


def test_ordered_distinto_numero_de_filas_es_row_count() -> None:
    r = compare([(1,)], [(1,), (2,)], ordered=True)
    assert not r.equal
    assert r.reason == "row_count"


@pytest.mark.parametrize(
    ("izquierda", "derecha", "motivo"),
    [
        ([(1,)], [(1,), (1,)], "row_count"),
        ([(1, 2)], [(1,)], "column_count"),
        ([(1,)], [("1",)], "cell_type"),
        ([(1,)], [(2,)], "cell_value"),
        ([(None,)], [("",)], "null_vs_empty"),
    ],
)
def test_todos_los_motivos_declarados_son_alcanzables(
    izquierda: list[tuple[object, ...]],
    derecha: list[tuple[object, ...]],
    motivo: str,
) -> None:
    """Un motivo que ningún caso produce es un motivo que nadie ha comprobado."""
    assert compare(izquierda, derecha).reason == motivo


def test_las_marcas_de_tiempo_con_zona_se_normalizan_a_utc() -> None:
    madrid = dt.timezone(dt.timedelta(hours=2))
    con_zona = dt.datetime(2026, 8, 31, 14, 0, tzinfo=madrid)
    en_utc = dt.datetime(2026, 8, 31, 12, 0, tzinfo=dt.UTC)
    assert compare([(con_zona,)], [(en_utc,)]).equal


def test_los_marcadores_se_identifican_en_su_repr() -> None:
    """`repr` sale en el informe de 60 casos; si no dice cuál es, no sirve."""
    assert repr(TIMEOUT) == "<timeout>"
    assert repr(REJECTED) == "<rejected>"


def test_p1_dos_nan_son_la_misma_respuesta() -> None:
    """SQL dice que NaN != NaN; aquí se compara estructura, no se evalúa un predicado."""
    nan = float("nan")
    assert compare([(nan,)], [(nan,)]).equal


def test_p1_infinito_frente_a_un_numero_finito_no_es_igual() -> None:
    assert not compare([(float("inf"),)], [(1e308,)]).equal


def test_p1_un_decimal_no_convertible_a_float_no_revienta() -> None:
    """`float(Decimal('sNaN'))` lanza `InvalidOperation`, no devuelve NaN."""
    senal = Decimal("sNaN")
    r = compare([(senal,)], [(1,)])
    assert not r.equal
    assert r.reason == "cell_value"
