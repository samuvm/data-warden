"""`G-BUDGET-ESCAPE` · **ninguna consulta cara llega al motor.** Es un AXIOMA.

La meta dice literalmente «Consultas que exceden presupuesto y llegan a ejecutarse:
0 %», y en `PROJECT.md` la celda «Cómo se mide» de esa métrica **está en blanco**.
`GOALS.yaml` la redefinió como invariante estructural más CONTADOR, y esto es ese
contador: no se mira el veredicto —eso sería comprobar que el código dice lo que
dice—, se mira si **el motor se movió**.

    delta == 0  cuando el coste estimado supera el presupuesto del rol

Con un motor de verdad no se podría afirmar esto: «no devolvió filas» y «no se
ejecutó» son cosas distintas. Con `RecordingEngine` la diferencia es exacta.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from datawarden.catalog import SCHEMA_PATH, load_generated
from datawarden.catalog.statistics import load as load_stats
from datawarden.cost import STATISTICS_PATH
from datawarden.cost.screen import screen
from datawarden.domain.types import Principal, Role, RoleSource
from datawarden.engines.base import RecordingEngine, executions
from datawarden.principal import BUDGETS_PATH, POLICY_PATH
from datawarden.principal.budgets import load_budgets
from datawarden.principal.policy import load_policy

_SCHEMA = load_generated(SCHEMA_PATH)
_POLICY = load_policy(POLICY_PATH)
_BUDGETS = load_budgets(BUDGETS_PATH)
_STATS = load_stats(STATISTICS_PATH)

_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)

#: Columnas anchas de la tabla de 66,6 M de filas y 4,1 GB. Cualquier combinación
#: sin poda de particiones se pasa del presupuesto de ops, y casi todas del de
#: analyst: es justo el espacio donde el invariante tiene algo que decir.
_WIDE_COLUMNS = st.lists(
    st.sampled_from(
        [
            "amount_minor",
            "amount_eur_minor",
            "fee_minor",
            "interchange_minor",
            "scheme_fee_minor",
            "auth_code",
            "decline_reason_code",
            "latency_ms",
            "risk_score",
            "payment_intent_id",
        ]
    ),
    min_size=1,
    max_size=6,
    unique=True,
)

_ROLES = st.sampled_from([Role.ANALYST, Role.OPS, Role.FINANCE, Role.ADMIN])


def _run(sql: str, role: Role) -> tuple[bool, int]:
    """`(se ejecutó, bytes estimados)`, pasando por los dos anillos y el motor."""
    engine = RecordingEngine()
    before = executions()
    result = screen(
        sql,
        principal=Principal(id="prop", role=role, source=RoleSource.CLI_FLAG),
        schema=_SCHEMA,
        policy=_POLICY,
        budgets=_BUDGETS,
        stats=_STATS,
    )
    if result.query is not None:
        engine.execute(result.query)
    estimated = result.cost.estimated_bytes if result.cost is not None else 0
    return (executions() > before, estimated)


@_SETTINGS
@given(_WIDE_COLUMNS, _ROLES)
def test_lo_que_pasa_del_presupuesto_no_llega_al_motor(columns: list[str], role: Role) -> None:
    """EL INVARIANTE. Si el coste estimado se pasa, el contador NO se mueve."""
    sql = f"SELECT {', '.join(columns)} FROM fact_payment_attempt"
    executed, estimated = _run(sql, role)
    if estimated > _BUDGETS.for_role(role).hard_bytes:
        assert not executed, (
            f"una consulta de {estimated} bytes llegó al motor con el rol "
            f"{role.value}, cuyo tope duro es {_BUDGETS.for_role(role).hard_bytes}"
        )


@_SETTINGS
@given(_WIDE_COLUMNS, _ROLES)
def test_lo_que_cabe_en_el_presupuesto_si_llega(columns: list[str], role: Role) -> None:
    """La otra mitad, y sin ella el axioma lo cumpliría un sistema que no ejecuta nada.

    `G-BUDGET-ESCAPE` sin esta propiedad la satisface un guardián que rechace todo,
    que es exactamente el guardián inútil que `G-COST-CALIB` existe para descartar.
    """
    sql = f"SELECT {', '.join(columns)} FROM fact_payment_attempt"
    executed, estimated = _run(sql, role)
    if 0 < estimated <= _BUDGETS.for_role(role).hard_bytes:
        assert executed


def test_una_particion_de_un_dia_cabe_donde_la_tabla_entera_no() -> None:
    """La poda de particiones no es un adorno: es lo que hace usable el presupuesto.

    La MISMA consulta, con y sin predicado de fecha, cae a los dos lados del tope de
    `ops`. Si la poda no funcionara, ops no podría preguntar nada sobre la tabla
    grande, y un rol que no puede preguntar nada acaba con el guardián desactivado.
    """
    columnas = "amount_minor, amount_eur_minor, auth_status"
    sin_poda, bytes_sin = _run(f"SELECT {columnas} FROM fact_payment_attempt", Role.OPS)
    con_poda, bytes_con = _run(
        f"SELECT {columnas} FROM fact_payment_attempt WHERE event_date = DATE '2026-08-31'",
        Role.OPS,
    )
    assert not sin_poda
    assert con_poda
    assert bytes_con < bytes_sin / 100


def test_el_recorte_de_filas_lo_hace_el_dominio_y_no_el_motor() -> None:
    """I-12. Un motor que devuelve 10.000 filas no manda sobre `max_rows` del rol."""
    engine = RecordingEngine(rows=10_000)
    result = screen(
        "SELECT customer_id FROM dim_customer",
        principal=Principal(id="prop", role=Role.OPS, source=RoleSource.CLI_FLAG),
        schema=_SCHEMA,
        policy=_POLICY,
        budgets=_BUDGETS,
        stats=_STATS,
    )
    assert result.query is not None
    assert result.query.max_rows == _BUDGETS.max_rows(Role.OPS) == 2_000
    # Y el LIMIT viaja DENTRO del árbol que se ejecuta, no en una variable aparte.
    assert "LIMIT 2000" in result.query.sql().upper()
    engine.execute(result.query)
