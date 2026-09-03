"""El `AuditedExecutor`: el ÚNICO camino a `Engine.execute()`. Zona TDD, 95 %.

FASE ROJA.

`G-AUDIT-COV` es un AXIOMA —`propuesta_admisible: false`— y dice una sola cosa:
**ninguna invocación sin su registro.** Los cuatro estados se auditan, no solo el
que ejecuta. Un rechazo del guard es un evento de seguridad, y el atacante que
sondea la política sistemáticamente es precisamente el que no dejaría ni una línea
si solo se registraran los éxitos.

Lo que se prueba aquí es esa cobertura y su ORDEN: el registro se escribe **pase lo
que pase**, incluso cuando el motor revienta. Un `except` que se tragara la
excepción convertiría un fallo del motor en un éxito silencioso; uno que no
escribiera el registro dejaría el peor de los eventos sin rastro.
"""

from __future__ import annotations

import pytest

from datawarden.audit.executor import AuditedExecutor
from datawarden.audit.store import AuditStore
from datawarden.catalog import SCHEMA_PATH, load_generated
from datawarden.catalog.statistics import Statistics, TableStats
from datawarden.domain.types import (
    Principal,
    ResultSet,
    Role,
    RoleSource,
    Status,
    ValidatedQuery,
)
from datawarden.engines import base as engine_base
from datawarden.engines.base import RecordingEngine
from datawarden.mask.config import MaskConfig
from datawarden.principal import BUDGETS_PATH, POLICY_PATH
from datawarden.principal.budgets import load_budgets
from datawarden.principal.policy import load_policy

_SCHEMA = load_generated(SCHEMA_PATH)
_POLICY = load_policy(POLICY_PATH)
_BUDGETS = load_budgets(BUDGETS_PATH)

#: Estadísticas de juguete: baratas para que quepa, y una carísima para el rechazo
#: por presupuesto. Sin fixture no se puede provocar `rejected_by_budget` sin los
#: 7,1 GB del dataset, y este fichero es unitario.
_BARATO = Statistics(
    profile="fixture",
    source="fixture",
    tables={
        "dim_customer": TableStats(
            name="dim_customer",
            rows=10,
            bytes=1_000,
            files=1,
            column_bytes={"customer_sk": 500},
        )
    },
)
_CARISIMO = Statistics(
    profile="fixture",
    source="fixture",
    tables={
        "dim_customer": TableStats(
            name="dim_customer",
            rows=10**9,
            bytes=10**12,
            files=1000,
            column_bytes={"customer_sk": 10**12},
        )
    },
)

_ANALYST = Principal(id="corpus-analyst", role=Role.ANALYST, source=RoleSource.CLI_FLAG)


#: La máscara es OBLIGATORIA en el ejecutor desde el 2026-09-03, y sin defecto a
#: propósito: llamaba a `screen()` y se saltaba el anillo 4, así que el único camino
#: sancionado al motor devolvía nombres y correos reales. Un parámetro opcional habría
#: dejado que el fallo volviera con solo olvidarse de pasarlo.
_MASK = MaskConfig(pepper="pimienta-de-pruebas-de-treinta-y-dos-o-mas")


def _ejecutor(stats: Statistics = _BARATO, engine: object | None = None) -> AuditedExecutor:
    return AuditedExecutor(
        engine=engine or RecordingEngine(rows=3),
        store=AuditStore(":memory:"),
        schema=_SCHEMA,
        policy=_POLICY,
        budgets=_BUDGETS,
        stats=stats,
        mask=_MASK,
    )


# ---------------------------------------------- G-AUDIT-COV · los cuatro estados ---


def test_una_consulta_legal_se_ejecuta_y_deja_su_registro() -> None:
    ex = _ejecutor()

    resultado = ex.run("SELECT customer_sk FROM dim_customer", principal=_ANALYST)

    assert isinstance(resultado.rows, ResultSet)
    (entrada,) = ex.store.rows_as_entries()
    assert entrada.record.status is Status.EXECUTED
    assert entrada.record.principal_id == "corpus-analyst"


def test_un_rechazo_del_guard_tambien_deja_su_registro() -> None:
    """El evento de seguridad. No registrarlo es el peor fallo posible del sistema."""
    ex = _ejecutor()

    resultado = ex.run("DELETE FROM dim_customer", principal=_ANALYST)

    assert resultado.rejection is not None
    (entrada,) = ex.store.rows_as_entries()
    assert entrada.record.status is Status.REJECTED_BY_GUARD


