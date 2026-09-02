"""Los cuatro anillos encadenados: **valida, cuesta, presupuesta y ENMASCARA.**

`cost/screen.py` encadena los tres primeros y se detiene ahí a propósito: el contrato
de capas pone `datawarden.mask` POR ENCIMA de `datawarden.cost`, así que `screen()` no
puede importar el enmascarador ni ahora ni nunca. Este módulo es la costura que falta,
y vive aquí porque `mask` sí puede mirar hacia abajo.

**Sin esto, `mask/` sería código muerto.** Nadie lo invocaría, `G-PII-LEAK` no tendría
cómo medirse y las diecisiete columnas `mask` seguirían saliendo en claro con el guard
en verde. Es la clase de agujero que no da error: cada pieza funciona y nadie las une.

**Qué NO hace: ejecutar.** Igual que `screen()`. Devuelve el árbol listo —validado,
presupuestado y enmascarado— y quien lo lleva al motor es el `AuditedExecutor` del
anillo 5, que es el único camino a `Engine.execute()`. Mantenerlo así es lo que permite
que `G-BUDGET-ESCAPE` se pruebe por contador: si el veredicto es un rechazo y el motor
no se movió, la consulta no llegó.
"""

from __future__ import annotations

from dataclasses import dataclass

from datawarden.catalog.statistics import Statistics
from datawarden.catalog.types import CatalogSchema
from datawarden.cost.screen import ScreenResult, screen
from datawarden.domain.types import (
    CostEstimate,
    Principal,
    RejectionReason,
    ValidatedQuery,
)
from datawarden.guard.context import DEFAULT_TIMEOUT_MS
from datawarden.mask.config import MaskConfig
from datawarden.mask.rewrite import mask_query
from datawarden.principal.budgets import BudgetBook, Decision
from datawarden.principal.policy import AccessPolicy


@dataclass(frozen=True, slots=True)
class MaskedResult:
    """El veredicto de los cuatro anillos, con todo lo que la auditoría necesita."""

    query: ValidatedQuery | None
    rejection: RejectionReason | None
    cost: CostEstimate | None = None
    decision: Decision | None = None
    warning: str | None = None

    @property
    def accepted(self) -> bool:
        return self.query is not None

    @property
    def masked_columns(self) -> tuple[str, ...]:
        """Las columnas que el anillo 4 reescribió. Evidencia, no promesa."""
        return () if self.query is None else self.query.masked_columns


def screen_and_mask(
    sql: str,
    *,
    principal: Principal,
    schema: CatalogSchema,
    policy: AccessPolicy,
    budgets: BudgetBook,
    stats: Statistics,
    config: MaskConfig,
    dialect: str = "duckdb",
    timeout_ms: float = DEFAULT_TIMEOUT_MS,
) -> MaskedResult:
    """Anillos 2, 3, 4 y 5 del mapa. Nunca lanza, nunca ejecuta.

    El orden no es negociable y cada paso depende del anterior. Enmascarar antes de
    validar sería enmascarar un árbol sin cualificar, donde los alias no están
    resueltos y `SELECT *` no está expandido: la mitad de las columnas sensibles no se
    encontrarían. Y enmascarar antes de presupuestar cambiaría el árbol que el
    estimador tarifó, con lo que el coste publicado dejaría de ser el de lo ejecutado.
    """
    result: ScreenResult = screen(
        sql,
        principal=principal,
        schema=schema,
        policy=policy,
        budgets=budgets,
        stats=stats,
        dialect=dialect,
        timeout_ms=timeout_ms,
    )
    if result.query is None:
        return MaskedResult(
            query=None,
            rejection=result.rejection,
            cost=result.cost,
            decision=result.decision,
        )

    masked = mask_query(result.query, policy=policy, config=config)
    if isinstance(masked, RejectionReason):
        # El enmascarador solo rechaza cuando encuentra algo que el guard no debería
        # haber dejado pasar. Se propaga tal cual: convertirlo en «aceptado sin
        # máscara» sería exactamente la fuga que el anillo existe para cerrar.
        return MaskedResult(
            query=None,
            rejection=masked,
            cost=result.cost,
            decision=result.decision,
        )

    return MaskedResult(
        query=masked,
        rejection=None,
        cost=result.cost,
        decision=result.decision,
        warning=result.warning,
    )
