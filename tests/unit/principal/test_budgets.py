"""Los presupuestos por rol. `docs/spec/budgets.yaml`, zona TDD obligatorio.

FASE ROJA. En memoria: la carga del fichero real vive en `tests/contract/` (I-13).

Lo que se prueba es que los TRES números son tres decisiones distintas y no «el
presupuesto» dicho tres veces: el blando pide confirmación, el duro no ejecuta, y
`max_rows` recorta en el dominio y nunca en el motor (I-12).
"""

from __future__ import annotations

import pytest

from datawarden.domain.types import Role
from datawarden.principal.budgets import BudgetBook, Decision, budgets_from_dict

_FIXTURE: dict[str, object] = {
    "version": 1,
    "source": "fixture",
    "source_sha256": "0" * 64,
    "unit_base": 1_000_000_000,
    "measures": "bytes_escaneados_estimados",
    "roles": {
        "analyst": {
            "soft_bytes": 300_000_000,
            "hard_bytes": 600_000_000,
            "soft_gb": 0.30,
            "hard_gb": 0.60,
            "max_rows": 50_000,
            "soft_is_calibrated": False,
        },
        "ops": {
            "soft_bytes": 20_000_000,
            "hard_bytes": 50_000_000,
            "soft_gb": 0.02,
            "hard_gb": 0.05,
            "max_rows": 2_000,
            "soft_is_calibrated": False,
        },
        "finance": {
            "soft_bytes": 400_000_000,
            "hard_bytes": 800_000_000,
            "soft_gb": 0.40,
            "hard_gb": 0.80,
            "max_rows": 100_000,
            "soft_is_calibrated": False,
        },
        "admin": {
            "soft_bytes": 750_000_000,
            "hard_bytes": 1_500_000_000,
            "soft_gb": 0.75,
            "hard_gb": 1.50,
            "max_rows": 250_000,
            "soft_is_calibrated": False,
        },
    },
}


@pytest.fixture
def book() -> BudgetBook:
    return budgets_from_dict(_FIXTURE)


def test_los_cuatro_roles_tienen_presupuesto(book: BudgetBook) -> None:
    """Un rol sin presupuesto ejecutaría sin límite, que es peor que no tener rol."""
    for role in Role:
        assert book.for_role(role).hard_bytes > 0


def test_por_debajo_del_blando_se_ejecuta_sin_preguntar(book: BudgetBook) -> None:
    assert book.decide(Role.ANALYST, estimated_bytes=1_000) is Decision.EXECUTE


def test_entre_el_blando_y_el_duro_se_pide_confirmacion(book: BudgetBook) -> None:
    """Un guardián que solo sabe decir «no» se desactiva en tres semanas."""
    assert book.decide(Role.ANALYST, estimated_bytes=450_000_000) is Decision.CONFIRM


def test_por_encima_del_duro_no_se_ejecuta(book: BudgetBook) -> None:
    """`G-BUDGET-ESCAPE` es un axioma: cero consultas caras llegan al motor."""
    assert book.decide(Role.ANALYST, estimated_bytes=600_000_001) is Decision.REJECT


def test_el_limite_duro_es_inclusivo(book: BudgetBook) -> None:
    """Exactamente el presupuesto SÍ se ejecuta; el axioma es «por encima»."""
    assert book.decide(Role.ANALYST, estimated_bytes=600_000_000) is Decision.CONFIRM


def test_ops_es_el_mas_estrecho_y_es_deliberado(book: BudgetBook) -> None:
    """Firmado así: ops mira un pago concreto; si necesita más, no es su rol."""
    assert book.for_role(Role.OPS).hard_bytes < book.for_role(Role.ANALYST).hard_bytes
    assert book.for_role(Role.OPS).max_rows == 2_000


def test_el_tope_de_filas_lo_pone_el_dominio(book: BudgetBook) -> None:
    """I-12: ningún engine aplica límites propios."""
    assert book.max_rows(Role.FINANCE) == 100_000


def test_un_presupuesto_sin_calibrar_lo_declara(book: BudgetBook) -> None:
    """Publicar un `soft` inventado como si estuviera medido es una mentira silenciosa."""
    assert book.for_role(Role.ADMIN).soft_is_calibrated is False


def test_una_estimacion_negativa_no_se_acepta(book: BudgetBook) -> None:
    with pytest.raises(ValueError, match="estimated_bytes"):
        book.decide(Role.OPS, estimated_bytes=-1)


def test_un_blando_por_encima_del_duro_no_se_puede_cargar() -> None:
    """Un aviso que salta después del rechazo no avisa de nada."""
    roto = {**_FIXTURE, "roles": {**_FIXTURE["roles"]}}  # type: ignore[dict-item]
    roto["roles"] = dict(_FIXTURE["roles"])  # type: ignore[arg-type]
    roto["roles"]["ops"] = {**roto["roles"]["ops"], "soft_bytes": 10**12}  # type: ignore[index]
    with pytest.raises(ValueError, match="soft"):
        budgets_from_dict(roto)


def test_falta_un_rol_y_se_dice_cual() -> None:
    incompleto = dict(_FIXTURE)
    incompleto["roles"] = {k: v for k, v in _FIXTURE["roles"].items() if k != "finance"}  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="finance"):
        budgets_from_dict(incompleto)
