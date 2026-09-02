"""La pimienta del enmascarado, y la comprobación de que el contrato sigue diciendo
lo que decía cuando se firmó.

**Este módulo puede negarse a arrancar, y es su razón de ser.**
`docs/spec/policy.yaml` —firmado por Samuel el 2026-09-01— declara
`determinista: true` y `pimienta_desde: config`, con el comentario *«nunca sal por
sesión ni por día»*. `docs/PLAN.md` dice lo contrario en la fila de la fase 4
—«hash truncado con sal por sesión»— y esa contradicción está abierta en P-006.

Manda el contrato firmado, y no por antigüedad: porque trae escrito su motivo y el
motivo es verificable. Con sal por sesión, el resultset de toda consulta con una
columna `mask` deja de ser comparable entre ejecuciones, y **las preguntas del banco
de 60 que toquen una columna enmascarada no podrían tener respuesta de referencia**.
Se descubriría en la fase 8, con el banco ya escrito y con 10-20 horas de Samuel
gastadas en escribirlo.

Comprobar el contrato aquí, al cargar, es lo que impide que esa decisión vuelva por
la puerta de atrás: si alguien cambia el YAML o una versión futura del compilador
deja de emitir el campo, esto **para** en vez de seguir con otro comportamiento.

**Qué se gana y qué se pierde con una pimienta fija, dicho aquí.** Se gana poder
agrupar y contar sin revelar, y respuestas de referencia estables. Se pierde
resistencia a la correlación: el mismo valor produce siempre el mismo hash, así que
quien conozca un valor lo reconoce en el resultset, y quien tenga la pimienta puede
construir un diccionario y revertir cualquier columna de cardinalidad baja. Está en
`docs/threat-model.md §4.2` en vez de disimulado.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

from datawarden.principal.policy import AccessPolicy

#: De dónde sale la pimienta en un despliegue. No hay valor por defecto y no lo va a
#: haber: una pimienta por defecto es una pimienta pública, y `check_secrets.py`
#: marcaría con razón cualquier literal que se pareciera a un secreto en el código.
PEPPER_ENV: Final = "DATAWARDEN_MASK_PEPPER"

#: Longitud mínima. 32 caracteres no es un número mágico: es lo que hace que la
#: pimienta no se pueda forzar por fuerza bruta cuando el atacante ya conoce el
#: algoritmo —que lo conoce, está publicado en `docs/spec/policy.yaml`— y tiene un
#: par (valor, hash) de una columna de cardinalidad baja.
MIN_PEPPER_LENGTH: Final = 32


@dataclass(frozen=True, slots=True)
class MaskConfig:
    """La configuración del enmascarado. Hoy es la pimienta y nada más."""

    pepper: str

    def __post_init__(self) -> None:
        if not self.pepper.strip():
            message = (
                "la pimienta del enmascarado está vacía. Sin ella, `sha256(valor)` "
                "es un diccionario público: las columnas que este sistema hashea "
                "son de cardinalidad acotada —una IP, una huella de dispositivo— y "
                "cualquiera con la tabla de hashes las revierte. Una pimienta vacía "
                f"no es menos seguridad, es ninguna. Define {PEPPER_ENV}."
            )
            raise ValueError(message)
        if len(self.pepper) < MIN_PEPPER_LENGTH:
            message = (
                f"la pimienta tiene {len(self.pepper)} caracteres y el mínimo es "
                f"{MIN_PEPPER_LENGTH}. El algoritmo está publicado en "
                "docs/spec/policy.yaml, así que lo único que separa el hash del "
                "valor original es esto."
            )
            raise ValueError(message)


def load_mask_config(policy: AccessPolicy, *, pepper: str | None = None) -> MaskConfig:
    """La configuración, COMPROBANDO antes que el contrato dice lo que decía.

    Se pasa `pepper` explícitamente en los tests y en el gate; en un despliegue sale
    de la variable de entorno. No hay valor por defecto a propósito: enmascarar con
    una pimienta que todo el mundo conoce es peor que no enmascarar, porque produce
    la apariencia de protección sin la protección.
    """
    if not policy.deterministic_masking:
        message = (
            "docs/spec/policy.yaml declara `determinista: false` y este enmascarador "
            "no funciona así. El contrato FIRMADO el 2026-09-01 dice `determinista: "
            "true` y `pimienta_desde: config`, con su motivo escrito: con sal por "
            "sesión, las preguntas del banco de 60 que tocan una columna enmascarada "
            "no pueden tener respuesta de referencia, y eso se descubre en la fase 8 "
            "con el banco ya escrito. Cambiar esta decisión se firma —ver P-006 en "
            "docs/PARA-SAMUEL.md—, no se hereda de un valor por defecto."
        )
        raise ValueError(message)
    if policy.pepper_from != "config":
        message = (
            f"docs/spec/policy.yaml declara `pimienta_desde: {policy.pepper_from!r}` "
            "y este enmascarador solo admite `config`. El contrato firmado dice "
            "literalmente «nunca sal por sesión ni por día»."
        )
        raise ValueError(message)
    return MaskConfig(pepper=pepper if pepper is not None else os.environ.get(PEPPER_ENV, ""))
