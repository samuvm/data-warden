"""El ciclo de corrección: generar → validar → mensaje accionable → reintentar.

FASE ROJA. **El BUCLE es TDD obligatorio; el GENERADOR no se testea, se mide.**
`docs/PLAN.md` lo separa así y `CLAUDE.md` lo repite: un test unitario sobre la salida
de un modelo mide el modelo, no el código. Lo que sí es código —cuántas veces se
reintenta, qué se le pasa al siguiente intento, cuándo se para— es determinista y se
prueba con un provider grabado, sin modelo ninguno.

**Qué mide este bucle, y por qué importa más de lo que parece.** `G-RECOVERY` no
pregunta si el modelo acierta a la primera: pregunta si **se corrige con el mensaje de
rechazo**. Esa es la tesis del proyecto puesta a prueba — *el valor no está en la tasa
de acierto, está en la garantía sobre el fallo*—. Un guard que rechaza sin explicar
produce un modelo que reintenta al azar; uno que explica produce uno que corrige. La
diferencia se mide, y este bucle es donde se mide.
"""

from __future__ import annotations

import pytest

from datawarden.domain.types import (
    Position,
    Principal,
    RejectionReason,
    Role,
    RoleSource,
    Severity,
    ValidatedQuery,
)
from datawarden.nl2sql.loop import MAX_RETRIES, Attempt, LoopResult, run_loop
from datawarden.nl2sql.providers import ScriptedProvider

_PRINCIPAL = Principal(id="loop", role=Role.ANALYST, source=RoleSource.CLI_FLAG)


def _rechazo(codigo: str = "column_policy", retryable: bool = True) -> RejectionReason:
    return RejectionReason(
        rule_id="R008",
        code=codigo,
        message="column dim_customer.birth_date is masked and appears in a WHERE predicate",
        suggestion="use dim_customer.age_band instead, which the policy publishes",
        severity=Severity.POLICY,
        position=Position.WHERE,
        subject="dim_customer.birth_date",
        alternative="dim_customer.age_band",
        retryable=retryable,
    )


class _Validador:
    """Un validador de mentira: devuelve lo que se le dijo, en orden.

    Se mira además CUÁNTAS veces se le llamó, porque el número de intentos es la
    mitad de lo que este bucle decide.
    """

    def __init__(self, *veredictos: object) -> None:
        self._veredictos = list(veredictos)
        self.vistos: list[str] = []

    def __call__(self, sql: str) -> object:
        self.vistos.append(sql)
        return self._veredictos.pop(0) if self._veredictos else _rechazo()


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


# ------------------------------------------------------------- el camino feliz ---


def test_una_consulta_valida_a_la_primera_no_reintenta() -> None:
    validador = _Validador(_aceptada())
    provider = ScriptedProvider(["SELECT 1 AS n"])

    resultado = run_loop("cuantos clientes hay", provider=provider, validate=validador)

    assert resultado.accepted
    assert len(resultado.attempts) == 1
    assert len(validador.vistos) == 1
    assert resultado.recovered is False, "sin rechazo previo no hay recuperación"


def test_recuperarse_al_segundo_intento_cuenta_como_recuperacion() -> None:
    """Es exactamente lo que `G-RECOVERY` mide: rechazo, mensaje, y acierto después."""
    validador = _Validador(_rechazo(), _aceptada())
    provider = ScriptedProvider(["SELECT birth_date FROM dim_customer", "SELECT 1 AS n"])

    resultado = run_loop("edad de los clientes", provider=provider, validate=validador)

    assert resultado.accepted
    assert resultado.recovered is True
    assert len(resultado.attempts) == 2


# ------------------------------------------------------------------- los topes ---


def test_se_reintenta_como_mucho_dos_veces() -> None:
    """Tres intentos en total: el primero y dos correcciones.

    El tope no es una comodidad: cada reintento cuesta una llamada al modelo y un
    guard entero, y un bucle sin tope convierte un rechazo persistente en una factura
    y en una latencia sin fin.
    """
    validador = _Validador(_rechazo(), _rechazo(), _rechazo(), _aceptada())
    provider = ScriptedProvider(["a", "b", "c", "d"])

    resultado = run_loop("lo que sea", provider=provider, validate=validador)

    assert not resultado.accepted
    assert len(resultado.attempts) == MAX_RETRIES + 1 == 3
    assert len(validador.vistos) == 3, "el cuarto intento no debe llegar a validarse"


def test_un_rechazo_no_reintentable_para_el_bucle_en_seco() -> None:
    """`DELETE` no se reformula. Reintentarlo solo gasta tokens.

    Y contamina la métrica: un rechazo que el modelo no puede arreglar por diseño
    cuenta como fallo de recuperación si se le da la oportunidad de fallar.
    """
    validador = _Validador(_rechazo(codigo="write_node_present", retryable=False), _aceptada())
    provider = ScriptedProvider(["DELETE FROM dim_customer", "SELECT 1 AS n"])

    resultado = run_loop("borra los clientes", provider=provider, validate=validador)

    assert not resultado.accepted
    assert len(resultado.attempts) == 1, "no se reintenta lo que no es reintentable"
    assert resultado.rejection is not None
    assert resultado.rejection.retryable is False


