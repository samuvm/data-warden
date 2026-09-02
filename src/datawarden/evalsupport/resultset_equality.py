"""Cuándo dos resultsets son «el mismo». Especificación: docs/spec/resultset-equality.md.

Esta función es la que convierte `G-EXEC-ACC` en un número reproducible. Sin ella,
«exactitud de ejecución >= 0,80» significa lo que cada uno entienda, y dos personas
publican dos cifras distintas con el mismo nombre.

Tres cosas que NO hace, y son deliberadas:

- **No compara SQL.** Ni una sola línea de este módulo mira una cadena de consulta.
  Dos consultas escritas de forma completamente distinta pueden dar el mismo
  resultado, y eso es exactamente lo que se quiere medir.
- **No inventa la forma de un resultset vacío.** Una lista de filas vacía no lleva
  aridad; cuando hace falta compararla se pasa una `Table`, que sí la lleva (P-2).
- **No trata un timeout ni un rechazo como «cero filas».** Son respuestas distintas
  con consecuencias distintas en el numerador y en el denominador.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Final

Row = tuple[Any, ...]

#: Tolerancias de la decisión 4. Un `sum()` sobre 66 M de filas acumula redondeo
#: distinto según el orden de agregación, y ese orden lo decide el plan, no la
#: corrección de la consulta.
REL_TOL: Final = 1e-9
ABS_TOL: Final = 1e-12

# Clases de tipo de la precisión P-1. Lo que no se puede confundir es TEXTO con
# NÚMERO, no INTEGER con DOUBLE.
_NUMBER: Final = "number"
_BOOL: Final = "bool"
_TEXT: Final = "text"
_BYTES: Final = "bytes"
_NULL: Final = "null"
_DATE: Final = "date"
_DATETIME: Final = "datetime"
_TIME: Final = "time"
_OTHER: Final = "other"


class _Outcome:
    """Un resultado que NO es un conjunto de filas.

    Un timeout y un rechazo del guard son respuestas legítimas de este sistema, y
    representarlos como «lista vacía» los haría indistinguibles entre sí y de una
    consulta que simplemente no devolvió nada. La especificación los trata de
    forma distinta en el numerador y en el denominador, así que tienen que ser
    distinguibles en el tipo.
    """

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        self._name = name

    def __repr__(self) -> str:
        return f"<{self._name}>"


#: La consulta no terminó. Cuenta como fallo Y entra en el denominador.
TIMEOUT = _Outcome("timeout")
#: El guard rechazó la consulta. Acierto o fallo según lo que esperase el caso.
REJECTED = _Outcome("rejected")


@dataclass(frozen=True, slots=True)
class Table:
    """Un resultset que además lleva su forma.

    Existe por dos motivos que una `list[tuple]` no puede cubrir: la decisión 8
    exige comparar el número de columnas de dos vacíos, y `strict_names` exige los
    nombres. Cuando la forma no está en duda, una lista pelada sigue valiendo.
    """

    columns: tuple[str, ...]
    rows: Sequence[Row]


ResultSet = Sequence[Row] | Table | _Outcome


@dataclass(frozen=True, slots=True)
class Comparison:
    """El veredicto y POR QUÉ.

    `reason` no es un extra: sin él, sesenta casos en rojo no dicen en qué se
    diferencian, y depurar la métrica cuesta más que calcularla.
    """

    equal: bool
    reason: str


@dataclass(frozen=True, slots=True)
class _Shape:
    """Forma normalizada de un lado de la comparación."""

    names: tuple[str, ...] | None
    n_cols: int | None
    rows: list[Row]


def compare(
    actual: ResultSet,
    expected: ResultSet,
    *,
    ordered: bool = False,
    strict_names: bool = False,
) -> Comparison:
    """Compara dos resultsets según `docs/spec/resultset-equality.md`.

    Args:
        actual: Lo que devolvió la consulta generada.
        expected: Lo que devuelve el SQL de referencia.
        ordered: La referencia lleva `ORDER BY`, así que el orden de filas cuenta.
        strict_names: La pregunta pedía un nombre de columna concreto.

    Returns:
        El veredicto, con uno de los motivos declarados en la especificación.
    """
    # Dos `if` separados y no un `or`: es lo que deja que el verificador de tipos
    # estreche `actual` y `expected` a resultsets de filas en todo lo que sigue.
    if isinstance(expected, _Outcome):
        return _outcome_verdict(actual, expected)
    if isinstance(actual, _Outcome):
        return _outcome_verdict(actual, expected)

    left = _shape_of(actual)
    right = _shape_of(expected)
    if left is None or right is None:
        return Comparison(equal=False, reason="column_count")

    if left.n_cols is not None and right.n_cols is not None and left.n_cols != right.n_cols:
        return Comparison(equal=False, reason="column_count")
    if len(left.rows) != len(right.rows):
        return Comparison(equal=False, reason="row_count")

    if strict_names:
        aligned = _align_by_name(left, right)
        if aligned is None:
            return Comparison(equal=False, reason="column_names")
        left, right = aligned
    else:
        left = _align_by_content(left)
        right = _align_by_content(right)

    if not ordered:
        return _compare_rows(_sorted_rows(left.rows), _sorted_rows(right.rows))

    verdict = _compare_rows(left.rows, right.rows)
    if verdict.equal:
        return verdict
    # Un fallo con `ordered` puede ser «las mismas filas en otro orden», que es un
    # diagnóstico muy distinto de «hay una celda mal». Merece motivo propio.
    if _compare_rows(_sorted_rows(left.rows), _sorted_rows(right.rows)).equal:
        return Comparison(equal=False, reason="row_order")
    return verdict


def _outcome_verdict(actual: ResultSet, expected: ResultSet) -> Comparison:
    """Veredicto cuando alguno de los dos lados no es un conjunto de filas."""
    # P-4: el timeout gana a todo. Una consulta que no termina no es una respuesta.
    if actual is TIMEOUT or expected is TIMEOUT:
        return Comparison(equal=False, reason="timeout")
    # P-5: `expected_rejection` es el motivo del CASO; `equal` dice si ocurrió.
    if expected is REJECTED:
        return Comparison(equal=actual is REJECTED, reason="expected_rejection")
    return Comparison(equal=False, reason="rejected")


def _shape_of(rs: Sequence[Row] | Table) -> _Shape | None:
    """Normaliza cualquier forma de resultset. `None` si las filas son irregulares."""
    if isinstance(rs, Table):
        rows = [tuple(r) for r in rs.rows]
        names: tuple[str, ...] | None = tuple(rs.columns)
        n_cols: int | None = len(rs.columns)
    else:
        rows = [tuple(r) for r in rs]
        names = None
        n_cols = len(rows[0]) if rows else None
    if any(len(r) != n_cols for r in rows):
        return None
    return _Shape(names=names, n_cols=n_cols, rows=rows)


def _align_by_name(left: _Shape, right: _Shape) -> tuple[_Shape, _Shape] | None:
    """Empareja columnas por nombre. `None` si no se puede verificar el nombre."""
    if left.names is None or right.names is None:
        return None
    if sorted(left.names) != sorted(right.names):
        return None
    return _reorder(left, _order_by(left.names)), _reorder(right, _order_by(right.names))


def _order_by(names: tuple[str, ...]) -> list[int]:
    return sorted(range(len(names)), key=lambda i: (names[i], i))


def _align_by_content(shape: _Shape) -> _Shape:
    """Canonicaliza el orden de columnas por su contenido (P-3).

    La clave de cada columna es su MULTICONJUNTO de valores, luego no depende del
    orden de filas; y la reordenación permuta columnas enteras, así que ninguna
    fila se desempareja.
    """
    if shape.n_cols is None or shape.n_cols <= 1:
        return shape
    keys = [(sorted(_cell_key(row[i]) for row in shape.rows), i) for i in range(shape.n_cols)]
    return _reorder(shape, [i for _, i in sorted(keys)])


def _reorder(shape: _Shape, order: list[int]) -> _Shape:
    names = tuple(shape.names[i] for i in order) if shape.names is not None else None
    return _Shape(
        names=names,
        n_cols=shape.n_cols,
        rows=[tuple(row[i] for i in order) for row in shape.rows],
    )


def _sorted_rows(rows: list[Row]) -> list[Row]:
    return sorted(rows, key=lambda r: tuple(_cell_key(c) for c in r))


def _compare_rows(left: list[Row], right: list[Row]) -> Comparison:
    for lrow, rrow in zip(left, right, strict=True):
        for lcell, rcell in zip(lrow, rrow, strict=True):
            reason = _cell_mismatch(lcell, rcell)
            if reason is not None:
                return Comparison(equal=False, reason=reason)
    return Comparison(equal=True, reason="equal")


def _cell_mismatch(left: Any, right: Any) -> str | None:
    """Motivo por el que dos celdas difieren, o `None` si son iguales."""
    lclass = _cell_class(left)
    rclass = _cell_class(right)
    if lclass != rclass:
        # P-6: NULL frente a cadena vacía tiene motivo propio porque es un error de
        # significado; NULL frente a un número es sencillamente otra clase.
        if {lclass, rclass} == {_NULL, _TEXT} and "" in (left, right):
            return "null_vs_empty"
        return "cell_type"
    if lclass == _NULL:
        return None
    if lclass == _NUMBER:
        return None if _numbers_close(left, right) else "cell_value"
    if lclass == _DATETIME:
        return None if _normalize_temporal(left) == _normalize_temporal(right) else "cell_value"
    if lclass == _OTHER and type(left) is not type(right):
        return "cell_type"
    return None if _safe_eq(left, right) else "cell_value"


def _cell_class(value: Any) -> str:
    if value is None:
        return _NULL
    if isinstance(value, bool):
        return _BOOL
    if isinstance(value, int | float | Decimal):
        return _NUMBER
    if isinstance(value, str):
        return _TEXT
    if isinstance(value, bytes | bytearray | memoryview):
        return _BYTES
    if isinstance(value, dt.datetime):
        return _DATETIME
    if isinstance(value, dt.date):
        return _DATE
    if isinstance(value, dt.time):
        return _TIME
    return _OTHER


def _numbers_close(left: Any, right: Any) -> bool:
    """Decisión 4, con el borde que la tolerancia sola no cubre.

    `float(Decimal("1e400"))` NO lanza: devuelve `inf`. Y `isclose(inf, inf)` es
    cierto, así que dos importes distintos y ambos fuera del rango de `float`
    saldrían iguales. Lo encontró un test, no una revisión: por eso lo no finito
    se compara en su tipo original y no por tolerancia.
    """
    lf = _as_float(left)
    rf = _as_float(right)
    if lf is None or rf is None or not (math.isfinite(lf) and math.isfinite(rf)):
        if _is_nan(left) and _is_nan(right):
            # Dos NaN son la misma respuesta aunque SQL diga que no son iguales:
            # aquí se compara estructura, no se evalúa un predicado.
            return True
        return _safe_eq(left, right)
    return math.isclose(lf, rf, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def _safe_eq(left: Any, right: Any) -> bool:
    """Igualdad que no puede reventar la comparación de sesenta casos.

    `Decimal("sNaN") == 1` LANZA `InvalidOperation`: un NaN señalizador está
    diseñado para gritar en cuanto alguien lo mira. Que la métrica de exactitud se
    caiga entera por una celda es peor que declararla distinta, así que lo que no
    se puede comparar se declara distinto.
    """
    try:
        return bool(left == right)
    except (ArithmeticError, TypeError, ValueError):
        return False


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (OverflowError, ValueError, InvalidOperation):
        return None


def _is_nan(value: Any) -> bool:
    as_float = _as_float(value)
    return as_float is not None and math.isnan(as_float)


def _normalize_temporal(value: dt.datetime) -> dt.datetime:
    """Decisión 10: un `TIMESTAMP` sin zona ES UTC; con zona, se convierte a UTC.

    Solo recibe `datetime`: un `DATE` es fecha civil y no se convierte, y un `TIME`
    se compara tal cual. Estrechar el tipo aquí es lo que evita una rama que nadie
    ejecuta nunca y que la cobertura señalaría para siempre.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def _cell_key(value: Any) -> tuple[str, str]:
    """Clave total y determinista para ordenar filas y columnas.

    Los números se redondean a 12 cifras significativas ANTES de formar la clave:
    si no, `0.1 + 0.2` y `0.3` se ordenarían en sitios distintos y la comparación
    de multiconjuntos emparejaría filas equivocadas justo en el caso que la
    tolerancia de la decisión 4 existe para tapar.
    """
    cls = _cell_class(value)
    if cls == _NULL:
        return (cls, "")
    if cls == _NUMBER:
        as_float = _as_float(value)
        if as_float is None or not math.isfinite(as_float):
            return (cls, str(value))
        return (cls, f"{as_float:.12g}")
    if cls == _DATETIME:
        return (cls, str(_normalize_temporal(value)))
    if cls in (_DATE, _TIME):
        return (cls, str(value))
    if cls == _TEXT:
        return (cls, str(value))
    return (cls, repr(value))
