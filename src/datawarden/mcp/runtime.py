"""Levanta el servidor MCP sobre stdio. **Adaptador: contrato y snapshot.**

**Qué es stdio y por qué no hay nada que levantar.** El cliente —Claude Desktop,
Cursor— lanza este proceso él mismo y habla con él por la entrada y la salida
estándar. No hay demonio, no hay puerto, no hay `make up` y no hay nada que acordarse
de arrancar: cuando el cliente se cierra, el proceso muere con él. DuckDB es
embebido, así que tampoco hay servidor de base de datos.

**Y no hace falta ningún modelo.** Las cuatro herramientas RECIBEN SQL; quien lo
escribe es el modelo del cliente. El modelo local de `models.lock` solo sirve para
medir `G-RECOVERY` y `G-TOOL-CHOICE` fuera de línea. Se puede tener Ollama apagado.

**Nada de esto decide política.** El rol sale de `mcp/principal.py` —del entorno del
proceso, que lo fija quien instala el servidor— y jamás de `_meta` ni de
`arguments`; al motor se llega solo por `AuditedExecutor`, construido por la factoría
de `audit/`. Este fichero traduce un protocolo, y ya está.

**Todo lo que puede fallar, falla al arrancar.** Un servidor que arranca y revienta
en la primera consulta es peor que uno que no arranca: el cliente ya le ha dicho al
usuario que está conectado, y el usuario culpa a la consulta.
"""

from __future__ import annotations

import pathlib
from typing import Any, Final

from datawarden.mcp import server as warden
from datawarden.mcp.principal import from_server_process

#: El contrato de las herramientas. Es la MISMA fuente que mide `G-TOOL-CHOICE`: si
#: el servidor publicara otras descripciones que las evaluadas, el número dejaría de
#: decir algo sobre lo que un cliente ve de verdad.
TOOLS_CONTRACT: Final = pathlib.Path("docs/spec/tools.yaml")

SERVER_NAME: Final = "data-warden"
SERVER_VERSION: Final = "0.7.0"

INSTRUCTIONS: Final = (
    "Almacén analítico de pagos con guard por AST, presupuesto y enmascarado por rol. "
    "Escribe SQL `SELECT` de DuckDB contra las tablas del recurso `warden://catalog`. "
    "Un rechazo NO es un error: trae la regla que saltó, qué pasó y qué hacer en su "
    "lugar. Léelo y corrige eso concreto; no intentes rodearlo, está comprobado."
)


def load_contract(path: pathlib.Path = TOOLS_CONTRACT) -> dict[str, Any]:
    """Lee el contrato de herramientas. Lo parsea el ADAPTADOR, no el dominio.

    PyYAML no está en `[project.dependencies]` —entra transitivamente— y ningún
    módulo de dominio lo importa (P-002). Aquí es admisible por el mismo motivo que
    en `scripts/`: esto es el borde, corre una vez al arrancar y no está en el camino
    crítico de ninguna consulta.
    """
    import yaml  # type: ignore[import-untyped]

    if not path.exists():
        message = (
            f"no existe {path}. Es el contrato de las cuatro herramientas y la MISMA "
            "fuente con la que se mide `G-TOOL-CHOICE`: publicar descripciones que no "
            "son las evaluadas haría que ese número no dijera nada del cliente real."
        )
        raise FileNotFoundError(message)
    parsed: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return parsed