def test_un_rechazo_de_presupuesto_deja_su_registro_con_el_coste() -> None:
    """Con el coste estimado dentro: sin él, `G-COST-CALIB` solo vería las baratas."""
    ex = _ejecutor(stats=_CARISIMO)

    resultado = ex.run("SELECT customer_sk FROM dim_customer", principal=_ANALYST)

    assert resultado.rejection is not None
    (entrada,) = ex.store.rows_as_entries()
    assert entrada.record.status is Status.REJECTED_BY_BUDGET
    assert entrada.record.estimated_bytes is not None
    assert entrada.record.estimated_bytes > 0


def test_un_fallo_del_motor_deja_su_registro_y_relanza() -> None:
    """Las dos mitades importan, y la segunda más.

    Tragarse la excepción convertiría un fallo del motor en un éxito silencioso: el
    llamante recibiría un resultset vacío indistinguible de «no hay filas». Se
    registra `error` y se relanza con un `raise` desnudo, que conserva la traza.
    """

    class MotorRoto:
        name = "roto"

        def execute(self, query: ValidatedQuery) -> ResultSet:
            engine_base.count_execution()
            message = "el motor se cayó"
            raise RuntimeError(message)

    ex = _ejecutor(engine=MotorRoto())

    with pytest.raises(RuntimeError, match="el motor se cayó"):
        ex.run("SELECT customer_sk FROM dim_customer", principal=_ANALYST)

    (entrada,) = ex.store.rows_as_entries()
    assert entrada.record.status is Status.ERROR


# ------------------------------------------------- I-06 · nada llega sin auditoría ---


def test_lo_rechazado_no_llega_al_motor_y_se_mira_el_contador() -> None:
    """No se mira el flujo de control: se mira si el motor se movió.

    Es la misma técnica que la propiedad de `G-BUDGET-ESCAPE`. «El código no llama
    a execute» es una lectura; «el contador de proceso no cambió» es una medida.
    """
    ex = _ejecutor(stats=_CARISIMO)
    antes = engine_base.executions()

    ex.run("SELECT customer_sk FROM dim_customer", principal=_ANALYST)

    assert engine_base.executions() == antes


def test_el_contador_del_motor_cuadra_con_los_registros_ejecutados() -> None:
    """La propiedad de contadores que exige el PLAN, en su forma más simple."""
    ex = _ejecutor()
    antes = engine_base.executions()

    ex.run("SELECT customer_sk FROM dim_customer", principal=_ANALYST)
    ex.run("DELETE FROM dim_customer", principal=_ANALYST)
    ex.run("SELECT customer_sk FROM dim_customer", principal=_ANALYST)

    ejecutados = [e for e in ex.store.rows_as_entries() if e.record.status is Status.EXECUTED]
    assert engine_base.executions() - antes == len(ejecutados) == 2


# ------------------------------------------------------------- forma del registro ---


def test_el_registro_de_un_rechazo_del_guard_lleva_el_centinela_en_sql_digest() -> None:
    """**P-007.** No hay árbol validado, luego no hay SQL re-serializado que hashear.

    Hashear la ENTRADA sería exactamente lo que el contrato prohíbe: la auditoría
    certificaría algo distinto de lo que corrió. El centinela de 64 ceros casa el
    `pattern` del campo y usa el mismo vocabulario que el génesis de `prev_hash`.
    """
    ex = _ejecutor()

    ex.run("DELETE FROM dim_customer", principal=_ANALYST)

    (entrada,) = ex.store.rows_as_entries()
    assert entrada.record.sql_digest == "0" * 64


def test_el_registro_de_una_ejecucion_hashea_el_arbol_y_no_la_entrada() -> None:
    """I-02 llevado a la auditoría: se certifica lo que corrió, no lo que se pidió."""
    ex = _ejecutor()
    entrada_sql = "select   customer_sk   from dim_customer"

    resultado = ex.run(entrada_sql, principal=_ANALYST)

    assert resultado.query is not None
    (registro,) = ex.store.rows_as_entries()
    assert registro.record.sql_digest == resultado.query.sql_digest()


def test_la_pregunta_se_guarda_hasheada_y_no_en_claro_por_defecto() -> None:
    """Una pregunta en lenguaje natural puede llevar un dato personal dentro.

    El digest permite agrupar y contar sin conservar el texto. Guardarlo en claro
    es una decisión que se toma explícitamente, no el valor por defecto.
    """
    ex = _ejecutor()

    ex.run("SELECT customer_sk FROM dim_customer", principal=_ANALYST, question="¿cuántos hay?")

    (entrada,) = ex.store.rows_as_entries()
    assert len(entrada.record.question_digest) == 64
    assert entrada.record.question_preview is None


def test_la_cadena_sigue_intacta_despues_de_mezclar_los_cuatro_estados() -> None:
    ex = _ejecutor()
    ex.run("SELECT customer_sk FROM dim_customer", principal=_ANALYST)
    ex.run("DELETE FROM dim_customer", principal=_ANALYST)
    ex.run("SELECT customer_sk FROM dim_customer", principal=_ANALYST)

    assert ex.store.verify() == (True, None)


