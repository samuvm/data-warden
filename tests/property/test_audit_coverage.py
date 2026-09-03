"""`G-AUDIT-COV` · **ninguna invocación sin su registro.** Es un AXIOMA.

`propuesta_admisible: false`: este número no se negocia, ni siquiera con dos intentos
medidos. Y la meta no dice «ninguna ejecución»: dice **ninguna INVOCACIÓN**. La
diferencia es todo el valor del anillo 5. Un rechazo del guard es un evento de
seguridad, y quien sondea la política sistemáticamente —para averiguar qué columnas
existen y cuáles están protegidas— es precisamente el que no dejaría ni una línea si
solo se registraran los éxitos.

Se comprueban las dos igualdades que la nota de `GOALS.yaml` fija:

    n_registros == n_invocaciones
    n_execute   == n_registros[status == executed]

**La segunda se mide con el CONTADOR del motor, no leyendo el código.** `n_execute`
sale de `engines.base.executions()`, un contador de proceso que incrementa toda
implementación de `Engine` en su primera línea. «El código llama a `append` antes de
`execute`» es una lectura del flujo; «el motor se movió exactamente tantas veces como
registros ejecutados hay» es una medida. Con un motor de verdad la diferencia no se
podría afirmar: «no devolvió filas» y «no se ejecutó» son cosas distintas.
"""

from __future__ import annotations

import contextlib

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

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
from datawarden.engines.base import RecordingEngine, count_execution, executions
from datawarden.mask.config import MaskConfig
from datawarden.principal import BUDGETS_PATH, POLICY_PATH
from datawarden.principal.budgets import load_budgets
from datawarden.principal.policy import load_policy

_SCHEMA = load_generated(SCHEMA_PATH)
_POLICY = load_policy(POLICY_PATH)
_BUDGETS = load_budgets(BUDGETS_PATH)

#: Fixture de estadísticas, no el almacén real: la propiedad tiene que correr en una
#: máquina sin los 7,1 GB de `datagen/out/`, y si dependiera de ellos el gate dejaría
#: de ser ejecutable donde más falta hace, que es en una máquina limpia.
_BARATO = Statistics(
    profile="fixture",
    source="fixture",
    tables={
        "dim_customer": TableStats(
            name="dim_customer",
            rows=10,
            bytes=1_000,
            files=1,
            column_bytes={"customer_sk": 500, "country_code": 500},
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
            column_bytes={"customer_sk": 10**12, "country_code": 10**12},
        )
    },
)

#: Las cuatro clases de invocación, una por estado del contrato. La quinta columna es
#: la que provoca `error`, y hace falta un motor roto para llegar a ella.
_LEGAL = "SELECT customer_sk FROM dim_customer"
_ILEGAL = "DELETE FROM dim_customer"
_OTRA_LEGAL = "SELECT country_code FROM dim_customer"

_SETTINGS = settings(deadline=None, suppress_health_check=[HealthCheck.too_slow])


class _MotorRoto:
    """Un motor que cuenta su ejecución y luego revienta.

    Cuenta ANTES de fallar a propósito: el contador mide invocaciones al motor, no
    éxitos. Si no contara, un fallo del motor haría cuadrar los números por el
    motivo equivocado.
    """

    name = "roto"

    def execute(self, query: ValidatedQuery) -> ResultSet:
        count_execution()
        message = "el motor se cayó"
        raise RuntimeError(message)


#: La máscara es OBLIGATORIA en el ejecutor desde el 2026-09-03, y sin defecto a
#: propósito: llamaba a `screen()` y se saltaba el anillo 4, así que el único camino
#: sancionado al motor devolvía nombres y correos reales. Un parámetro opcional habría
#: dejado que el fallo volviera con solo olvidarse de pasarlo.
_MASK = MaskConfig(pepper="pimienta-de-pruebas-de-treinta-y-dos-o-mas")


def _ejecutor(stats: Statistics, engine: object | None = None) -> AuditedExecutor:
    return AuditedExecutor(
        engine=engine or RecordingEngine(rows=2),
        store=AuditStore(":memory:"),
        schema=_SCHEMA,
        policy=_POLICY,
        budgets=_BUDGETS,
        stats=stats,
        mask=_MASK,
    )