def build_server(
    *,
    database: pathlib.Path,
    audit_db: pathlib.Path | None = None,
    contract_path: pathlib.Path = TOOLS_CONTRACT,
) -> Any:
    """Monta el `MCPServer` con sus cuatro herramientas y el recurso del catálogo."""
    from mcp.server import CacheHint, MCPServer

    from datawarden.audit.factory import DEFAULT_AUDIT_DB, build_executor
    from datawarden.mask.config import load_mask_config
    from datawarden.principal import POLICY_PATH
    from datawarden.principal.policy import load_policy

    contract = load_contract(contract_path)
    specs = warden.tool_specs(contract)

    # La pimienta sale del entorno y NO tiene valor por defecto: una pimienta por
    # defecto es una pimienta pública. Si falta, esto levanta aquí y el cliente ve
    # el motivo, en vez de servir columnas hasheadas con una clave que todos saben.
    mask = load_mask_config(load_policy(POLICY_PATH))
    executor = build_executor(
        database=database,
        audit_db=audit_db or DEFAULT_AUDIT_DB,
        mask=mask,
    )
    # EL ROL SALE DEL PROCESO, que lo fija quien instala el servidor en su cliente.
    # Nunca de `_meta` ni de `arguments`: `G-ROLE-SPOOF` es un axioma.
    tools = warden.WardenTools(executor=executor, principal=from_server_process())

    hint = CacheHint(ttl_ms=warden.TTL_MS, scope=warden.CACHE_SCOPE)
    server = MCPServer(
        name=SERVER_NAME,
        title="Data Warden",
        version=SERVER_VERSION,
        instructions=INSTRUCTIONS,
        # `ttlMs` y `cacheScope` son OBLIGATORIOS en los `list` de la spec
        # 2026-07-28: sin TTL, un cliente no sabe si puede cachear el catálogo de
        # herramientas ni por cuánto, y acaba pidiéndolo en cada vuelta.
        cache_hints={
            "server/discover": hint,
            "tools/list": hint,
            "resources/list": hint,
            "resources/read": hint,
        },
    )

    # EL ORDEN DE REGISTRO ES EL DEL CONTRATO. `tools/list` determinista lo exige la
    # spec, y además es lo que hace que un snapshot signifique algo.
    for spec in specs:
        _register(server, tools, spec)

    _register_catalog(server, executor)
    return server


def _register(server: Any, tools: warden.WardenTools, spec: warden.ToolSpec) -> None:
    """Cuelga una herramienta con la descripción del CONTRATO, no con su docstring.

    La descripción es lo que `G-TOOL-CHOICE` mide, así que tiene que salir del mismo
    fichero que se evaluó. Un docstring que se editara «para que quede mejor» movería
    lo que ve el cliente sin mover el número, que es la peor forma de que una métrica
    deje de significar algo.
    """
    method = getattr(tools, spec.name)
    server.add_tool(
        method,
        name=spec.name,
        title=spec.title,
        description=spec.description,
        structured_output=True,
    )


def _register_catalog(server: Any, executor: Any) -> None:
    """El catálogo COMPLETO como recurso, no dentro del prompt.

    32 tablas y 428 columnas no caben cómodas en un prompt, y meterlas obligaría a
    recortarlas justo cuando el modelo necesita el esquema entero. Como recurso, el
    cliente lo pide una vez y lo cachea el `ttlMs` que declara el discover.
    """

    def catalog() -> str:
        return warden.catalog_resource(executor.schema.published().to_dict())

    # Se registra por llamada y no con `@`: el decorador del SDK no lleva tipos, y
    # `mypy --strict` marca —con razón— que decorar una función tipada con uno sin
    # tipar la vuelve `Any`. Llamarlo así conserva la firma de `catalog()`.
    server.resource(
        "warden://catalog",
        name="catalogo",
        title="Catálogo publicado",
        description=(
            "Tablas, columnas, tipos y linaje del almacén. Es el MISMO artefacto "
            "contra el que valida el guard, así que lo que aquí no está no se puede "
            "consultar."
        ),
        mime_type="application/json",
    )(catalog)


def serve_stdio(*, database: pathlib.Path, audit_db: pathlib.Path | None = None) -> None:
    """Arranca y se queda hablando por la entrada y la salida estándar.

    **Nada se imprime en `stdout`**, y no es un detalle de estilo: en stdio, `stdout`
    ES el canal del protocolo. Un `print` de depuración ahí corrompe el flujo JSON-RPC
    y el cliente desconecta con un error que no se parece en nada a su causa.
    """
    import anyio

    server = build_server(database=database, audit_db=audit_db)
    anyio.run(server.run_stdio_async)
