"""El servidor MCP · spec 2026-07-28. **Adaptador: contrato y snapshot, no cobertura.**

`docs/RULES.md` clasifica `mcp/` como adaptador, y eso decide cómo se prueba: por
CONTRATO —los esquemas, el orden, los tipos de resultado— y no por cobertura de
línea. Aquí no se decide nada de política; se traduce un protocolo a llamadas de
dominio que ya están probadas.

**La spec de julio de 2026 es una ruptura de generación** y el código de ejemplo de
2025 no compila: se fueron las sesiones, el handshake `initialize`, `ping` y la
resumibilidad SSE; `FastMCP` pasó a `MCPServer`; `sampling`, `roots` y `logging`
están deprecados. Lo que este servidor implementa de esa spec:

- `server/discover` con `ttlMs` y `cacheScope`, obligatorios en los `list`.
- `tools/list` con **orden determinista**, tomado del contrato y no de un diccionario.
- `resultType` en toda respuesta.
- `inputSchema` y `outputSchema` en JSON Schema 2020-12, con `oneOf` entre `rows` y
  `rejected`: **un rechazo no es un error del protocolo, es una respuesta legítima**,
  y marcarlo como error haría que el cliente lo reintentara en vez de leerlo.
- stdio y Streamable HTTP. **HTTP+SSE no**: la spec lo reclasificó a *Deprecated*.

**Y lo que NO hace, que es igual de importante:**

- **No decide el rol.** Viene de `mcp/principal.py`, nunca de `_meta` ni de
  `arguments`. `G-ROLE-SPOOF` es un axioma.
- **No llega al motor.** Todo pasa por `AuditedExecutor`, que es el único camino
  (I-06) y el único que atraviesa los cuatro anillos. El contrato de import-linter lo
  impone; no es disciplina.
- **No compone SQL.** Lo que se ejecuta es `ast.sql()` del árbol validado.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final

from datawarden.service.principal import SPOOF_KEYS
from datawarden.service.tools import WardenTools
from datawarden.service.tracing import trace_id_from

#: El TTL de los `list`. Una hora porque el catálogo se REGENERA (I-07) y
#: `G-CATALOG-FRESH` ya vigila que no envejezca; el ámbito es `public` porque el
#: catálogo publicado es el mismo para todos los roles — lo que cambia por rol es la
#: POLÍTICA sobre las columnas, no la lista de tablas.
TTL_MS: Final = 3_600_000
CACHE_SCOPE: Final = "public"

#: El orden de `tools/list`. **Determinista y explícito.** Un orden que sale de un
#: diccionario cambia entre ejecuciones y convierte cualquier snapshot en ruido.
TOOL_ORDER: Final = ("run_query", "describe_table", "sample_table", "explain_cost")

#: JSON Schema 2020-12. El `oneOf` es el punto: `rows` o `rejected`, y las dos son
#: respuestas correctas del sistema.
SCHEMA_DIALECT: Final = "https://json-schema.org/draft/2020-12/schema"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Una herramienta publicada: su descripción y sus dos esquemas."""

    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


def rejection_schema() -> dict[str, Any]:
    """La forma de un rechazo. Conforme a `docs/spec/rejection.schema.json`.

    Va en el `outputSchema` y no en un error del protocolo porque **el rechazo es la
    respuesta más valiosa que da este sistema**: lleva la regla que saltó, qué pasó y
    qué hacer en su lugar. Un cliente que lo reciba como error lo reintenta a ciegas;
    uno que lo reciba como dato se lo enseña a quien preguntó.
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
        },
        "additionalProperties": False,
    }


def result_schema() -> dict[str, Any]:
    """`oneOf` entre filas y rechazo. Las dos son respuestas, no una de ellas un fallo."""
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
    """Las cuatro herramientas, **en el orden del contrato**.

    El orden sale de `docs/spec/tools.yaml` y no de recorrer un diccionario: la spec
    exige `tools/list` determinista, y además es lo que hace que un snapshot signifique
    algo. Si el contrato nombra una herramienta que este módulo no sabe construir, se
    LEVANTA: publicar tres cuando el contrato dice cuatro sería mentir en el discover.
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
            # `table` NO es obligatorio: sin él se devuelve la LISTA de tablas, que es
            # el único camino de descubrimiento que funciona en cualquier cliente.
            # Los recursos MCP son opcionales para un cliente; las herramientas no.
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
                f"el contrato `docs/spec/tools.yaml` nombra {name!r} y este servidor no "
                "sabe construirla. Publicar menos herramientas de las que el contrato "
                "declara sería mentir en `server/discover`."
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
    """`server/discover`, obligatorio desde la spec 2026-07-28.

    Lleva `ttlMs` y `cacheScope` porque la spec los exige en los `list`: sin TTL, un
    cliente no sabe si puede cachear el catálogo de herramientas ni por cuánto, y
    acaba pidiéndolo en cada vuelta o cacheándolo para siempre.
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
    """`tools/call`: el punto por el que entra TODO lo que manda un cliente.

    **Aquí está el sitio exacto donde `G-ROLE-SPOOF` se gana o se pierde.** Un
    despachador escrito con prisa hace `getattr(tools, name)(**arguments)` y da por
    bueno lo que venga; el día que alguien añada un `role=` a una firma —o que un
    cliente mande `arguments = {"role": "admin"}` contra una firma que lo acepte por
    `**kwargs`— la política se decide con un dato del cliente.

    Tres decisiones, y ninguna es paranoia:

    1. **`meta` NO SE PASA.** Se recibe para poder registrarlo y se queda aquí. El
       `traceparent` que trae encadena trazas; no concede nada.
    2. **Los argumentos se FILTRAN contra la firma real**, y cualquier clave
       sospechosa se descarta antes. Sobra con lo primero mientras nadie ponga un
       `role=` en una firma; lo segundo es para el día en que alguien lo haga.
    3. Una herramienta desconocida se rechaza nombrando las que hay, en vez de
       levantar: el cliente es un modelo y un mensaje accionable le sirve.
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
    # EL `traceparent` SALE DE `_meta` Y NUNCA DE `arguments`. Es la única cosa del
    # sobre que se usa, y se usa como DATO: encadena la traza y no concede nada.
    # Tomarlo de `arguments` dejaría que el modelo se inventara la correlación de una
    # petición con otra, que es justo lo que la auditoría no puede permitirse.
    if "trace_id" in accepted:
        safe["trace_id"] = trace_id_from(meta)
    result: dict[str, Any] = method(**safe)
    return result


def catalog_resource(schema_json: dict[str, Any]) -> str:
    """El catálogo COMPLETO como recurso, no dentro del prompt.

    32 tablas y 428 columnas no caben cómodas en un prompt, y meterlas obligaría a
    recortarlas justo cuando el modelo necesita el esquema entero. Como recurso, el
    cliente lo pide una vez y lo cachea el `ttlMs` que declara el discover.
    """
    return json.dumps(schema_json, indent=2, sort_keys=True, ensure_ascii=False)
