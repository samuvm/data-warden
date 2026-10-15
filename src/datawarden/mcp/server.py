"""MCP server, spec 2026-07-28. Adapter: tested by contract and snapshot, not coverage.

No policy is decided here; the protocol is translated into domain calls that are
tested elsewhere.

The July 2026 spec is a breaking generation: sessions, the `initialize` handshake,
`ping` and SSE resumability are gone; `FastMCP` became `MCPServer`; `sampling`,
`roots` and `logging` are deprecated. Implemented here:

- `server/discover` with `ttlMs` and `cacheScope`, required on `list` results.
- `tools/list` in deterministic order, taken from the contract.
- `resultType` on every response.
- `inputSchema` and `outputSchema` in JSON Schema 2020-12, with `oneOf` between
  `rows` and `rejected`. A rejection is a legitimate answer, not a protocol error;
  flagging it as an error would make clients retry instead of reading it.
- stdio and Streamable HTTP. HTTP+SSE is not supported (deprecated by the spec).

Not done here:

- Deciding the role. It comes from `service/principal.py`, never from `_meta` or
  `arguments` (`G-ROLE-SPOOF`).
- Reaching the engine. Everything goes through `AuditedExecutor`, the only path
  (I-06), enforced by an import-linter contract.
- Composing SQL. What runs is `ast.sql()` of the validated tree.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final

from datawarden.service.principal import SPOOF_KEYS
from datawarden.service.tools import WardenTools
from datawarden.service.tracing import trace_id_from

#: TTL of `list` results. One hour because the catalog is regenerated (I-07) and
#: `G-CATALOG-FRESH` watches its freshness. Scope is `public` because the published
#: catalog is the same for every role; what varies by role is column policy.
TTL_MS: Final = 3_600_000
CACHE_SCOPE: Final = "public"

#: `tools/list` order, explicit so snapshots are stable.
TOOL_ORDER: Final = ("run_query", "describe_table", "sample_table", "explain_cost")

#: JSON Schema 2020-12. Results are `oneOf` `rows` or `rejected`; both are valid
#: answers.
SCHEMA_DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A published tool: its description and both schemas."""

    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


def rejection_schema() -> dict[str, Any]:
    """Shape of a rejection, per `docs/spec/rejection.schema.json`.

    Part of `outputSchema` rather than a protocol error: it carries the rule, what
    happened and what to do instead. A client treating it as an error retries blind;
    one treating it as data shows it to the user.
    """
    return {
        "type": "object",
        "required": ["rule_id", "code", "message", "suggestion", "severity", "position"],
        "properties": {
            "rule_id": {"type": "string", "description": "La regla que rechazó, p. ej. R008"},
            "code": {"type": "string"},
            "message": {"type": "string", "description": "Qué pasó, en una frase"},
            "suggestion": {"type": "string", "description": "Qué hacer en su lugar"},
            "severity": {"type": "string", "enum": ["security", "policy", "internal"]},
            "position": {"type": "string", "description": "Dónde del árbol saltó"},
            "subject": {"type": ["string", "null"]},
            "alternative": {
                "type": ["string", "null"],
                "description": "La columna o forma que sí se admite, si la hay",
            },
            "retryable": {"type": "boolean"},
        },
        "additionalProperties": False,
    }


def rows_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["columns", "rows", "row_count", "truncated"],
        "properties": {
            "columns": {"type": "array", "items": {"type": "string"}},
            "rows": {"type": "array", "items": {"type": "array"}},
            "row_count": {"type": "integer", "minimum": 0},
            "truncated": {
                "type": "boolean",
                "description": "El recorte lo hace el DOMINIO por `max_rows`, no el motor",
            },
            "columns_masked": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Las columnas que el anillo 4 reescribió. Evidencia, no promesa",
            },
            # Optional; present only when the soft budget was crossed and the client
            # cannot answer an MRTR confirmation, so the cost is at least reported.
            "budget_warning": {
                "type": "string",
                "description": "Cruzó el presupuesto blando y el cliente no sabe confirmar",
            },
        },
        "additionalProperties": False,
    }


def result_schema() -> dict[str, Any]:
    """`oneOf` rows or rejection. Both are answers; neither is a failure."""
    return {
        "$schema": SCHEMA_DIALECT,
        "type": "object",
        "required": ["outcome"],
        "oneOf": [
            {
                "properties": {
                    "outcome": {"const": "rows"},
                    "result": rows_schema(),
                },
                "required": ["outcome", "result"],
            },
            {
                "properties": {
                    "outcome": {"const": "rejected"},
                    "rejected": rejection_schema(),
                },
                "required": ["outcome", "rejected"],
            },
        ],
    }


