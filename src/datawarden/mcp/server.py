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

from datawarden.audit.executor import AuditedExecutor, RunResult
from datawarden.domain.types import Principal, RejectionReason
from datawarden.mcp.principal import SPOOF_KEYS
from datawarden.principal.budgets import Decision

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


def to_payload(result: RunResult) -> dict[str, Any]:
    """El resultado de una invocación, en la forma que publica el `outputSchema`.

    **`columns_masked` viaja al cliente a propósito.** Que una columna salga
    enmascarada no es un secreto: decirlo evita que quien lee la respuesta confunda
    un `***` con un dato real de la base, y le dice exactamente qué columna pedir de
    otra forma. Ocultarlo solo protegería del usuario, no del atacante.
    """
    if result.rejection is not None:
        return {"outcome": "rejected", "rejected": _rejection_dict(result.rejection)}
    rows = result.rows
    assert rows is not None
    return {
        "outcome": "rows",
        "result": {
            "columns": list(rows.columns),
            "rows": [list(row) for row in rows.rows],
            "row_count": len(rows.rows),
            "truncated": rows.truncated,
            "columns_masked": list(result.query.masked_columns) if result.query else [],
        },
    }


def _rejection_dict(rejection: RejectionReason) -> dict[str, Any]:
    return {
        "rule_id": rejection.rule_id,
        "code": rejection.code,
        "message": rejection.message,
        "suggestion": rejection.suggestion,
        "severity": rejection.severity.value,
        "position": rejection.position.value,
        "subject": rejection.subject,
        "alternative": rejection.alternative,
        "retryable": rejection.retryable,
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
            "required": ["table"],
            "properties": {"table": {"type": "string"}},
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


class WardenTools:
    """Las cuatro herramientas, sobre el ejecutor auditado. **No deciden nada.**

    El `principal` se recibe en el constructor y NUNCA se lee de los argumentos de
    una llamada: es la mitad ejecutable de `G-ROLE-SPOOF`. Un método que aceptara un
    `role=` en su firma sería un agujero con forma de comodidad.
    """

    def __init__(self, *, executor: AuditedExecutor, principal: Principal) -> None:
        self._executor = executor
        self._principal = principal

    def run_query(self, question_sql: str, question: str | None = None) -> dict[str, Any]:
        return to_payload(
            self._executor.run(question_sql, principal=self._principal, question=question)
        )

    def explain_cost(self, question_sql: str) -> dict[str, Any]:
        """Lo que COSTARÍA, sin ejecutar nada. Anillos 2 y 3, y ahí se para.

        Usa `screen()` a propósito, que es justo lo que `check_mask_path.py` prohíbe
        en el ejecutor. Aquí es correcto y allí no, y la diferencia es exactamente la
        que importa: **esto no devuelve ni una fila**, así que no hay nada que
        enmascarar; el ejecutor sí las devuelve, y por eso tiene que pasar por el
        anillo 4. La regla no es «llama siempre a `screen_and_mask`», es «lo que
        devuelve datos pasa por el anillo 4».

        **Límite declarado, y prefiero escribirlo que descubrirlo luego:** esto NO
        deja registro de auditoría, porque el invariante I-06 habla del camino al
        motor y esto no llega al motor. Aun así, repetir `explain_cost` con distintos
        predicados revela cómo está particionado el almacén, que es información. Si
        se decide auditarlo, hace falta un estado nuevo en
        `docs/spec/audit-record.schema.json` y `G-AUDIT-COV` pasa de cuatro estados a
        cinco: es una decisión de contrato, no un añadido, y va al buzón antes que al
        código.
        """
        from datawarden.cost.screen import screen

        result = screen(
            question_sql,
            principal=self._principal,
            schema=self._executor.schema,
            policy=self._executor.policy,
            budgets=self._executor.budgets,
            stats=self._executor.stats,
        )
        if result.rejection is not None and result.cost is None:
            # Rechazo del GUARD: ni siquiera se llegó a estimar, así que no hay coste
            # que publicar. Devolver un cero aquí sería el mismo error que cobrar
            # cero por una tabla de 4,1 GB.
            return {"outcome": "rejected", "rejected": _rejection_dict(result.rejection)}

        cost = result.cost
        assert cost is not None
        budget = self._executor.budgets.for_role(self._principal.role)
        return {
            "outcome": "rows",
            "result": {
                "columns": ["estimated_bytes", "estimated_rows", "files", "budget", "decision"],
                "rows": [
                    [
                        cost.estimated_bytes,
                        cost.estimated_rows,
                        cost.files_scanned,
                        budget.hard_bytes,
                        (result.decision or Decision.EXECUTE).value,
                    ]
                ],
                "row_count": 1,
                "truncated": False,
                "columns_masked": [],
            },
        }

    def sample_table(self, table: str, limit: int = 10) -> dict[str, Any]:
        """Unas filas sin condición. **Lo que se interpola sale del CATÁLOGO.**

        Aquí el servidor compone SQL, y componer SQL con un nombre que viene de una
        petición es la forma clásica de comerse una inyección. Se cierra por
        construcción y en dos pasos, no escapando cadenas:

        1. **El nombre de la tabla se RESUELVE contra el catálogo generado antes de
           componer nada.** Lo que acaba dentro del `SELECT` no es la cadena que
           mandó el cliente: es `found.name`, un identificador que ya estaba en
           `catalog/generated/schema.json`. Una tabla desconocida —o una con una
           subconsulta dentro— no resuelve y se va por el rechazo, sin llegar a
           componerse.
        2. Y aunque lo anterior fallara, **lo que se ejecuta es el árbol que el guard
           validó**, nunca esta cadena: R004 rechaza toda relación fuera del catálogo
           y la allowlist rechaza todo nodo desconocido. La defensa es estructural.
        """
        found = self._executor.schema.table(table.lower())
        if found is None:
            return _unknown_relation(table)
        columns = [c.name for c in found.columns if c.published]
        projection = ", ".join(columns) if columns else "*"
        # `found.name` y `columns` salen del catálogo; `limit` pasa por `int()` y el
        # `inputSchema` ya lo acota entre 1 y 100. Ningún trozo viene de la petición.
        sql = f"SELECT {projection} FROM {found.name} LIMIT {int(limit)}"  # noqa: S608
        return to_payload(self._executor.run(sql, principal=self._principal))

    def describe_table(self, table: str) -> dict[str, Any]:
        """La ficha publicada de una tabla. **No lee ni una fila y no cuesta escaneo.**"""
        found = self._executor.schema.table(table.lower())
        if found is None:
            return _unknown_relation(table)
        return {
            "outcome": "rows",
            "result": {
                "columns": ["column", "type", "derives_from"],
                "rows": [
                    [c.name, c.engine_type, ", ".join(c.derives_from)]
                    for c in found.columns
                    if c.published
                ],
                "row_count": sum(1 for c in found.columns if c.published),
                "truncated": False,
                "columns_masked": [],
            },
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
    result: dict[str, Any] = method(**safe)
    return result


def _unknown_relation(table: str) -> dict[str, Any]:
    """El rechazo de una tabla que no está en el catálogo, con el mismo `rule_id`.

    Se responde con la forma de R004 y no con un error del protocolo porque para
    quien pregunta es exactamente el mismo suceso que si hubiera llegado por SQL, y
    darle dos formas distintas al mismo hecho obliga al cliente a tratarlo dos veces.
    """
    return {
        "outcome": "rejected",
        "rejected": {
            "rule_id": "R004",
            "code": "relation_out_of_scope",
            "message": f"relation {table.lower()} is not in the generated catalog",
            "suggestion": "read the catalog resource and use one of the relations it lists",
            "severity": "security",
            "position": "statement",
            "subject": table.lower(),
            "alternative": None,
            "retryable": True,
        },
    }


def catalog_resource(schema_json: dict[str, Any]) -> str:
    """El catálogo COMPLETO como recurso, no dentro del prompt.

    32 tablas y 428 columnas no caben cómodas en un prompt, y meterlas obligaría a
    recortarlas justo cuando el modelo necesita el esquema entero. Como recurso, el
    cliente lo pide una vez y lo cachea el `ttlMs` que declara el discover.
    """
    return json.dumps(schema_json, indent=2, sort_keys=True, ensure_ascii=False)
