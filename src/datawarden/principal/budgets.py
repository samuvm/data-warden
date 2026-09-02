"""Presupuestos por rol: el blando avisa, el duro no ejecuta, `max_rows` recorta.

Son TRES decisiones distintas y llamarlas «el presupuesto» a las tres es como se
acaba con un solo número que no sirve para ninguna de las tres cosas.

- **`hard_bytes`** hace verdadera `G-BUDGET-ESCAPE`, que es un axioma: cero
  consultas por encima del presupuesto llegan al motor. No se negocia en caliente.
- **`soft_bytes`** pide confirmación en vez de rechazar. Existe porque un guardián
  que solo sabe decir «no» se desactiva en tres semanas. En la fase 7 es un
  `InputRequiredResult` de MRTR.
- **`max_rows`** recorta en el dominio y nunca en el motor (I-12): si cada engine
  aplicara el suyo, DuckDB y Athena devolverían resultados distintos para el mismo
  rol, y el criterio de aceptación nº 5 de `PROJECT.md` fallaría por eso.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from datawarden.domain.types import Role


class Decision(StrEnum):
    """Qué hacer con una consulta según lo que el estimador dijo que costaría."""

    EXECUTE = "execute"
    CONFIRM = "confirm"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class Budget:
    """El presupuesto de un rol, en bytes ya convertidos."""

    role: Role
    soft_bytes: int
    hard_bytes: int
    max_rows: int
    #: `False` mientras `soft_bytes` sea una hipótesis del agente y no una medida de
    #: `make cost-calibration`. Publicarlo como si estuviera medido sería una
    #: mentira silenciosa, que es la clase de error que este proyecto persigue.
    soft_is_calibrated: bool


@dataclass(frozen=True, slots=True)
class BudgetBook:
    """Los cuatro presupuestos, cargados una vez."""

    budgets: Mapping[Role, Budget]
    source_sha256: str = ""

    def for_role(self, role: Role) -> Budget:
        return self.budgets[role]

    def max_rows(self, role: Role) -> int:
        return self.budgets[role].max_rows

    def decide(self, role: Role, *, estimated_bytes: int) -> Decision:
        """El veredicto de coste.

        El límite duro es INCLUSIVO: el axioma dice «ninguna consulta POR ENCIMA del
        presupuesto llega al motor», así que gastar exactamente el presupuesto está
        dentro. Fijarlo aquí evita que dos capas lo interpreten de dos maneras y que
        la diferencia aparezca como un fallo de `G-BUDGET-ESCAPE` de un byte.
        """
        if estimated_bytes < 0:
            msg = (
                f"estimated_bytes={estimated_bytes}. Un coste negativo no significa "
                "nada, y aceptarlo dejaría pasar cualquier consulta cuyo estimador "
                "se equivocara de signo."
            )
            raise ValueError(msg)
        budget = self.budgets[role]
        if estimated_bytes > budget.hard_bytes:
            return Decision.REJECT
        if estimated_bytes > budget.soft_bytes:
            return Decision.CONFIRM
        return Decision.EXECUTE


def budgets_from_dict(payload: dict[str, Any]) -> BudgetBook:
    """Construye el libro de presupuestos desde el JSON compilado. Puro."""
    rows = payload["roles"]
    budgets: dict[Role, Budget] = {}
    for role in Role:
        if role.value not in rows:
            msg = (
                f"falta el presupuesto del rol {role.value!r}. Un rol sin presupuesto "
                "ejecutaría sin límite: sería exactamente el agujero que "
                "G-BUDGET-ESCAPE existe para cerrar, y sin nada que lo delate."
            )
            raise ValueError(msg)
        row = rows[role.value]
        soft = int(row["soft_bytes"])
        hard = int(row["hard_bytes"])
        if soft > hard:
            msg = (
                f"{role.value}: soft_bytes ({soft}) por encima de hard_bytes ({hard}). "
                "Un aviso que salta después del rechazo no avisa de nada."
            )
            raise ValueError(msg)
        budgets[role] = Budget(
            role=role,
            soft_bytes=soft,
            hard_bytes=hard,
            max_rows=int(row["max_rows"]),
            soft_is_calibrated=bool(row.get("soft_is_calibrated", False)),
        )
    return BudgetBook(
        budgets=MappingProxyType(budgets),
        source_sha256=str(payload.get("source_sha256", "")),
    )


def load_budgets(path: pathlib.Path) -> BudgetBook:
    """Carga los presupuestos compilados. La única función que toca disco."""
    return budgets_from_dict(json.loads(path.read_text(encoding="utf-8")))
