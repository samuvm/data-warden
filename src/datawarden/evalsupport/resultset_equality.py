"""Cuándo dos resultsets son «el mismo». Especificación: docs/spec/resultset-equality.md.

ESTADO: ROJO. Los tipos y la firma existen para que la suite falle POR LA ASERCIÓN
y no por un `ImportError` — un test que revienta al importar no prueba nada sobre
el comportamiento, solo que el fichero no está.

La implementación llega en el turno siguiente. `compare` devuelve hoy un motivo
`stub` que no coincide con NINGÚN motivo esperado, así que los doce casos fallan,
incluidos los que esperan desigualdad. Un stub que devolviera `equal=False` a secas
haría pasar en vacío la mitad de la suite, que es peor que no tenerla.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

Row = tuple[Any, ...]


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

ResultSet = list[Row] | _Outcome


@dataclass(frozen=True, slots=True)
class Comparison:
    """El veredicto y POR QUÉ.

    `reason` no es un extra: sin él, sesenta casos en rojo no dicen en qué se
    diferencian, y depurar la métrica cuesta más que calcularla.
    """

    equal: bool
    reason: str


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
        El veredicto. HOY siempre `stub`, que es el motivo de que los doce casos
        estén en rojo.
    """
    # ROJO. Devolver un veredicto en vez de lanzar `NotImplementedError` es
    # deliberado: la fase roja exige que los tests fallen POR LA ASERCIÓN, no por
    # una excepción, porque un test que revienta antes de comparar no ha probado
    # que la comparación esté mal — solo que no existe. Y `NotImplementedError`
    # consumiría además presupuesto de deuda declarada.
    return Comparison(equal=False, reason="stub")
