"""El mismo sistema por HTTP.

**Existe para demostrar que el dominio no depende del transporte.**

`docs/PLAN.md` pone `http/` antes que `mcp/` en la fase 7 y el orden es el argumento:
si las mismas cuatro operaciones se sirven por dos transportes sin tocar una línea de
`guard/`, `cost/`, `mask/` ni `audit/`, entonces la frase «el dominio no depende del
transporte» deja de ser una promesa del README y pasa a ser algo que se puede comprobar
leyendo qué importa este fichero.

**Y lo que importa es exactamente lo mismo que importa `mcp/`:** el ejecutor auditado
que construye la factoría de `audit/`, y las herramientas de `mcp/server.py`. Cero
lógica de política aquí. Si mañana alguien añade una regla en este fichero, la habrá
añadido a un transporte y no al sistema — y el otro transporte no la tendrá.

**El rol tampoco sale de aquí.** Ni de una cabecera, ni del cuerpo, ni de un parámetro:
una cabecera la elige quien llama, así que es dato transportado igual que `_meta` en
MCP. `G-ROLE-SPOOF` es un axioma y no cambia porque cambie el transporte.

**Un rechazo se responde con 200, no con 4xx.** Es deliberado y es la misma decisión
que en MCP: un rechazo del guard **no es un error del protocolo**, es la respuesta más
valiosa que da este sistema — trae la regla, el motivo y la alternativa. Devolverlo como
error haría que un cliente lo reintentara a ciegas o lo tratara como una caída, en vez
de leerlo. El `outcome` del cuerpo dice si son filas o un rechazo.
"""

from __future__ import annotations

import pathlib
from typing import Any, Final

from datawarden.domain.types import Principal
from datawarden.mask.config import MaskConfig
from datawarden.service.principal import from_server_process
from datawarden.service.tools import WardenTools

#: La versión que se publica en `/health`. La misma que declara el servidor MCP.
API_VERSION: Final = "0.7.0"


def build_app(
    *,
    database: pathlib.Path,
    audit_db: pathlib.Path | None = None,
    principal: Principal | None = None,
    mask: MaskConfig | None = None,
) -> Any:
    """Monta la aplicación FastAPI sobre el MISMO ejecutor auditado que usa MCP.

    `mask` se puede inyectar —lo hacen los tests y el gate— y en un despliegue sale de
    la variable de entorno. Que exista el parámetro no abre ninguna puerta: sin él y
    sin la variable, `load_mask_config` se niega a construir nada.
    """
    from fastapi import Body, FastAPI

    from datawarden.audit.factory import DEFAULT_AUDIT_DB, build_executor
    from datawarden.mask.config import load_mask_config
    from datawarden.principal import POLICY_PATH
    from datawarden.principal.policy import load_policy

    mask = mask or load_mask_config(load_policy(POLICY_PATH))
    executor = build_executor(
        database=database, audit_db=audit_db or DEFAULT_AUDIT_DB, mask=mask
    )
    tools = WardenTools(executor=executor, principal=principal or from_server_process())

    app = FastAPI(
        title="Data Warden",
        version=API_VERSION,
        description=(
            "El mismo guard, presupuesto, enmascarado y auditoría que sirve el servidor "
            "MCP. Un rechazo se responde con 200 y `outcome: rejected`: no es un error "
            "del protocolo, es la respuesta."
        ),
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        """Vivo y con el catálogo cargado. No toca el motor."""
        return {
            "status": "ok",
            "version": API_VERSION,
            "tables": len(executor.schema.published().tables),
        }

    @app.get("/catalog")
    def catalog() -> dict[str, Any]:
        """Las relaciones publicadas. El mismo camino de descubrimiento que en MCP."""
        return tools.describe_table()

    @app.get("/tables/{table}")
    def describe(table: str) -> dict[str, Any]:
        return tools.describe_table(table)

    @app.get("/tables/{table}/sample")
    def sample(table: str, limit: int = 10) -> dict[str, Any]:
        return tools.sample_table(table, limit=limit)

    @app.post("/query")
    def query(
        question_sql: str = Body(..., embed=True),
        question: str | None = Body(None, embed=True),
    ) -> dict[str, Any]:
        """Ejecuta si procede, y responde 200 tanto si ejecuta como si rechaza."""
        return tools.run_query(question_sql, question=question)

    @app.post("/explain")
    def explain(question_sql: str = Body(..., embed=True)) -> dict[str, Any]:
        return tools.explain_cost(question_sql)

    return app
