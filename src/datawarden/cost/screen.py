"""Los dos primeros anillos encadenados: **valida y luego cuesta**.

El orden no es negociable y es la mitad del valor del sistema. Estimar antes de
validar sería estimar el coste de una consulta que quizá ni siquiera se puede
ejecutar —y peor: obligaría al estimador a razonar sobre un árbol sin cualificar,
donde los alias no están resueltos y `SELECT *` no está expandido, así que la poda
por proyección contaría columnas que no son—.

**Qué NO hace este módulo: ejecutar.** Devuelve una `ValidatedQuery` o un rechazo, y
quien la lleva al motor es el `AuditedExecutor` de la fase 5, que es el único camino
a `Engine.execute()`. Separarlo así es lo que permite que `G-BUDGET-ESCAPE` se pruebe
por CONTADOR: si el veredicto es un rechazo y el contador del motor no se movió, la
consulta cara no llegó, y eso es una propiedad y no una promesa.
"""

from __future__ import annotations

from dataclasses import dataclass

from datawarden.catalog.statistics import Statistics
from datawarden.catalog.types import CatalogSchema
from datawarden.cost.budget import enforce
from datawarden.cost.estimator import estimate
from datawarden.domain.types import (
    CostEstimate,
    Principal,
    RejectionReason,
    ValidatedQuery,
)
from datawarden.guard.context import DEFAULT_TIMEOUT_MS
from datawarden.guard.validator import validate
from datawarden.principal.budgets import BudgetBook, Decision
from datawarden.principal.policy import AccessPolicy


@dataclass(frozen=True, slots=True)
class ScreenResult:
    """El veredicto de los dos anillos, con todo lo que la auditoría necesita.

    Lleva el coste ESTIMADO incluso cuando rechaza, y es deliberado: `G-COST-CALIB`
    compara estimado contra real, y sin guardar el estimado de las rechazadas la
    calibración solo vería las baratas — justo la mitad que no importa.
    """

    query: ValidatedQuery | None
    rejection: RejectionReason | None
    cost: CostEstimate | None = None
    decision: Decision | None = None
    warning: str | None = None

    @property
    def accepted(self) -> bool:
        return self.query is not None


def screen(
    sql: str,
    *,
    principal: Principal,
    schema: CatalogSchema,
    policy: AccessPolicy,
    budgets: BudgetBook,
    stats: Statistics,
    dialect: str = "duckdb",
    timeout_ms: float = DEFAULT_TIMEOUT_MS,
) -> ScreenResult:
    """Anillo 3 y anillo 4. Nunca lanza, nunca ejecuta."""
    verdict = validate(
        sql,
        principal=principal,
        schema=schema,
        policy=policy,
        max_rows=budgets.max_rows(principal.role),
        dialect=dialect,
        timeout_ms=timeout_ms,
    )
    if isinstance(verdict, RejectionReason):
        return ScreenResult(query=None, rejection=verdict)

    cost = estimate(verdict, stats)
    budget = enforce(cost, budgets, principal.role)
    if budget.rejection is not None:
        return ScreenResult(
            query=None,
            rejection=budget.rejection,
            cost=cost,
            decision=budget.decision,
        )
    return ScreenResult(
        query=verdict,
        rejection=None,
        cost=cost,
        decision=budget.decision,
        warning=budget.warning,
    )
