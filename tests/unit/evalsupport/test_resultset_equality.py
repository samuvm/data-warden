"""Los doce casos de `G-RESULTSET-EQ`. Especificación: docs/spec/resultset-equality.md.

FASE ROJA. Los doce fallan a propósito, y fallan POR LA ASERCIÓN: `compare`
devuelve hoy `reason='stub'`, que no coincide con ningún motivo esperado. Por eso
cada caso asierta sobre `equal` Y sobre `reason` — un test que solo mirase `equal`
pasaría en vacío en los seis casos que esperan desigualdad, y media suite verde
por accidente es peor que ninguna suite.

Un caso por decisión de la especificación, numerados igual que allí.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from datawarden.evalsupport.resultset_equality import REJECTED, TIMEOUT, compare


def test_01_orden_de_filas_se_ignora_sin_order_by() -> None:
    """Sin `ORDER BY`, SQL no garantiza orden: exigirlo penalizaría un acierto."""
    r = compare([(2, "b"), (1, "a")], [(1, "a"), (2, "b")])
    assert r.equal, r.reason
    assert r.reason == "equal"


def test_02_orden_de_filas_importa_con_order_by() -> None:
    """Con `ORDER BY` en la referencia, el orden ES parte de la respuesta."""
    r = compare([(2, "b"), (1, "a")], [(1, "a"), (2, "b")], ordered=True)
    assert not r.equal
    assert r.reason == "row_order"


def test_03_orden_de_columnas_se_ignora() -> None:
    """`SELECT a, b` y `SELECT b, a` responden lo mismo."""
    r = compare([("a", 1)], [(1, "a")])
    assert r.equal, r.reason
    assert r.reason == "equal"


def test_04_tolerancia_en_flotantes() -> None:
    """El error de redondeo depende del plan de ejecución, no de la corrección."""
    r = compare([(0.1 + 0.2,)], [(0.3,)])
    assert r.equal, r.reason
    assert r.reason == "equal"


def test_05_decimal_y_float_son_iguales_si_el_valor_coincide() -> None:
    """DuckDB devuelve DECIMAL o DOUBLE según cómo se escriba la misma respuesta."""
    r = compare([(Decimal("42.50"),)], [(42.5,)])
    assert r.equal, r.reason
    assert r.reason == "equal"


def test_06_null_no_es_cadena_vacia() -> None:
    """La decisión más discutida. Este almacén tiene NULLs que SIGNIFICAN algo."""
    r = compare([(None,)], [("",)])
    assert not r.equal
    assert r.reason == "null_vs_empty"


def test_06b_null_es_igual_a_null() -> None:
    r = compare([(None, 1)], [(None, 1)])
    assert r.equal, r.reason
    assert r.reason == "equal"


def test_07_entero_no_es_su_representacion_en_texto() -> None:
    """El otro caso discutible: un tipo distinto es una respuesta distinta."""
    r = compare([(1,)], [("1",)])
    assert not r.equal
    assert r.reason == "cell_type"


def test_08_vacio_es_igual_a_vacio_con_la_misma_forma() -> None:
    r = compare([], [])
    assert r.equal, r.reason
    assert r.reason == "equal"


def test_09_duplicados_son_multiset_no_conjunto() -> None:
    """Deduplicar o no es una de las trampas centrales del dataset."""
    r = compare([(1,), (1,)], [(1,)])
    assert not r.equal
    assert r.reason == "row_count"


def test_10_temporales_se_normalizan_a_utc() -> None:
    """Un desfase de zona es un error del sistema, no de la consulta."""
    naive = dt.datetime(2026, 8, 31, 12, 0, 0)
    aware = dt.datetime(2026, 8, 31, 12, 0, 0, tzinfo=dt.UTC)
    r = compare([(naive,)], [(aware,)])
    assert r.equal, r.reason
    assert r.reason == "equal"


def test_11_un_timeout_es_un_fallo_no_una_exclusion() -> None:
    """Sacarlo del denominador subiría la exactitud escondiendo el problema."""
    r = compare(TIMEOUT, [(1,)])
    assert not r.equal
    assert r.reason == "timeout"


def test_12_un_rechazo_esperado_es_un_acierto() -> None:
    """Diez de las sesenta preguntas del banco esperan rechazo como respuesta."""
    r = compare(REJECTED, REJECTED)
    assert r.equal, r.reason
    assert r.reason == "expected_rejection"


def test_12b_un_rechazo_no_esperado_es_un_fallo() -> None:
    r = compare(REJECTED, [(1,)])
    assert not r.equal
    assert r.reason == "rejected"
