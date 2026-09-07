"""Construye el ejecutor auditado. **`audit/` es el único sitio donde puede vivir.**

`datawarden.engines` solo se importa desde aquí y desde `audit/executor.py`, y no es
una convención: el contrato de import-linter «Al motor solo se llega por la auditoría
(I-06)» lo prohíbe a los diez módulos restantes. `mcp/`, `http/` y el CLI necesitan
un ejecutor, y si cada uno construyera su propio `DuckDBEngine` tendríamos tres
sitios desde los que se puede llegar al motor sin pasar por la auditoría — que es
exactamente el atajo que I-06 existe para impedir.

Lo dejó anotado el propio CLI antes de que hiciera falta: *«el día que exista
`warden query`, su motor lo construirá una factoría de `audit/`, no este fichero»*.
Esto es esa factoría.

**Todo lo que hace falta para los cinco anillos se resuelve aquí, y falla pronto si
falta algo.** Un servidor MCP que arranca y revienta en la primera consulta es peor
que uno que no arranca: el cliente ya ha dicho que está conectado.
"""

from __future__ import annotations

import pathlib
from typing import Final

from datawarden.audit.executor import AuditedExecutor
from datawarden.audit.store import AuditStore
from datawarden.mask.config import MaskConfig

#: Dónde vive la cadena de auditoría por defecto. Se crea si no existe; lo que NO se
#: crea sola es la base de datos del almacén, porque inventarla vacía convertiría
#: «no encuentro los datos» en «no hay filas», que son cosas muy distintas.
#:
#: Relativa al PAQUETE y no al directorio de trabajo: cuando el servidor lo lanza una
#: aplicación de escritorio, el directorio de trabajo lo elige ella. Con una ruta
#: relativa, la cadena de auditoría aparecería en un sitio distinto cada vez — y una
#: cadena que cambia de sitio no es una cadena.
DEFAULT_AUDIT_DB: Final = pathlib.Path(__file__).resolve().parents[3] / "var" / "audit.sqlite3"


class MissingDatasetError(RuntimeError):
    """El almacén no está donde se dijo. Se distingue de un fallo del motor."""


def build_executor(
    *,
    database: pathlib.Path,
    audit_db: pathlib.Path = DEFAULT_AUDIT_DB,
    mask: MaskConfig,
) -> AuditedExecutor:
    """El ejecutor con sus cinco anillos montados, o un error que dice qué falta.

    El `setup_sql` del motor instala la macro `warden_hash` con la MISMA pimienta que
    recibe el enmascarador. Que sean la misma no es economía: si difirieran, el árbol
    reescrito llamaría a una macro que hashea con otra clave y los valores dejarían
    de ser estables entre ejecuciones — justo lo que el contrato firmado prohíbe.
    """
    from datawarden.catalog import SCHEMA_PATH, load_generated
    from datawarden.catalog.statistics import load as load_stats
    from datawarden.cost import STATISTICS_PATH
    from datawarden.engines.duckdb_engine import DuckDBEngine
    from datawarden.mask.macro import macro_ddl
    from datawarden.principal import BUDGETS_PATH, POLICY_PATH
    from datawarden.principal.budgets import load_budgets
    from datawarden.principal.policy import load_policy

    if not database.exists():
        message = (
            f"no existe {database}. El almacén no se inventa vacío: un servidor que "
            "arranca sin datos convierte «no encuentro el almacén» en «no hay filas», "
            "y quien pregunta no puede distinguirlas. Genera el dataset con "
            "`make dataset PROFILE=dev`."
        )
        raise MissingDatasetError(message)
    if not SCHEMA_PATH.exists():
        message = (
            "no hay catálogo generado. El catálogo NO se escribe a mano (I-07): "
            "ejecuta `warden catalog build`."
        )
        raise MissingDatasetError(message)

    audit_db.parent.mkdir(parents=True, exist_ok=True)
    return AuditedExecutor(
        engine=DuckDBEngine(database, setup_sql=(macro_ddl(mask),)),
        store=AuditStore(str(audit_db)),
        schema=load_generated(SCHEMA_PATH),
        policy=load_policy(POLICY_PATH),
        budgets=load_budgets(BUDGETS_PATH),
        stats=load_stats(STATISTICS_PATH),
        mask=mask,
    )
