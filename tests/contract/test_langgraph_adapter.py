"""El grafo y el bucle dan LO MISMO. Nivel 3, contrato.

**Por qué esto es un contrato y no un test unitario.** `agent/` se MIDE, no se
testea (`CLAUDE.md`): un test sobre el grafo probaría LangGraph, que ya está
probado por quien lo escribió. Lo que sí hay que sostener es la afirmación del
propio adaptador —*«si un día se borra este fichero, `G-RECOVERY` sigue midiendo el
mismo número»*—, y eso no se comprueba mirando el código: se comprueba corriendo
los dos caminos con la misma entrada y exigiendo la misma salida.

Es la clase de invariante que se rompe sin que nadie lo note: alguien parte el
grafo en tres nodos «para que se vea mejor», la política de reintento se muda al
grafo, y a partir de ese día la evaluación mide un bucle y producción corre otro.
"""

from __future__ import annotations

import pytest

from datawarden.agent.langgraph_graph import ask, build
from datawarden.domain.types import (
    Position,
    Principal,
    RejectionReason,
    Role,
    RoleSource,
    Severity,
    ValidatedQuery,
)
from datawarden.nl2sql.loop import Attempt, run_loop
from datawarden.nl2sql.providers import ScriptedProvider

_PRINCIPAL = Principal(id="grafo", role=Role.ANALYST, source=RoleSource.CLI_FLAG)


def _rechazo(retryable: bool = True) -> RejectionReason:
    return RejectionReason(
        rule_id="R008",
        code="column_policy",
        message="column dim_customer.birth_date is masked and appears in a WHERE predicate",
        suggestion="use dim_customer.age_band instead, which the policy publishes",
        severity=Severity.POLICY,
        position=Position.WHERE,
        subject="dim_customer.birth_date",
        retryable=retryable,
    )


def _aceptada() -> ValidatedQuery:
    import sqlglot

    return ValidatedQuery(
        ast=sqlglot.parse_one("SELECT 1 AS n", dialect="duckdb"),
        dialect="duckdb",
        principal=_PRINCIPAL,
        tables=(),
        columns=(),
        max_rows=50_000,
    )


class _Validador:
    def __init__(self, *veredictos: object) -> None:
        self._veredictos = list(veredictos)

    def __call__(self, sql: str) -> object:
        return self._veredictos.pop(0) if self._veredictos else _rechazo()


@pytest.mark.parametrize(
    ("veredictos", "respuestas"),
    [
        pytest.param((_aceptada(),), ["SELECT 1 AS n"], id="acierta-a-la-primera"),
        pytest.param(
            (_rechazo(), _aceptada()),
            ["SELECT birth_date FROM dim_customer", "SELECT 1 AS n"],
            id="se-recupera-al-segundo",
        ),
        pytest.param(
            (_rechazo(), _rechazo(), _rechazo()),
            ["a", "b", "c"],
            id="agota-los-reintentos",
        ),
        pytest.param(
            (_rechazo(retryable=False),),
            ["DELETE FROM dim_customer"],
            id="para-en-seco",
        ),
    ],
)
def test_el_grafo_devuelve_lo_mismo_que_el_bucle(
    veredictos: tuple[object, ...], respuestas: list[str]
) -> None:
    """La misma entrada por los dos caminos tiene que dar la misma salida.

    Si esto falla, la evaluación está midiendo un bucle y producción corre otro.
    """
    directo = run_loop(
        "una pregunta",
        provider=ScriptedProvider(list(respuestas)),
        validate=_Validador(*veredictos),
    )
    grafo = ask(
        "una pregunta",
        provider=ScriptedProvider(list(respuestas)),
        validate=_Validador(*veredictos),
    )

    assert [a.sql for a in grafo["attempts"]] == [a.sql for a in directo.attempts]
    assert (grafo["query"] is None) == (directo.query is None)
    assert (grafo["rejection"] is None) == (directo.rejection is None)


def test_el_grafo_tambien_acepta_un_rechazo_sembrado() -> None:
    """`G-RECOVERY` siembra el rechazo, así que el adaptador tiene que dejar pasarlo.

    Un grafo que no pudiera arrancar desde una semilla obligaría a que la evaluación
    usara otro camino que el de producción, y entonces el número no sería del sistema
    que se despliega.
    """
    semilla = Attempt(sql="SELECT birth_date FROM dim_customer", rejection=_rechazo())

    compilado = build(
        provider=ScriptedProvider(["SELECT 1 AS n"]),
        validate=_Validador(_aceptada()),
        seed=semilla,
    )
    estado = compilado.invoke({"question": "edad de los clientes"})

    assert estado["query"] is not None
    assert len(estado["attempts"]) == 2
    assert estado["attempts"][0] is semilla


def test_el_estado_del_grafo_no_lleva_el_principal() -> None:
    """**El rol NUNCA viene de datos no autenticados** (I-09).

    El estado de un grafo es exactamente eso: algo que un nodo anterior escribió.
    Si el `principal` viajara ahí, un nodo podría subirse el rol escribiendo un
    campo, y toda la política se decidiría con un dato en vez de con una identidad.
    """
    from datawarden.agent.langgraph_graph import GraphState

    assert "principal" not in GraphState.__annotations__
    assert "role" not in GraphState.__annotations__
