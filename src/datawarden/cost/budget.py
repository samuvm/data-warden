"""El presupuesto, aplicado. `G-BUDGET-ESCAPE` es un AXIOMA.

**Ninguna consulta por encima del presupuesto llega al motor.** No «casi ninguna»,
no «se avisa y se ejecuta»: cero. Por eso la meta lleva `propuesta_admisible: false`
y por eso este módulo no tiene ninguna vía de escape ni bandera para saltárselo.

Y hay una segunda mitad que importa igual: **el rechazo tiene que ser accionable.**
Un «te has pasado de presupuesto» sin decir cuánto costaba, cuánto cabía y qué
recortar es el rechazo que enseña a reintentar lo mismo — y `G-RECOVERY` mide
exactamente si el modelo se corrige solo con el mensaje. Por eso la sugerencia se
construye MIRANDO EL DETALLE del estimador: si la consulta se comió las 730
particiones, lo que hay que decir es «pon un predicado de fecha», no «hazla más
pequeña».
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from datawarden.domain.types import (
    CostEstimate,
    Position,
    RejectionReason,
    Role,
    Severity,
)
from datawarden.principal.budgets import BudgetBook, Decision


@dataclass(frozen=True, slots=True)
class BudgetVerdict:
    """Qué hacer con la consulta, y por qué.

    `warning` existe para el caso intermedio: por encima del blando, la consulta NO
    se rechaza y quien la lanza tiene derecho a saber que va a costar. En la fase 7
    eso se convierte en un `InputRequiredResult` de MRTR; hasta entonces es texto.
    """

    decision: Decision
    rejection: RejectionReason | None = None
    warning: str | None = None


def enforce(cost: CostEstimate, book: BudgetBook, role: Role) -> BudgetVerdict:
    """Aplica el presupuesto del rol al coste estimado."""
    budget = book.for_role(role)
    decision = book.decide(role, estimated_bytes=cost.estimated_bytes)

    if decision is Decision.EXECUTE:
        return BudgetVerdict(decision=decision)

    if decision is Decision.CONFIRM:
        return BudgetVerdict(
            decision=decision,
            warning=(
                f"this query is estimated at {_human(cost.estimated_bytes)}, above the "
                f"{_human(budget.soft_bytes)} advisory limit for role {role.value} and "
                f"below the {_human(budget.hard_bytes)} hard limit"
            ),
        )

    return BudgetVerdict(
        decision=decision,
        rejection=RejectionReason(
            rule_id="BUDGET",
            code="over_budget",
            message=(
                f"the query would scan about {_human(cost.estimated_bytes)} "
                f"({cost.estimated_bytes} bytes across {cost.files_scanned} files) and "
                f"role {role.value} may scan at most "
                f"{_human(budget.hard_bytes)} ({budget.hard_bytes} bytes)"
            ),
            suggestion=_suggestion(cost, role),
            severity=Severity.BUDGET,
            position=Position.STATEMENT,
            subject=f"{cost.estimated_bytes} bytes",
            # Acotar la pregunta SÍ arregla esto, al revés que un intento de
            # escritura: reintentar aquí es exactamente lo que se quiere.
            retryable=True,
        ),
    )


def _suggestion(cost: CostEstimate, role: Role) -> str:
    """La sugerencia mira el detalle del estimador, no un texto genérico."""
    unpruned = _table_without_pruning(cost.detail)
    if unpruned is not None:
        table, column = unpruned
        return (
            f"add a predicate on {table}.{column} so the engine can skip partitions: "
            f"`WHERE {column} >= DATE '...' AND {column} < DATE '...'`. Scanning every "
            "partition is what makes the query expensive, not the number of columns"
        )
    wide = _widest_table(cost.detail)
    if wide is not None:
        return (
            f"name fewer columns from {wide}: this warehouse is columnar, so every "
            "column you drop is bytes the engine never reads"
        )
    return (
        f"narrow the question: add a date range, aggregate earlier, or ask for fewer "
        f"columns. Role {role.value} is meant for that shape of query"
    )


def _table_without_pruning(detail: dict[str, Any]) -> tuple[str, str] | None:
    """La primera tabla particionada cuyas particiones NO se podaron."""
    for name, spec in (detail.get("per_table") or {}).items():
        total = spec.get("partitions_total", 1)
        kept = spec.get("partitions_kept", 1)
        if total > 1 and kept >= total:
            # `event_date` es la columna de partición de las dos tablas grandes de
            # este almacén; el estimador la conoce y el mensaje la nombra.
            return (name, "event_date")
    return None


def _widest_table(detail: dict[str, Any]) -> str | None:
    tables: dict[str, Any] = detail.get("per_table") or {}
    if not tables:
        return None
    widest: str = max(tables, key=lambda name: int(tables[name].get("bytes", 0)))
    return widest


def _human(nbytes: int) -> str:
    """Bytes en una unidad que una persona pueda comparar de un vistazo.

    GB decimales, como `docs/spec/budgets.yaml` declara. Que el contrato fije la
    base evita la discusión de GB frente a GiB justo donde más confunde: en un
    mensaje de rechazo.
    """
    for unit, size in (("GB", 1_000_000_000), ("MB", 1_000_000), ("kB", 1_000)):
        if nbytes >= size:
            return f"{nbytes / size:.2f} {unit}"
    return f"{nbytes} B"
