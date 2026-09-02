"""La costura de los cuatro anillos. Zona TDD obligatorio, cobertura 95 %.

FASE ROJA.

**Sin esto, `mask/` sería código muerto.** El contrato de capas pone `datawarden.mask`
POR ENCIMA de `datawarden.cost`, así que `cost/screen.py` no puede importar el
enmascarador ni ahora ni nunca, y el `AuditedExecutor` que lo invocará vive en el
anillo 5. Sin una costura propia, las diecisiete columnas `mask` seguirían saliendo en
claro **con el guard en verde**: cada pieza funcionando y nadie uniéndolas. Es la clase
de agujero que no da error.
"""

from __future__ import annotations

from datawarden.catalog import SCHEMA_PATH, load_generated
from datawarden.catalog.statistics import Statistics, TableStats
from datawarden.domain.types import Principal, Role, RoleSource
from datawarden.mask.config import MaskConfig
from datawarden.mask.pipeline import screen_and_mask
from datawarden.principal import BUDGETS_PATH, POLICY_PATH
from datawarden.principal.budgets import Decision, load_budgets
from datawarden.principal.policy import load_policy, policy_from_dict

_SCHEMA = load_generated(SCHEMA_PATH)
_POLICY = load_policy(POLICY_PATH)
_BUDGETS = load_budgets(BUDGETS_PATH)
_CONFIG = MaskConfig(pepper="pimienta-de-pruebas-suficientemente-larga")

_BARATO = Statistics(
    profile="fixture",
    source="fixture",
    tables={
        "dim_customer": TableStats(
            name="dim_customer",
            rows=10,
            bytes=1_000,
            files=1,
            column_bytes={"first_name": 500, "country_code": 500},
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
            column_bytes={"first_name": 10**12, "country_code": 10**12},
        )
    },
)

_ANALYST = Principal(id="pipe", role=Role.ANALYST, source=RoleSource.CLI_FLAG)


def _run(sql: str, *, stats: Statistics = _BARATO, principal: Principal = _ANALYST):
    return screen_and_mask(
        sql,
        principal=principal,
        schema=_SCHEMA,
        policy=_POLICY,
        budgets=_BUDGETS,
        stats=stats,
        config=_CONFIG,
    )


def test_una_consulta_legal_sale_validada_y_enmascarada() -> None:
    """El caso que justifica que este módulo exista."""
    out = _run("SELECT first_name FROM dim_customer")

    assert out.accepted
    assert out.masked_columns == ("dim_customer.first_name",)
    assert out.cost is not None


def test_una_consulta_sin_columnas_protegidas_no_declara_ninguna() -> None:
    out = _run("SELECT country_code FROM dim_customer")

    assert out.accepted
    assert out.masked_columns == ()


def test_un_rechazo_del_guard_no_llega_al_enmascarador() -> None:
    """Y el veredicto conserva su rechazo: el orden de los anillos se ve aquí."""
    out = _run("DELETE FROM dim_customer")

    assert not out.accepted
    assert out.rejection is not None
    assert out.masked_columns == ()


def test_un_rechazo_de_presupuesto_conserva_su_coste() -> None:
    """Sin el coste de las rechazadas, `G-COST-CALIB` solo vería las baratas."""
    out = _run("SELECT first_name FROM dim_customer", stats=_CARISIMO)

    assert not out.accepted
    assert out.rejection is not None
    assert out.cost is not None
    assert out.decision is Decision.REJECT


def test_admin_pasa_los_cuatro_anillos_sin_mascara() -> None:
    admin = Principal(id="pipe", role=Role.ADMIN, source=RoleSource.SERVER_PROCESS)

    out = _run("SELECT first_name FROM dim_customer", principal=admin)

    assert out.accepted
    assert out.masked_columns == ()


def test_el_orden_es_validar_costar_y_despues_enmascarar() -> None:
    """No es un detalle de implementación, es una dependencia.

    Enmascarar antes de validar sería enmascarar un árbol sin cualificar, donde los
    alias no están resueltos y `SELECT *` no está expandido: media docena de columnas
    sensibles no se encontrarían. Y enmascarar antes de presupuestar cambiaría el
    árbol que el estimador tarifó, con lo que el coste publicado dejaría de ser el de
    lo que se ejecuta.

    Se comprueba por la evidencia que deja: una consulta enmascarada llega con su
    coste ya calculado, luego el coste se calculó ANTES.
    """
    out = _run("SELECT first_name FROM dim_customer")

    assert out.masked_columns
    assert out.cost is not None
    assert out.decision is not None


def test_un_rechazo_del_enmascarador_se_propaga_y_no_se_vuelve_aceptado() -> None:
    """La rama que no puede fallar en silencio.

    El enmascarador solo rechaza cuando encuentra algo que el guard no debería haber
    dejado pasar, o una fila `mask` que su política no dice cómo enmascarar.
    Convertir eso en «aceptado sin máscara» sería exactamente la fuga que el anillo 4
    existe para cerrar: la consulta seguiría adelante y la columna saldría en claro.
    """
    rota = policy_from_dict(
        {
            "default_level": "allow",
            "deterministic_masking": True,
            "pepper_from": "config",
            "columns": {
                "dim_customer.first_name": {
                    "levels": {
                        "admin": "allow",
                        "analyst": "mask",
                        "finance": "allow",
                        "ops": "allow",
                    },
                    # Declarada `mask` y sin decir CÓMO: no hay forma de protegerla.
                    "transformation": None,
                    "generalized": None,
                    "keep_last_n": None,
                    "derived_from": [],
                }
            },
        }
    )

    out = screen_and_mask(
        "SELECT first_name FROM dim_customer",
        principal=_ANALYST,
        schema=_SCHEMA,
        policy=rota,
        budgets=_BUDGETS,
        stats=_BARATO,
        config=_CONFIG,
    )

    assert not out.accepted, "una columna que no se puede enmascarar NO puede pasar"
    assert out.rejection is not None
    assert out.rejection.rule_id == "INTERNAL"
    # Y el coste sobrevive al rechazo, igual que en los otros dos: `G-COST-CALIB`
    # necesita ver también las que no se ejecutaron.
    assert out.cost is not None
