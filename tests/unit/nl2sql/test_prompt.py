"""La composición del prompt. **El TEXTO vive en `prompts/*.md`, nunca en un `.py`.**

Lo que se prueba aquí no es el texto —ese es un dato firmado que se mide, no se
asierta— sino las tres decisiones de código que lo rodean: de dónde sale la
procedencia que va al informe, qué se le enseña al reintento, y qué pasa cuando la
consulta anterior no cabe.
"""

from __future__ import annotations

import pytest

from datawarden.domain.types import Position, RejectionReason, Severity
from datawarden.nl2sql.prompt import MAX_PREVIOUS_SQL_CHARS, clip, load, render
from datawarden.nl2sql.providers import Request


def _rechazo() -> RejectionReason:
    return RejectionReason(
        rule_id="R013",
        code="tree_too_large",
        message="the syntax tree has 5210 nodes and the maximum is 4000",
        suggestion="ask for fewer things at once, or filter with IN instead of many ORs",
        severity=Severity.SECURITY,
        position=Position.STATEMENT,
        subject="5210 nodes",
    )


def test_el_prompt_trae_su_procedencia_para_el_informe() -> None:
    """El informe publica `{prompt_id, version, sha256}` y el sha es DEL FICHERO.

    Dos números medidos con prompts distintos no se pueden comparar; que la
    procedencia salga del fichero y no de una constante es lo que impide confundirlos.
    """
    prompt = load("nl2sql")

    assert prompt.prompt_id == "nl2sql"
    assert prompt.version
    assert len(prompt.sha256) == 64


def test_un_prompt_que_no_existe_dice_donde_van_los_prompts() -> None:
    with pytest.raises(FileNotFoundError, match="prompts/"):
        load("este-prompt-no-existe")


def test_el_primer_intento_no_lleva_bloque_de_reintento() -> None:
    compuesto = render(Request(question="cuantos clientes hay"), catalog="- dim_customer")

    assert "cuantos clientes hay" in compuesto
    assert "rechazado por el guard" not in compuesto


def test_el_reintento_lleva_la_regla_el_motivo_y_la_consulta_anterior() -> None:
    """**Sin el rechazo dentro no hay ciclo de corrección, hay un reintento a ciegas.**

    Y sin la consulta anterior, la instrucción de `nl2sql-retry.md` —«corrige eso
    concreto, no reescribas la consulta entera»— no se puede seguir.
    """
    peticion = Request(
        question="cuantos clientes hay",
        attempt=2,
        rejection=_rechazo(),
        previous_sql="SELECT 1 + 1 AS n FROM dim_customer",
    )

    compuesto = render(peticion, catalog="- dim_customer")

    assert "R013" in compuesto
    assert "the syntax tree has 5210 nodes" in compuesto
    assert "SELECT 1 + 1 AS n FROM dim_customer" in compuesto


# ------------------------------------------------ la consulta anterior que no cabe ---


def test_una_consulta_corta_no_se_toca() -> None:
    assert clip("SELECT 1 AS n") == "SELECT 1 AS n"


def test_una_consulta_larguisima_se_corta_diciendo_que_se_ha_cortado() -> None:
    """Cortar en silencio es peor que no cortar.

    Un modelo que recibe SQL truncado sin saberlo intenta «completar» algo que no
    está roto, y lo que se mediría sería su reacción a un prompt mutilado.
    """
    largo = "SELECT 1" + " + 1" * 20_000

    recortado = clip(largo)

    assert len(recortado) < len(largo)
    assert "truncados" in recortado
    assert str(len(largo) - MAX_PREVIOUS_SQL_CHARS) in recortado


def test_la_bomba_de_ast_no_desborda_el_prompt_del_reintento() -> None:
    """Es el caso real: tres semillas del corpus pasan de los 30.000 caracteres.

    Tienen que superar los 4.000 nodos para que R013 dispare, y meterlas enteras
    gastaría la llamada al modelo en repetirle `+ 1` diez mil veces.
    """
    bomba = "SELECT customer_sk FROM dim_customer WHERE " + " OR ".join(
        ["country_code = 'ES'"] * 1_500
    )

    compuesto = render(
        Request(
            question="clientes de estos paises",
            attempt=2,
            rejection=_rechazo(),
            previous_sql=bomba,
        ),
        catalog="- dim_customer",
    )

    assert len(compuesto) < 10_000
    assert "truncados" in compuesto