def tool_specs(contract: dict[str, Any]) -> tuple[ToolSpec, ...]:
    """The four tools, in contract order.

    Order comes from `docs/spec/tools.yaml`: the spec requires a deterministic
    `tools/list`. Raises if the contract names a tool this module cannot build, since
    publishing fewer tools than declared would misreport `server/discover`.
    """
    inputs: dict[str, dict[str, Any]] = {
        "run_query": {
            "type": "object",
            "required": ["question_sql"],
            "properties": {
                "question_sql": {"type": "string", "description": "La consulta SELECT"},
                "question": {
                    "type": "string",
                    "description": "La pregunta original. Se guarda HASHEADA en la auditoría",
                },
            },
            "additionalProperties": False,
        },
        "describe_table": {
            "type": "object",
            # `table` is optional: without it the table list is returned, the only
            # discovery path that works in every client (resources are optional for
            # MCP clients; tools are not).
            "properties": {
                "table": {
                    "type": "string",
                    "description": (
                        "La tabla a describir. Omítelo para ver la lista de tablas que existen."
                    ),
                }
            },
            "additionalProperties": False,
        },
        "sample_table": {
            "type": "object",
            "required": ["table"],
            "properties": {
                "table": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
            },
            "additionalProperties": False,
        },
        "explain_cost": {
            "type": "object",
            "required": ["question_sql"],
            "properties": {"question_sql": {"type": "string"}},
            "additionalProperties": False,
        },
    }

    by_name = {str(t["nombre"]): t for t in contract["herramientas"]}
    specs: list[ToolSpec] = []
    for name in contract["orden"]:
        tool = by_name.get(str(name))
        if tool is None or str(name) not in inputs:
            message = (
                f"the contract `docs/spec/tools.yaml` names {name!r} and this server "
                "cannot build it. Publishing fewer tools than the contract declares "
                "would misreport `server/discover`."
            )
            raise KeyError(message)
        specs.append(
            ToolSpec(
                name=str(name),
                title=str(tool["titulo"]),
                description=" ".join(str(tool["descripcion"]).split()),
                input_schema={"$schema": SCHEMA_DIALECT, **inputs[str(name)]},
                output_schema=result_schema(),
            )
        )
    return tuple(specs)


def discover_payload(specs: tuple[ToolSpec, ...], version: str) -> dict[str, Any]:
    """`server/discover`, required since spec 2026-07-28.

    Carries `ttlMs` and `cacheScope`, which the spec requires on `list` results so
    clients know whether and for how long to cache the tool list.
    """
    return {
        "resultType": "complete",
        "ttlMs": TTL_MS,
        "cacheScope": CACHE_SCOPE,
        "supportedVersions": ["2026-07-28"],
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {"listChanged": False, "subscribe": False},
        },
        "tools": [
            {
                "name": spec.name,
                "title": spec.title,
                "description": spec.description,
                "inputSchema": spec.input_schema,
                "outputSchema": spec.output_schema,
            }
            for spec in specs
        ],
    }


def dispatch(
    tools: WardenTools, name: str, arguments: dict[str, Any], meta: Any = None
) -> dict[str, Any]:
    """`tools/call`: the entry point for everything a client sends.

    This is where `G-ROLE-SPOOF` is won or lost. A naive
    `getattr(tools, name)(**arguments)` would let `arguments = {"role": "admin"}`
    decide policy as soon as some signature accepted it.

    1. `meta` is not forwarded. Its `traceparent` links traces and grants nothing.
    2. Arguments are filtered against the real signature, and spoofing keys are
       dropped first, in case a `role=` parameter is ever added.
    3. An unknown tool is rejected with the list of valid ones instead of raising:
       the client is a model and an actionable message helps it.
    """
    import inspect

    method = getattr(tools, name, None)
    if name not in TOOL_ORDER or method is None or not callable(method):
        return {
            "outcome": "rejected",
            "rejected": {
                "rule_id": "INTERNAL",
                "code": "unknown_tool",
                "message": f"there is no tool called {name!r}",
                "suggestion": f"use one of: {', '.join(TOOL_ORDER)}",
                "severity": "internal",
                "position": "statement",
                "subject": name,
                "alternative": None,
                "retryable": False,
            },
        }

    accepted = set(inspect.signature(method).parameters)
    safe = {
        key: value
        for key, value in (arguments or {}).items()
        if key in accepted and key.lower() not in SPOOF_KEYS
    }
    # `traceparent` comes from `_meta`, never from `arguments`, and is used only as
    # data to link traces. Taking it from `arguments` would let the model forge
    # correlation between requests in the audit trail.
    if "trace_id" in accepted:
        safe["trace_id"] = trace_id_from(meta)
    result: dict[str, Any] = method(**safe)
    return result


def catalog_resource(schema_json: dict[str, Any]) -> str:
    """The full catalog as a resource rather than inside the prompt.

    32 tables and 428 columns do not fit comfortably in a prompt. As a resource the
    client fetches it once and caches it for the `ttlMs` declared by discover.
    """
    return json.dumps(schema_json, indent=2, sort_keys=True, ensure_ascii=False)
