"""El presupuesto, aplicado. Zona TDD obligatorio.

FASE ROJA. `G-BUDGET-ESCAPE` es un AXIOMA —`propuesta_admisible: false`— y dice una
cosa muy concreta: **ninguna consulta por encima del presupuesto llega al motor.**
No «casi ninguna», no «se avisa»: cero.

Lo que se prueba aquí es la decisión y, sobre todo, **que el rechazo sea accionable**.
Un «te has pasado de presupuesto» sin decir cuánto ni qué hacer es el rechazo que
enseña a reintentar lo mismo, y `G-RECOVERY` mide justo eso en la fase 6.
"""

from __future__ import annotations

import pytest

from datawarden.cost.budget import enforce
from datawarden.domain.types import CostEstimate, RejectionReason, Role, Severity
from datawarden.principal.budgets import BudgetBook, Decision, budgets_from_dict

_BOOK: BudgetBook = budgets_from_dict(
    {
        "roles": {
            "analyst": {
                "soft_bytes": 300,
                "hard_bytes": 600,
                "max_rows": 50,
                "soft_is_calibrated": False,
            },
            "ops": {
                "soft_bytes": 20,
                "hard_bytes": 50,
                "max_rows": 5,
                "soft_is_calibrated": False,
            },
            "finance": {
                "soft_bytes": 400,
                "hard_bytes": 800,
                "max_rows": 100,
                "soft_is_calibrated": False,
            },
            "admin": {
                "soft_bytes": 750,
                "hard_bytes": 1500,
                "max_rows": 250,
                "soft_is_calibrated": False,
            },
        }
    }
)


def _cost(nbytes: int, *, rows: int = 10, files: int = 1) -> CostEstimate:
    return CostEstimate(
        estimated_bytes=nbytes,
        estimated_rows=rows,
        files_scanned=files,
        method="iceberg",
        detail={
            "per_table": {
                "fact_payment_attempt": {
                    "known": True,
                    "bytes": nbytes,
                    "columns": ["amount_minor", "event_date"],
                    "partitions_kept": 730,
                    "partitions_total": 730,
                }
            }
        },
    )


def test_por_debajo_del_blando_se_ejecuta_sin_rechazo() -> None:
    verdict = enforce(_cost(10), _BOOK, Role.ANALYST)
    assert verdict.decision is Decision.EXECUTE
    assert verdict.rejection is None


def test_entre_el_blando_y_el_duro_se_pide_confirmacion_pero_no_se_rechaza() -> None:
    """Un guardián que solo sabe decir «no» se desactiva en tres semanas."""
    verdict = enforce(_cost(450), _BOOK, Role.ANALYST)
    assert verdict.decision is Decision.CONFIRM
    assert verdict.rejection is None
    assert verdict.warning


def test_por_encima_del_duro_se_rechaza_y_es_un_axioma() -> None:
    verdict = enforce(_cost(601), _BOOK, Role.ANALYST)
    assert verdict.decision is Decision.REJECT
    assert isinstance(verdict.rejection, RejectionReason)
    assert verdict.rejection.rule_id == "BUDGET"
    assert verdict.rejection.severity is Severity.BUDGET


def test_el_rechazo_dice_cuanto_costaba_y_cuanto_cabia() -> None:
    """Sin los dos números, quien lee el rechazo no sabe cuánto tiene que recortar."""
    mensaje = enforce(_cost(1_500_000_000), _BOOK, Role.OPS).rejection.message
    assert "1.5 GB" in mensaje or "1500000000" in mensaje or "1,5" in mensaje
    assert "0.05" in mensaje or "50000000" in mensaje


def test_el_rechazo_sugiere_acotar_por_particion_cuando_no_se_podo() -> None:
    """La sugerencia mira el DETALLE del estimador, no un texto genérico.

    Si la consulta escaneó las 730 particiones, lo que hay que decir es «pon un
    predicado de fecha», no «haz la consulta más pequeña».
    """
    sugerencia = enforce(_cost(10**12), _BOOK, Role.OPS).rejection.suggestion
    assert "event_date" in sugerencia or "date" in sugerencia.lower()


def test_el_rechazo_es_reintentable() -> None:
    """Acotar la pregunta SÍ arregla esto, al revés que un intento de escritura."""
    assert enforce(_cost(10**9), _BOOK, Role.OPS).rejection.retryable is True


def test_ops_se_pasa_donde_analyst_no() -> None:
    """El mismo coste, dos veredictos: el presupuesto es del ROL."""
    assert enforce(_cost(100), _BOOK, Role.ANALYST).decision is Decision.EXECUTE
    assert enforce(_cost(100), _BOOK, Role.OPS).decision is Decision.REJECT


def test_gastar_exactamente_el_presupuesto_no_se_pasa() -> None:
    """El axioma dice «POR ENCIMA del presupuesto». Un byte de margen no es un fallo."""
    assert enforce(_cost(600), _BOOK, Role.ANALYST).decision is not Decision.REJECT


def test_una_estimacion_negativa_no_se_acepta() -> None:
    with pytest.raises(ValueError, match="estimated_bytes"):
        CostEstimate(estimated_bytes=-1, estimated_rows=0, files_scanned=0, method="iceberg")


def test_si_la_consulta_ya_podo_la_sugerencia_habla_de_columnas() -> None:
    """La sugerencia mira el DETALLE: decir «pon un predicado de fecha» a quien ya lo
    puso es el mensaje que enseña a ignorar los mensajes.
    """
    coste = CostEstimate(
        estimated_bytes=10**9,
        estimated_rows=1,
        files_scanned=1,
        method="iceberg",
        detail={
            "per_table": {
                "fact_payment_attempt": {
                    "bytes": 10**9,
                    "partitions_kept": 1,
                    "partitions_total": 730,
                }
            }
        },
    )
    sugerencia = enforce(coste, _BOOK, Role.OPS).rejection.suggestion
    assert "fewer columns" in sugerencia
    assert "fact_payment_attempt" in sugerencia


def test_sin_detalle_la_sugerencia_sigue_siendo_accionable() -> None:
    """Un rechazo mudo es peor que uno genérico, y este no puede quedarse mudo."""
    coste = CostEstimate(
        estimated_bytes=10**9, estimated_rows=1, files_scanned=1, method="iceberg", detail={}
    )
    sugerencia = enforce(coste, _BOOK, Role.OPS).rejection.suggestion
    assert "date range" in sugerencia
    assert "ops" in sugerencia


def test_el_mensaje_usa_unidades_que_una_persona_compara_de_un_vistazo() -> None:
    """GB decimales, como declara `budgets.yaml`: la base la fija el contrato."""
    assert "1.00 GB" in enforce(_cost(10**9), _BOOK, Role.OPS).rejection.message
    assert "1.00 MB" in enforce(_cost(10**6), _BOOK, Role.OPS).rejection.message
    assert "1.00 kB" in enforce(_cost(1000), _BOOK, Role.OPS).rejection.message
    assert "999 B" in enforce(_cost(999), _BOOK, Role.OPS).rejection.message
