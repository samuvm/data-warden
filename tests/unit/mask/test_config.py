"""La pimienta del enmascarado. Zona TDD obligatorio, cobertura 95 %.

FASE ROJA.

**Aquí se ejecuta una decisión firmada, y por eso este módulo puede negarse a
arrancar.** `docs/spec/policy.yaml`, firmado por Samuel el 2026-09-01, dice
`determinista: true` y `pimienta_desde: config`, con el comentario `# nunca sal por
sesión ni por día`. `docs/PLAN.md` dice lo contrario —«hash truncado con sal por
sesión»— y esa contradicción está en P-006 esperando a que Samuel corrija el plan.

Mientras tanto manda el contrato firmado, porque su motivo es verificable: con sal
por sesión, el resultset de toda consulta con una columna `mask` deja de ser
comparable entre ejecuciones, y **las preguntas del banco de 60 que toquen una
columna enmascarada no podrían tener respuesta de referencia**. Se descubriría en la
fase 8, con el banco ya escrito y 10-20 horas de Samuel gastadas.

Para que esa decisión no pueda volver por la puerta de atrás, el enmascarador
**comprueba el contrato al cargarse y se niega a funcionar si no dice lo que decía**.
"""

from __future__ import annotations

import pytest

from datawarden.mask.config import MaskConfig, load_mask_config
from datawarden.principal.policy import policy_from_dict

_POLICY_OK: dict[str, object] = {
    "default_level": "allow",
    "deterministic_masking": True,
    "pepper_from": "config",
    "columns": {},
}


def _policy(**cambios: object):
    payload = dict(_POLICY_OK)
    payload.update(cambios)
    return policy_from_dict(payload)


# ------------------------------------------------------------------- la pimienta ---


def test_una_pimienta_normal_se_acepta() -> None:
    assert MaskConfig(pepper="una-pimienta-larga-de-verdad-y-suficiente").pepper


@pytest.mark.parametrize("valor", ["", "   ", "\t\n"])
def test_una_pimienta_vacia_no_se_puede_construir(valor: str) -> None:
    """Sin pimienta, `sha256(valor)` es un diccionario público.

    Las columnas que este sistema hashea son de cardinalidad baja o acotada —una IP,
    una huella de dispositivo—, así que cualquiera con la tabla de hashes las
    revierte. Una pimienta vacía no es «menos seguridad»: es ninguna.
    """
    with pytest.raises(ValueError, match="pimienta"):
        MaskConfig(pepper=valor)


def test_una_pimienta_demasiado_corta_no_se_puede_construir() -> None:
    """Doce caracteres se fuerzan; el mínimo declarado es 32."""
    with pytest.raises(ValueError, match="32"):
        MaskConfig(pepper="corta")


# --------------------------------------------- el contrato firmado, comprobado ---


def test_la_configuracion_sale_de_la_politica_firmada() -> None:
    config = load_mask_config(_policy(), pepper="x" * 40)
    assert config.pepper == "x" * 40


def test_se_niega_a_funcionar_si_la_politica_no_declara_enmascarado_determinista() -> None:
    """La puerta de atrás que P-006 no puede reabrir por descuido.

    Si alguien pusiera `determinista: false` en el contrato —o si una versión futura
    del compilador dejara de emitir el campo— el enmascarador tiene que PARAR, no
    seguir con otro comportamiento. Un cambio de esta decisión se firma; no se
    hereda de un valor por defecto.
    """
    with pytest.raises(ValueError, match="determinista"):
        load_mask_config(_policy(deterministic_masking=False), pepper="x" * 40)


def test_se_niega_a_funcionar_si_la_pimienta_no_viene_de_configuracion() -> None:
    """`pimienta_desde: sesion` sería exactamente lo que el contrato prohíbe."""
    with pytest.raises(ValueError, match="config"):
        load_mask_config(_policy(pepper_from="sesion"), pepper="x" * 40)


def test_el_mensaje_de_la_negativa_explica_que_hay_que_hacer() -> None:
    """Un fallo de arranque sin salida obliga a leer el código fuente."""
    with pytest.raises(ValueError) as fallo:
        load_mask_config(_policy(deterministic_masking=False), pepper="x" * 40)
    texto = str(fallo.value)
    assert "policy.yaml" in texto
    assert "P-006" in texto or "firmad" in texto