# ------------------------------------------- lo que se le pasa al siguiente ---


def test_el_siguiente_intento_recibe_el_rechazo_anterior() -> None:
    """Sin esto no hay ciclo de corrección: hay un bucle que reintenta a ciegas.

    Es la diferencia entera entre `G-RECOVERY` y una tasa de reintento.
    """
    provider = ScriptedProvider(["SELECT birth_date FROM dim_customer", "SELECT 1 AS n"])

    run_loop("edad", provider=provider, validate=_Validador(_rechazo(), _aceptada()))

    assert len(provider.recibido) == 2
    segundo = provider.recibido[1]
    assert segundo.rejection is not None
    assert segundo.rejection.subject == "dim_customer.birth_date"


def test_el_primer_intento_no_recibe_ningun_rechazo() -> None:
    provider = ScriptedProvider(["SELECT 1 AS n"])

    run_loop("cuantos", provider=provider, validate=_Validador(_aceptada()))

    assert provider.recibido[0].rejection is None


# ------------------------------------------------------------------ la historia ---


def test_la_historia_guarda_todos_los_intentos_con_su_veredicto() -> None:
    """Es lo que el informe de evaluación publica y lo que la auditoría registra.

    Guardar solo el último intento haría imposible responder «¿de qué se corrigió?»,
    que es la única pregunta interesante sobre una recuperación.
    """
    validador = _Validador(_rechazo(), _aceptada())
    provider = ScriptedProvider(["SELECT birth_date FROM dim_customer", "SELECT 1 AS n"])

    resultado = run_loop("edad", provider=provider, validate=validador)

    assert [a.sql for a in resultado.attempts] == [
        "SELECT birth_date FROM dim_customer",
        "SELECT 1 AS n",
    ]
    assert resultado.attempts[0].rejection is not None
    assert resultado.attempts[1].rejection is None


def test_el_primer_rechazo_se_publica_aparte_del_ultimo() -> None:
    """`G-RECOVERY` agrupa por la regla que rechazó LA PRIMERA VEZ.

    Si se agrupara por la última, una consulta que se corrige de R008 y cae en R006
    contaría como fallo de R006, y el mensaje que se está evaluando es el de R008.
    """
    validador = _Validador(_rechazo(), _rechazo(codigo="row_limit"), _rechazo())
    provider = ScriptedProvider(["a", "b", "c"])

    resultado = run_loop("lo que sea", provider=provider, validate=validador)

    assert resultado.first_rejection is not None
    assert resultado.first_rejection.code == "column_policy"
    assert resultado.rejection is not None


# ----------------------------------------------------------------- fail-closed ---


def test_un_provider_que_revienta_no_tumba_el_bucle() -> None:
    """Un modelo que falla es un rechazo, no una excepción que sube al llamante.

    El mismo principio que gobierna el guard: lo que no se puede completar se
    convierte en un veredicto, nunca en un fallo del proceso.
    """

    class _Roto:
        name = "roto"

        def generate(self, request: object) -> str:
            message = "el modelo no responde"
            raise RuntimeError(message)

    resultado = run_loop("lo que sea", provider=_Roto(), validate=_Validador(_aceptada()))

    assert not resultado.accepted
    assert resultado.rejection is not None
    assert resultado.rejection.rule_id == "INTERNAL"
    assert resultado.rejection.retryable is False


def test_un_provider_que_devuelve_vacio_se_trata_como_rechazo() -> None:
    resultado = run_loop(
        "lo que sea", provider=ScriptedProvider(["   "]), validate=_Validador(_aceptada())
    )

    assert not resultado.accepted
    assert resultado.rejection is not None


@pytest.mark.parametrize("pregunta", ["", "   ", "\n\t"])
def test_una_pregunta_vacia_no_llega_ni_al_modelo(pregunta: str) -> None:
    """Gastar una llamada en una cadena vacía es gastar dinero en nada."""
    provider = ScriptedProvider(["SELECT 1 AS n"])

    resultado = run_loop(pregunta, provider=provider, validate=_Validador(_aceptada()))

    assert not resultado.accepted
    assert provider.recibido == []


def test_el_resultado_dice_de_que_modelo_salio() -> None:
    """Un número de recuperación sin decir qué modelo lo produjo no es un número.

    `models.lock` fija generador y juez por DIGEST justamente para esto.
    """
    resultado: LoopResult = run_loop(
        "cuantos",
        provider=ScriptedProvider(["SELECT 1 AS n"]),
        validate=_Validador(_aceptada()),
    )

    assert resultado.provider == "scripted"


