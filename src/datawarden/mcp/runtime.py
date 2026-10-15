"""Run the MCP server over stdio. Adapter: tested by contract and snapshot.

The client (Claude Desktop, Cursor) spawns this process and talks to it over stdin
and stdout: no daemon, no port, nothing to start. DuckDB is embedded, so there is no
database server either.

No model is required. The four tools receive SQL written by the client's model; the
local model in `models.lock` is only used to measure `G-RECOVERY` and
`G-TOOL-CHOICE` offline.

No policy is decided here. The role comes from `service/principal.py` (the process
environment set by whoever installs the server), never from `_meta` or `arguments`;
the engine is reached only through `AuditedExecutor`, built by the `audit/` factory.

Everything that can fail does so at startup: a server that connects and then breaks
on the first query makes the user blame the query.
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

#: Repository root, derived from the package location rather than the working
#: directory. Observed with Claude Desktop as client: it ignores a configured `cwd`
#: and spawns the process elsewhere, so `uv run warden` failed with
#: `Failed to spawn: warden`. A desktop-launched server cannot assume its cwd.
REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[3]

#: Tool contract. Same source `G-TOOL-CHOICE` measures, so published descriptions
#: are exactly the evaluated ones.
TOOLS_CONTRACT: Final = REPO_ROOT / "docs" / "spec" / "tools.yaml"

SERVER_NAME: Final = "data-warden"
SERVER_VERSION: Final = "0.7.0"

INSTRUCTIONS: Final = (
    "Almacén analítico de pagos con guard por AST, presupuesto y enmascarado por rol. "
    # Discovery instruction first. Observed with Claude Desktop as client: without
    # MCP resource support the model guessed table names (`customers`, `clientes`,
    # `pagos`) until giving up. Resources are optional for clients; tools are not.
    "EMPIEZA llamando a `describe_table` SIN argumentos: devuelve las tablas que "
    "existen. Después `describe_table` con una de ellas para ver sus columnas. "
    "Solo entonces escribe SQL `SELECT` de DuckDB en `run_query`. "
    "No inventes nombres de tabla: los que no estén en esa lista se rechazan. "
    "Un rechazo NO es un error: trae la regla que saltó, qué pasó y qué hacer en su "
    "lugar. Léelo y corrige eso concreto; no intentes rodearlo, está comprobado."
)


def load_contract(path: pathlib.Path = TOOLS_CONTRACT) -> dict[str, Any]:
    """Read the tool contract. Parsed by the adapter, not the domain.

    PyYAML is a transitive dependency and no domain module imports it. It is
    acceptable here as in `scripts/`: this is the edge, runs once at startup, and is
    off every query's critical path.
    """
    import yaml  # type: ignore[import-untyped]

    if not path.exists():
        message = (
            f"{path} does not exist. It is the contract of the four tools and the "
            "same source `G-TOOL-CHOICE` is measured against."
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
    """Build the `MCPServer` with its four tools and the catalog resource."""
    from mcp.server import CacheHint, MCPServer

    from datawarden.audit.factory import DEFAULT_AUDIT_DB, build_executor
    from datawarden.mask.config import load_mask_config
    from datawarden.principal import POLICY_PATH
    from datawarden.principal.policy import load_policy

    contract = load_contract(contract_path)
    specs = warden.tool_specs(contract)
    # Relative paths resolve against the repository root, not the cwd chosen by
    # the launching desktop app. Absolute paths are kept as is.
    database = database if database.is_absolute() else REPO_ROOT / database

    # The pepper comes from the environment with no default (a default pepper is a
    # public one). If missing, this raises at startup instead of hashing columns
    # with a known key.
    mask = load_mask_config(load_policy(POLICY_PATH))
    executor = build_executor(
        database=database,
        audit_db=audit_db or DEFAULT_AUDIT_DB,
        mask=mask,
    )
    # The role comes from the process, set by whoever installs the server; never
    # from `_meta` or `arguments` (`G-ROLE-SPOOF`).
    tools = WardenTools(executor=executor, principal=from_server_process())

    hint = CacheHint(ttl_ms=warden.TTL_MS, scope=warden.CACHE_SCOPE)
    server = MCPServer(
        name=SERVER_NAME,
        title="Data Warden",
        version=SERVER_VERSION,
        instructions=INSTRUCTIONS,
        # `ttlMs` and `cacheScope` are required on `list` results by spec
        # 2026-07-28.
        cache_hints={
            "server/discover": hint,
            "tools/list": hint,
            "resources/list": hint,
            "resources/read": hint,
        },
    )

    # Registration follows contract order: the spec requires a deterministic
    # `tools/list`, and snapshots depend on it.
    for spec in specs:
        _register(server, tools, spec)

    _register_catalog(server, executor)
    return server


#: Id of the single question inside `input_requests`.
_CONFIRM_ID: Final = "confirm_soft_budget"

#: Opaque state the server sends and the client echoes back, to tell the first round
#: from the answered one.
_CONFIRM_STATE: Final = "soft-budget-confirmation"


def _peticion_de_confirmacion(sql: str) -> InputRequiredResult:
    """MRTR round 1: the server returns the question instead of calling the client.

    The pre-2026 pattern, `ctx.elicit()`, opens a server-initiated request. Spec
    2026-07-28 removed sessions, so there is no back-channel and the SDK raises
    `NoBackChannelError` (see `docs/STACK.md`). With MRTR the server answers with an
    `InputRequiredResult`, the client calls again with its answer, and round 2 reads
    `ctx.input_responses`. The server stays stateless.
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
    """Whether the client said yes. Declining, cancelling or not answering means no.

    Fail-closed in the cheap direction: anything other than `accept` with
    `proceed: true` does not run the expensive query. A wrong no costs a retry; a
    wrong yes costs an unrequested scan.
    """
    if not isinstance(responses, dict):
        return False
    answer = responses.get(_CONFIRM_ID)
    if getattr(answer, "action", None) != "accept":
        return False
    content = getattr(answer, "content", None) or {}
    return bool(content.get("proceed"))


