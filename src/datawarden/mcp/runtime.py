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

from mcp.server.mcpserver import Context
from mcp.types import InputRequiredResult

from datawarden.mcp import server as warden
from datawarden.principal.budgets import Decision
from datawarden.service.principal import from_server_process
from datawarden.service.tools import WardenTools

#: La raíz del repositorio, DEDUCIDA DEL PAQUETE y no del directorio de trabajo.
#:
#: **Esto nace de un fallo real de Q-009.** El README decía que se pusiera `cwd` en la
#: configuración del cliente, y **Claude Desktop lo ignora**: lanzaba el proceso desde
#: otro sitio y `uv run warden` moría con `Failed to spawn: warden`. Un servidor que
#: lo lanza una aplicación de escritorio no puede dar por hecho su directorio de
#: trabajo — lo elige quien lo lanza, y no hay forma de obligarle.
#:
#: Los artefactos generados (`schema.json`, `policy.json`, `budgets.json`) ya eran
#: relativos al paquete y por eso no fallaron. Estos dos no lo eran.
REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[3]

#: El contrato de las herramientas. Es la MISMA fuente que mide `G-TOOL-CHOICE`: si
#: el servidor publicara otras descripciones que las evaluadas, el número dejaría de
#: decir algo sobre lo que un cliente ve de verdad.
TOOLS_CONTRACT: Final = REPO_ROOT / "docs" / "spec" / "tools.yaml"

SERVER_NAME: Final = "data-warden"
SERVER_VERSION: Final = "0.7.0"

