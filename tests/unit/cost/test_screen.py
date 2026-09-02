"""Los dos anillos encadenados. Zona TDD obligatorio de `cost/`.

`screen()` no tenía ni un test unitario y la MUTACIÓN lo dijo: cincuenta mutantes,
cero muertos. Estaba ejercitado por la propiedad de presupuesto y por la reserva
—las dos suites lentas—, así que la cobertura de línea salía al 100 % y no había
nada que verificara su comportamiento. Es exactamente la diferencia que la meta
`G-MUTATION` existe para señalar: cubrir no es verificar.

Lo que se prueba es el ORDEN y sus consecuencias: primero se valida y solo después
se cuesta, y lo que se rechaza en el anillo 3 nunca llega a estimarse.
"""

from __future__ import annotations

import pytest

from datawarden.catalog.statistics import Statistics, TableStats
from datawarden.catalog.types import CatalogSchema, ColumnSpec, TableSpec
from datawarden.cost.screen import screen
from datawarden.domain.types import Principal, Role, RoleSource
from datawarden.principal.budgets import Decision, budgets_from_dict
from datawarden.principal.policy import policy_from_dict


def _columna(name: str, tipo: str = "INTEGER") -> ColumnSpec:
    return ColumnSpec(
        name=name,
        engine_type=tipo,
        family="integer",
        nullable=True,
        ordinal=1,
        derives_from=(f"t.{name}",),
    )


_SCHEMA = CatalogSchema(
    dialect="duckdb",
    tables=(
        TableSpec(
            name="t",
            kind="table",
            columns=(_columna("a"), _columna("b"), _columna("secreta")),
        ),
    ),
)

_POLICY = policy_from_dict(
    {
        "default_level": "allow",
        "forbidden_positions_in_mask": ["where"],
        "columns": {
            "t.secreta": {
                "levels": {
                    "admin": "allow",
                    "analyst": "deny",
                    "finance": "deny",
                    "ops": "deny",
                },
                "data_type": "identificador_directo",
                "generalized": None,
            }
        },
    }
)

_BUDGETS = budgets_from_dict(
    {
        "roles": {
            r: {
                "soft_bytes": 50,
                "hard_bytes": 100,
                "max_rows": 10,
                "soft_is_calibrated": False,
            }
            for r in ("analyst", "ops", "finance", "admin")
        }
    }
)

_STATS = Statistics(
    profile="fixture",
    source="fixture",
    tables={
        "t": TableStats(
            name="t",
            rows=10,
            bytes=1000,
            files=1,
            column_bytes={"a": 10, "b": 900, "secreta": 90},
        )
    },
)


def _screen(sql: str, role: Role = Role.ANALYST):
    return screen(
        sql,
        principal=Principal(id="s", role=role, source=RoleSource.CLI_FLAG),
        schema=_SCHEMA,
        policy=_POLICY,
        budgets=_BUDGETS,
        stats=_STATS,
    )


def test_lo_barato_y_permitido_pasa_los_dos_anillos() -> None:
    result = _screen("SELECT a FROM t")
    assert result.accepted
    assert result.query is not None
    assert result.rejection is None
    assert result.cost is not None
    assert result.cost.estimated_bytes == 10
    assert result.decision is Decision.EXECUTE


def test_lo_que_el_guard_rechaza_no_llega_a_estimarse() -> None:
    """El orden importa: estimar el coste de algo que no se puede ejecutar es trabajo
    tirado, y además obligaría al estimador a razonar sobre un árbol sin cualificar.
    """
    result = _screen("SELECT secreta FROM t")
    assert not result.accepted
    assert result.rejection is not None
    assert result.rejection.rule_id == "R008"
    assert result.cost is None
    assert result.decision is None


def test_lo_caro_se_rechaza_por_presupuesto_y_conserva_el_coste() -> None:
    """El coste viaja incluso en el rechazo: sin él, `G-COST-CALIB` solo vería las
    consultas baratas, que son justo la mitad que no importa.
    """
    result = _screen("SELECT b FROM t")
    assert not result.accepted
    assert result.rejection is not None
    assert result.rejection.rule_id == "BUDGET"
    assert result.cost is not None
    assert result.cost.estimated_bytes == 900
    assert result.decision is Decision.REJECT


def test_entre_el_blando_y_el_duro_se_acepta_con_aviso() -> None:
    """Un guardián que solo sabe decir «no» se desactiva en tres semanas."""
    result = _screen("SELECT a, secreta FROM t", Role.ADMIN)
    assert result.accepted
    assert result.decision is Decision.CONFIRM
    assert result.warning
    assert "advisory" in result.warning


def test_lo_que_ni_siquiera_parsea_se_rechaza_sin_estimar() -> None:
    result = _screen("esto no es SQL ((")
    assert not result.accepted
    assert result.cost is None
    assert result.rejection is not None
    assert result.rejection.rule_id == "R001"


def test_el_arbol_que_sale_lleva_el_limit_del_rol() -> None:
    """I-12: el tope de filas es del dominio y viaja DENTRO del árbol validado."""
    result = _screen("SELECT a FROM t")
    assert result.query is not None
    assert "LIMIT 10" in result.query.sql().upper()
    assert result.query.max_rows == 10


@pytest.mark.parametrize("role", list(Role))
def test_los_cuatro_roles_atraviesan_los_dos_anillos(role: Role) -> None:
    """Un rol sin camino por el sistema es un rol que nadie ha probado."""
    result = _screen("SELECT a FROM t", role)
    assert result.accepted or result.rejection is not None