def _mrtr_run_query(tools: WardenTools) -> Any:
    """`run_query` with MRTR: ask before spending rather than warn afterwards.

    A soft budget that only attaches a warning nobody reads is decorative. Spec
    2026-07-28 replaced `sampling` and `elicitation` with the multi-round-trip
    pattern, which fits this exactly.

    The question is asked before anything is invoked, so the audit contract is
    unchanged: the query is audited when it actually runs, with the same four
    statuses. Probing the cost does not reach the engine (same as `explain_cost`).

    The soft budget is a cost control; the hard budget still rejects on its own and
    `G-BUDGET-ESCAPE` does not depend on this path.
    """

    async def run_query(
        question_sql: str,
        ctx: Context[Any, Any],
        question: str | None = None,
    ) -> dict[str, Any] | InputRequiredResult:
        # The return type must be declared, not `Any`: the SDK rejects structured
        # output it cannot serialize (`InvalidSignature: return type Any is not
        # serializable`) and uses the annotation to detect that the tool may request
        # input. Without it the server fails at startup.
        if tools.budget_decision(question_sql) is not Decision.CONFIRM:
            return tools.run_query(question_sql, question=question)
        # Returning `InputRequiredResult` to a client that did not declare
        # `elicitation` fails the call with `MCPError: Elicitation not supported`,
        # making the tool unusable across the whole soft band. Observed with Claude
        # Desktop as client: 17 of 92 calls (18 %) failed this way, all `CONFIRM`.
        #
        # For such clients the soft budget falls back to running with a warning in
        # the response. The hard budget still rejects on its own and is what
        # `G-BUDGET-ESCAPE` covers.
        if not _sabe_confirmar(ctx):
            return _con_aviso(tools.run_query(question_sql, question=question))
        if ctx.request_state != _CONFIRM_STATE:
            return _peticion_de_confirmacion(question_sql)
        if not _confirmado(ctx.input_responses):
            return _no_confirmada()
        return tools.run_query(question_sql, question=question)

    return run_query


def _sabe_confirmar(ctx: Context[Any, Any]) -> bool:
    """Whether the client declared it can answer an input request.

    Uses declared capabilities only. `client_capabilities` is `None` when none were
    declared, which means no.
    """
    capacidades = ctx.client_capabilities
    return capacidades is not None and capacidades.elicitation is not None


#: Warning attached to a query that crossed the soft budget without confirmation.
AVISO_BLANDO: Final = (
    "this query crossed the soft budget for this role; it ran because the client "
    "cannot answer a confirmation request. Narrow it — a date range or fewer columns "
    "usually does it — if you did not mean to scan this much"
)


def _con_aviso(payload: dict[str, Any]) -> dict[str, Any]:
    """Attach the warning to the result without touching the rows.

    Placed inside `result` because the `outputSchema` `oneOf` has exactly two
    outcomes; this is rows with a note, not a third outcome. Rejections are left
    unchanged.
    """
    cuerpo = payload.get("result")
    if isinstance(cuerpo, dict):
        cuerpo["budget_warning"] = AVISO_BLANDO
    return payload


def _no_confirmada() -> dict[str, Any]:
    """The query did not run because it was not confirmed.

    Uses the guard's rejection shape so clients handle a single "no rows, here is
    why" form. `retryable: true` because confirming is exactly what makes it run.
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
    """Register a tool with the contract's description, not its docstring.

    `G-TOOL-CHOICE` measures the description, so it must come from the evaluated
    file; a docstring edit would change what clients see without moving the metric.
    """
    # Only `run_query` spends budget, so only it is wrapped in MRTR.
    method = _mrtr_run_query(tools) if spec.name == "run_query" else getattr(tools, spec.name)
    server.add_tool(
        method,
        name=spec.name,
        title=spec.title,
        description=spec.description,
        structured_output=True,
    )


def _register_catalog(server: Any, executor: Any) -> None:
    """Register the full catalog as a resource rather than putting it in the prompt."""

    def catalog() -> str:
        return warden.catalog_resource(executor.schema.published().to_dict())

    # Called rather than used as `@`: the SDK decorator is untyped and would turn
    # `catalog()` into `Any` under `mypy --strict`.
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
    """Start the server and serve over stdin and stdout.

    Nothing may be printed to `stdout`: over stdio it is the protocol channel, and a
    stray `print` corrupts the JSON-RPC stream.
    """
    import anyio

    server = build_server(database=database, audit_db=audit_db)
    anyio.run(server.run_stdio_async)