INSTRUCTIONS: Final = (
    "Almacén analítico de pagos con guard por AST, presupuesto y enmascarado por rol. "
    # EMPIEZA POR AQUÍ, y va lo primero por un motivo medido: en Q-009 un cliente sin
    # lectura de recursos MCP dejó al modelo adivinando nombres de tabla a ciegas
    # —`customers`, `clientes`, `pagos`— hasta rendirse. Los recursos son OPCIONALES
    # para un cliente; las herramientas no.
    "EMPIEZA llamando a `describe_table` SIN argumentos: devuelve las tablas que "
    "existen. Después `describe_table` con una de ellas para ver sus columnas. "
    "Solo entonces escribe SQL `SELECT` de DuckDB en `run_query`. "
    "No inventes nombres de tabla: los que no estén en esa lista se rechazan. "
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
    # Una ruta relativa se resuelve contra la RAÍZ DEL REPOSITORIO, nunca contra el
    # directorio de trabajo: quien lanza este proceso es una aplicación de escritorio
    # y elige el suyo. Una ruta absoluta se respeta tal cual.
    database = database if database.is_absolute() else REPO_ROOT / database

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
    tools = WardenTools(executor=executor, principal=from_server_process())

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


#: El identificador de la pregunta dentro de `input_requests`. Uno solo: esta ronda
#: pregunta una cosa y nada más.
_CONFIRM_ID: Final = "confirm_soft_budget"

#: El estado opaco que el servidor manda y el cliente devuelve. Sirve para distinguir
#: «ronda uno» de «el cliente ya contestó», que es lo único que hace falta recordar.
_CONFIRM_STATE: Final = "soft-budget-confirmation"


def _peticion_de_confirmacion(sql: str) -> InputRequiredResult:
    """La ronda 1 de MRTR: **el servidor DEVUELVE la pregunta, no la llama.**

    Aquí estaba mi error, y es exactamente el que `docs/STACK.md` avisa cuando dice que
    «el código de ejemplo de 2025 no compila». Lo implementé primero con `ctx.elicit()`,
    que es el patrón viejo: el servidor abre una petición HACIA el cliente. La spec
    2026-07-28 **eliminó las sesiones**, así que no hay canal de vuelta y el SDK lo dijo
    con todas las letras — `NoBackChannelError: this transport context has no
    back-channel for server-initiated requests`.

    Por eso la spec puso **MRTR** en su lugar: el servidor responde a la llamada con un
    `InputRequiredResult`, el cliente contesta y vuelve a llamar, y la segunda ronda lee
    `ctx.input_responses`. Sin estado en el servidor, que es la idea entera del cambio.
    """
    from mcp.types import ElicitRequest
    from mcp_types import ElicitRequestFormParams

    return InputRequiredResult(
        input_requests={
            _CONFIRM_ID: ElicitRequest(
                params=ElicitRequestFormParams(
                    message=(
                        "Esta consulta pasa del presupuesto blando de tu rol: va a "
                        "escanear más de lo habitual y todavía no se ha ejecutado. "
                        "¿La lanzo igualmente?"
                    ),
                    requested_schema={
                        "type": "object",
                        "properties": {
                            "proceed": {
                                "type": "boolean",
                                "title": "Lanzar la consulta cara",
                                "description": (
                                    "true la ejecuta; false la deja sin ejecutar. "
                                    f"Consulta: {sql[:200]}"
                                ),
                            }
                        },
                        "required": ["proceed"],
                    },
                )
            )
        },
        request_state=_CONFIRM_STATE,
    )


def _confirmado(responses: Any) -> bool:
    """Si el cliente dijo que sí. Declinar, cancelar o no contestar es que no.

    **Fail-closed en la dirección barata:** ante cualquier respuesta que no sea un
    `accept` con `proceed: true`, la consulta cara no se lanza. Equivocarse aquí cuesta
    una repetición; equivocarse al revés cuesta un escaneo que nadie pidió.
    """
    if not isinstance(responses, dict):
        return False
    answer = responses.get(_CONFIRM_ID)
    if getattr(answer, "action", None) != "accept":
        return False
    content = getattr(answer, "content", None) or {}
    return bool(content.get("proceed"))


def _mrtr_run_query(tools: WardenTools) -> Any:
    """`run_query` con **MRTR**: preguntar antes de gastar, no rechazar después.

    El presupuesto `soft` ejecutaba con un aviso que nadie leía. Un umbral blando que no
    pregunta no es blando: es decorativo. La spec 2026-07-28 retiró `sampling` y
    `elicitation` y puso el patrón de múltiples idas y vueltas en su sitio, y encaja
    exactamente con esto.

    **Se pregunta ANTES de invocar nada**, lo que deja intacto el contrato de auditoría:
    la consulta se audita cuando de verdad ocurre, con los mismos cuatro estados.
    Sondear el coste no llega al motor —es lo mismo que hace `explain_cost`—, así que no
    inventa un quinto estado.

    **Y si el cliente no entiende MRTR**, recibe un resultado `input_required` que la
    spec define y no ejecuta nada. No se le rompe: se le pide algo que no sabe dar. El
    presupuesto `soft` es un control de COSTE; el `hard` sigue rechazando solo, y
    `G-BUDGET-ESCAPE` —el axioma— no depende de esto.
    """

    async def run_query(
        question_sql: str,
        ctx: Context[Any, Any],
        question: str | None = None,
    ) -> dict[str, Any] | InputRequiredResult:
        # **EL TIPO DE RETORNO SE DECLARA, no se deja en `Any`.** El SDK lo usa para dos
        # cosas: rechaza una herramienta con salida estructurada cuyo retorno no sabe
        # serializar —`InvalidSignature: return type Any is not serializable`— y detecta
        # por la anotación que esta herramienta puede pedir entrada. Sin declararlo, el
        # servidor ni siquiera arranca, que es lo correcto: mejor no arrancar que
        # arrancar y romperse en la primera consulta cara.
        if tools.budget_decision(question_sql) is not Decision.CONFIRM:
            return tools.run_query(question_sql, question=question)
        if ctx.request_state != _CONFIRM_STATE:
            return _peticion_de_confirmacion(question_sql)
        if not _confirmado(ctx.input_responses):
            return _no_confirmada()
        return tools.run_query(question_sql, question=question)

    return run_query


def _no_confirmada() -> dict[str, Any]:
    """La consulta no se lanzó porque no se confirmó. **No es un fallo, es una decisión.**

    Se devuelve con la forma de un rechazo —la misma que el guard— para que el cliente
    no tenga que tratar dos formas distintas del suceso «no hay filas y este es el
    motivo». Y `retryable: true`, porque confirmar es exactamente lo que hay que hacer
    para que ocurra.
    """
    return {
        "outcome": "rejected",
        "rejected": {
            "rule_id": "BUDGET",
            "code": "not_confirmed",
            "message": (
                "the query exceeds the soft budget for this role and the confirmation "
                "was declined, so it was not run"
            ),
            "suggestion": (
                "narrow the query — a date range or fewer columns usually does it — or "
                "run it again and confirm"
            ),
            "severity": "policy",
            "position": "statement",
            "subject": "soft budget",
            "alternative": None,
            "retryable": True,
        },
    }


def _register(server: Any, tools: WardenTools, spec: warden.ToolSpec) -> None:
    """Cuelga una herramienta con la descripción del CONTRATO, no con su docstring.

    La descripción es lo que `G-TOOL-CHOICE` mide, así que tiene que salir del mismo
    fichero que se evaluó. Un docstring que se editara «para que quede mejor» movería
    lo que ve el cliente sin mover el número, que es la peor forma de que una métrica
    deje de significar algo.
    """
    # `run_query` se registra envuelto en MRTR; las otras tres, directas. Solo esa
    # gasta presupuesto, así que solo esa tiene algo que preguntar.
    method = _mrtr_run_query(tools) if spec.name == "run_query" else getattr(tools, spec.name)
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