# ------------------------------------------------- el rechazo SEMBRADO (fase 6) ---


def test_un_rechazo_sembrado_entra_en_la_historia_como_primer_intento() -> None:
    """`G-RECOVERY` mide la CORRECCIÓN, no el error, y por eso el error se siembra.

    Si el corpus fueran solo preguntas y se dejara que el modelo produjera por su
    cuenta el SQL rechazable, el número mediría dos cosas mezcladas: cuántas veces se
    equivoca del modo previsto y cuántas se corrige después. La primera varía con el
    modelo y no es interesante.
    """
    semilla = Attempt(sql="SELECT birth_date FROM dim_customer", rejection=_rechazo())
    provider = ScriptedProvider(["SELECT 1 AS n"])

    resultado = run_loop(
        "edad de los clientes",
        provider=provider,
        validate=_Validador(_aceptada()),
        seed=semilla,
    )

    assert resultado.accepted
    assert resultado.recovered is True, "aceptada tras un rechazo: eso es recuperarse"
    assert len(resultado.attempts) == 2
    assert resultado.attempts[0] is semilla


def test_el_primer_rechazo_de_un_caso_sembrado_es_el_de_la_semilla() -> None:
    """Es lo que agrupa la métrica: la regla cuyo mensaje se está evaluando."""
    semilla = Attempt(sql="SELECT national_id FROM dim_customer", rejection=_rechazo())

    resultado = run_loop(
        "el dni de los clientes",
        provider=ScriptedProvider(["SELECT 1 AS n"]),
        validate=_Validador(_aceptada()),
        seed=semilla,
    )

    assert resultado.first_rejection is not None
    assert resultado.first_rejection.rule_id == "R008"


def test_el_modelo_recibe_la_semilla_como_rechazo_y_como_consulta_anterior() -> None:
    """**Sin la consulta anterior el reintento no es una corrección, es un rehacer.**

    `prompts/nl2sql-retry.md` le pide al modelo que corrija «eso concreto» y que no
    reescriba la consulta entera si el resto era correcto. Sin el SQL anterior dentro
    del prompt esa instrucción no se puede seguir, y lo que se mediría sería otra cosa.
    """
    semilla = Attempt(sql="SELECT birth_date FROM dim_customer", rejection=_rechazo())
    provider = ScriptedProvider(["SELECT 1 AS n"])

    run_loop(
        "edad",
        provider=provider,
        validate=_Validador(_aceptada()),
        seed=semilla,
    )

    primera = provider.recibido[0]
    assert primera.rejection is not None
    assert primera.previous_sql == "SELECT birth_date FROM dim_customer"


def test_una_semilla_no_reintentable_no_gasta_ni_una_llamada() -> None:
    """Un `DELETE` sembrado no se reformula, igual que uno generado.

    Darle la oportunidad de fallar contaminaría `G-RECOVERY` con casos que nadie
    puede arreglar, y además cuesta una llamada al modelo por cada uno.
    """
    semilla = Attempt(
        sql="DELETE FROM dim_customer",
        rejection=_rechazo(codigo="write_node_present", retryable=False),
    )
    provider = ScriptedProvider(["SELECT 1 AS n"])

    resultado = run_loop(
        "borra los clientes",
        provider=provider,
        validate=_Validador(_aceptada()),
        seed=semilla,
    )

    assert not resultado.accepted
    assert provider.recibido == [], "no se llama al modelo por algo que no se puede arreglar"
    assert resultado.attempts == (semilla,)


def test_una_semilla_no_consume_los_reintentos_del_modelo() -> None:
    """El tope cuenta llamadas AL MODELO, y la semilla no es una llamada.

    Si la semilla gastara un intento, un caso sembrado tendría una oportunidad menos
    que uno normal y `G-RECOVERY` mediría un bucle más corto del que se ejecuta en
    producción.
    """
    semilla = Attempt(sql="SELECT birth_date FROM dim_customer", rejection=_rechazo())
    provider = ScriptedProvider(["a", "b", "c", "d"])

    resultado = run_loop(
        "edad",
        provider=provider,
        validate=_Validador(_rechazo(), _rechazo(), _rechazo(), _aceptada()),
        seed=semilla,
    )

    assert not resultado.accepted
    assert len(provider.recibido) == MAX_RETRIES + 1 == 3
    assert len(resultado.attempts) == 4, "la semilla más los tres intentos"


def test_cada_intento_le_pasa_al_siguiente_el_sql_que_acaba_de_fallar() -> None:
    provider = ScriptedProvider(["SELECT a", "SELECT b", "SELECT c"])

    run_loop(
        "lo que sea",
        provider=provider,
        validate=_Validador(_rechazo(), _rechazo(), _rechazo()),
    )

    assert [r.previous_sql for r in provider.recibido] == ["", "SELECT a", "SELECT b"]