@_SETTINGS
@given(
    invocaciones=st.lists(
        st.sampled_from([_LEGAL, _ILEGAL, _OTRA_LEGAL]), min_size=1, max_size=12
    ),
    rol=st.sampled_from(list(Role)),
    caro=st.booleans(),
)
def test_toda_invocacion_deja_exactamente_un_registro(
    invocaciones: list[str], rol: Role, caro: bool
) -> None:
    """`n_registros == n_invocaciones`. Ni uno menos, y tampoco uno más.

    «Uno más» importa tanto como «uno menos»: un registro duplicado infla el
    denominador de cualquier métrica que se calcule sobre la auditoría, y una tasa
    de rechazo que se mide sobre registros inventados no mide nada.
    """
    ex = _ejecutor(_CARISIMO if caro else _BARATO)
    principal = Principal(id=f"prop-{rol.value}", role=rol, source=RoleSource.CLI_FLAG)

    for sql in invocaciones:
        ex.run(sql, principal=principal)

    assert ex.store.count() == len(invocaciones)


@_SETTINGS
@given(
    invocaciones=st.lists(
        st.sampled_from([_LEGAL, _ILEGAL, _OTRA_LEGAL]), min_size=1, max_size=12
    ),
    caro=st.booleans(),
)
def test_el_motor_se_movio_exactamente_los_registros_ejecutados(
    invocaciones: list[str], caro: bool
) -> None:
    """`n_execute == n_registros[status == executed]`, medido por CONTADOR.

    Es la mitad de la meta que no se puede falsear leyendo el código: si el
    ejecutor llamara al motor sin registrar, el contador subiría y los registros
    no, y esta igualdad se rompería.
    """
    ex = _ejecutor(_CARISIMO if caro else _BARATO)
    principal = Principal(id="prop", role=Role.ANALYST, source=RoleSource.CLI_FLAG)
    antes = executions()

    for sql in invocaciones:
        ex.run(sql, principal=principal)

    ejecutados = [e for e in ex.store.rows_as_entries() if e.record.status is Status.EXECUTED]
    assert executions() - antes == len(ejecutados)


@_SETTINGS
@given(
    invocaciones=st.lists(
        st.sampled_from([_LEGAL, _ILEGAL, _OTRA_LEGAL]), min_size=1, max_size=12
    ),
    caro=st.booleans(),
)
def test_la_cadena_sigue_verificando_haga_lo_que_haga_el_llamante(
    invocaciones: list[str], caro: bool
) -> None:
    """Auditar de más no puede romper la cadena: es la otra mitad del axioma."""
    ex = _ejecutor(_CARISIMO if caro else _BARATO)
    principal = Principal(id="prop", role=Role.ANALYST, source=RoleSource.CLI_FLAG)

    for sql in invocaciones:
        ex.run(sql, principal=principal)

    assert ex.store.verify() == (True, None)


def test_los_cuatro_estados_del_contrato_se_auditan() -> None:
    """El umbral adicional de la meta: **4 estados auditados, no 2.**

    No es una propiedad sino un caso cerrado, y va aquí porque es el número que el
    gate lee. Cada estado se provoca por su camino real: el guard rechaza un
    `DELETE`, el presupuesto rechaza una consulta carísima, el motor bueno ejecuta
    y el motor roto produce el error.
    """
    principal = Principal(id="prop", role=Role.ANALYST, source=RoleSource.CLI_FLAG)

    barato = _ejecutor(_BARATO)
    barato.run(_LEGAL, principal=principal)
    barato.run(_ILEGAL, principal=principal)

    caro = _ejecutor(_CARISIMO)
    caro.run(_LEGAL, principal=principal)

    roto = _ejecutor(_BARATO, engine=_MotorRoto())
    # El ejecutor RELANZA a propósito: tragarse la excepción convertiría un fallo del
    # motor en un éxito silencioso. Aquí se suprime porque lo que se comprueba es el
    # REGISTRO que dejó, no el error, que ya tiene su test en `tests/unit/audit/`.
    with contextlib.suppress(RuntimeError):
        roto.run(_LEGAL, principal=principal)

    vistos = {
        e.record.status for ex in (barato, caro, roto) for e in ex.store.rows_as_entries()
    }
    assert vistos == set(Status), f"faltan estados por auditar: {set(Status) - vistos}"
    assert len(vistos) == 4