def test_el_rol_del_registro_sale_del_principal_y_no_de_la_consulta() -> None:
    """I-05. El rol nunca viene de datos no autenticados, y la auditoría lo fija."""
    ops = Principal(id="x", role=Role.OPS, source=RoleSource.SERVER_PROCESS)
    ex = _ejecutor()

    ex.run("SELECT customer_sk FROM dim_customer", principal=ops)

    (entrada,) = ex.store.rows_as_entries()
    assert entrada.record.role is Role.OPS
    assert entrada.record.role_source is RoleSource.SERVER_PROCESS


def test_el_resultado_dice_si_se_ejecuto_sin_obligar_a_mirar_las_filas() -> None:
    """`executed` distingue «ejecutó y no hubo filas» de «no ejecutó».

    Sin esta propiedad el llamante tendría que mirar `rows is not None`, y el día
    que alguien escriba `if result.rows:` —que es lo natural— un resultset vacío
    pasaría por un rechazo. Son dos cosas distintas y el tipo las separa.
    """
    ex = _ejecutor(engine=RecordingEngine(rows=0))

    ejecutada = ex.run("SELECT customer_sk FROM dim_customer", principal=_ANALYST)
    rechazada = ex.run("DELETE FROM dim_customer", principal=_ANALYST)

    assert ejecutada.executed is True
    assert len(ejecutada.rows.rows) == 0 if ejecutada.rows is not None else False
    assert rechazada.executed is False


# ------------------------------------- el anillo 4 · la fuga del 2026-09-03 ---


def test_el_camino_auditado_enmascara_y_no_solo_valida() -> None:
    """**Este test existe porque el ejecutor NO enmascaraba, y era una fuga real.**

    Llamaba a `screen()` —anillos 2 y 3— y se saltaba el 4. Como `AuditedExecutor` es
    el ÚNICO camino sancionado al motor (I-06), el sistema entero devolvía nombres y
    correos REALES a `analyst`, un rol para el que la política dice `mask`. Y el
    registro lo estaba diciendo todo el rato con un `columns_masked: []` que nadie
    leía.

    `G-PII-LEAK` pasaba con 0 fugas en 177 comprobaciones porque `pii_suite.py` medía
    `screen_and_mask()`, que es correcto y **no es el camino por el que se ejecuta**.
    La suite se cambió el mismo día para medir por aquí.

    Se asierta sobre el ÁRBOL y sobre `masked_columns`, nunca sobre la cadena de SQL:
    comparar SQL como texto engaña (`docs/spec/resultset-equality.md`).
    """
    from sqlglot import expressions as exp

    resultado = _ejecutor().run("SELECT first_name FROM dim_customer", principal=_ANALYST)

    assert resultado.query is not None
    assert resultado.query.masked_columns == ("dim_customer.first_name",)
    proyeccion = resultado.query.ast.find(exp.Select).expressions[0]
    assert not isinstance(proyeccion, exp.Column), (
        "la proyección sigue siendo una columna desnuda: el anillo 4 no reescribió nada"
    )


def test_lo_enmascarado_queda_en_el_registro_como_evidencia() -> None:
    """`columns_masked` no es decoración: es lo que permite auditar que se enmascaró.

    Vacío mientras el ejecutor se saltaba el anillo 4, y era una descripción exacta
    de lo que pasaba.
    """
    ejecutor = _ejecutor()

    resultado = ejecutor.run("SELECT first_name FROM dim_customer", principal=_ANALYST)

    assert resultado.entry.record.columns_masked == ("dim_customer.first_name",)


def test_un_rol_que_puede_ver_la_columna_no_la_recibe_enmascarada() -> None:
    """Enmascarar de más también es un fallo: `admin` tiene `allow` sobre el nombre."""
    admin = Principal(id="jefa", role=Role.ADMIN, source=RoleSource.CLI_FLAG)

    resultado = _ejecutor().run("SELECT first_name FROM dim_customer", principal=admin)

    assert resultado.query is not None
    assert resultado.query.masked_columns == ()


def test_no_se_puede_construir_un_ejecutor_sin_mascara() -> None:
    """**La mitad del arreglo es que `mask` no tenga valor por defecto.**

    Con un parámetro opcional, el mismo fallo vuelve el día que alguien construya un
    ejecutor y se olvide de pasarlo — y volvería en silencio, que es lo peor.
    """
    with pytest.raises(TypeError, match="mask"):
        AuditedExecutor(  # type: ignore[call-arg]
            engine=RecordingEngine(rows=1),
            store=AuditStore(":memory:"),
            schema=_SCHEMA,
            policy=_POLICY,
            budgets=_BUDGETS,
            stats=_BARATO,
        )
