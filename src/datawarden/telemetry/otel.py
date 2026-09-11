"""El traductor. **El único módulo del repositorio que escribe `gen_ai.*`.**

`docs/CONTRACTS/otel-genai.md §4` da la lista literal de atributos obligatorios y §6 la
regla que no se negocia: *un fallo de observabilidad nunca tumba la aplicación*. Las dos
cosas viven aquí porque las dos son propiedades de la frontera con el estándar, no del
dominio.

**Por qué `to_attributes` devuelve un diccionario y no toca un span.** Porque así el
contrato se puede comprobar sin levantar un SDK, y porque el mismo diccionario sirve para
un span, para un log y para el informe. Emitir es otra responsabilidad, y está en `emit`,
que es tres líneas y un `except`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

from datawarden.telemetry.model import LlmCall

#: La versión de las convenciones semánticas contra la que traduce este módulo.
#:
#: Está DUPLICADA a propósito con `otel-semconv.lock`, y un test comprueba que coinciden.
#: El fichero es el pin —lo que se firma y se revisa—; esta constante es lo que el código
#: cree estar cumpliendo. Que puedan discrepar es justo lo que hay que poder detectar:
#: subir el pin sin revisar el traductor es el error que §3 prohíbe.
SEMCONV_VERSION: Final = "v1.42.0"

#: El prefijo del estándar. Nada nuestro entra aquí.
GEN_AI: Final = "gen_ai."

#: Lista LITERAL de §4. Un span que no los lleve todos es un span roto.
MANDATORY: Final[tuple[str, ...]] = (
    "gen_ai.operation.name",
    "gen_ai.provider.name",
    "gen_ai.request.model",
    "gen_ai.response.model",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.response.finish_reasons",
    "server.address",
)

#: Los «si aplica» de §4. Se emiten solo cuando tienen valor, que es lo que significa.
OPTIONAL: Final[tuple[str, ...]] = (
    "gen_ai.request.temperature",
    "gen_ai.request.max_tokens",
    "error.type",
)

#: Todo `gen_ai.*` que este proyecto tiene permitido emitir. **Es una allowlist**, por el
#: mismo motivo que el guard: inventar un nombre dentro de `gen_ai.*` es el error que
#: hace inútil la integración con cualquier herramienta de terceros, y una denylist no
#: puede prohibir lo que todavía no se le ha ocurrido a nadie.
ALLOWED_GEN_AI: Final[frozenset[str]] = frozenset(
    name for name in (*MANDATORY, *OPTIONAL) if name.startswith(GEN_AI)
)


def to_attributes(call: LlmCall) -> dict[str, Any]:
    """Los atributos de un span de invocación de modelo, en la versión pineada.

    Lo nuestro sale bajo `app.*` y lo del estándar bajo `gen_ai.*`, y no hay un solo
    sitio donde se decida eso dos veces.
    """
    attributes: dict[str, Any] = {
        "gen_ai.operation.name": call.operation,
        # `provider.name`, NO `system`: la spec lo renombró en v1.37.0 y el nombre viejo
        # sigue apareciendo en la mitad de los ejemplos de internet.
        "gen_ai.provider.name": call.provider,
        "gen_ai.request.model": call.requested_model,
        "gen_ai.response.model": call.responded_model,
        # `input`/`output`, NO `prompt`/`completion`: renombrados en v1.27.0.
        "gen_ai.usage.input_tokens": call.input_tokens,
        "gen_ai.usage.output_tokens": call.output_tokens,
        "gen_ai.response.finish_reasons": list(call.finish_reasons),
        "server.address": call.server_address,
    }

    if call.temperature is not None:
        attributes["gen_ai.request.temperature"] = call.temperature
    if call.max_tokens is not None:
        attributes["gen_ai.request.max_tokens"] = call.max_tokens
    if call.error_type is not None:
        attributes["error.type"] = call.error_type

    propios: dict[str, Any] = {
        "app.cost.eur": call.cost_eur,
        "app.cost.pricing_version": call.pricing_version,
        "app.prompt.id": call.prompt_id,
        "app.prompt.version": call.prompt_version,
        "app.prompt.sha256": call.prompt_sha256,
        "app.ttft_ms": call.ttft_ms,
        "app.stream.client_disconnected": call.client_disconnected,
    }
    attributes.update({k: v for k, v in propios.items() if v is not None})
    return attributes


def atributos_ausentes(attributes: dict[str, Any]) -> tuple[str, ...]:
    """Los obligatorios que faltan. Vacío es lo único aceptable."""
    return tuple(name for name in MANDATORY if name not in attributes)


def gen_ai_inventados(attributes: dict[str, Any]) -> tuple[str, ...]:
    """Los `gen_ai.*` que este proyecto se ha inventado. **Tiene que ser vacío.**

    Es la mitad del valor de `G-OTEL-ATTRS`: el contrato dice que inventar dentro de
    `gen_ai.*` te saca del estándar que el proyecto 02 dice defender, y eso no se puede
    comprobar leyendo el código —se comprueba sobre lo que se emite—.
    """
    return tuple(
        sorted(
            name
            for name in attributes
            if name.startswith(GEN_AI) and name not in ALLOWED_GEN_AI
        )
    )


class Dropped:
    """Cuántos spans se han descartado. `app.spans.dropped` de §4, contador.

    **Un descarte silencioso es peor que un descarte medido.** El contrato lo dice con
    esas palabras y lo pone en el panel: el documento del 02 declara a la vez «pérdida
    de trazas 0 %» y «la cola se llena y descarta sin bloquear», que son objetivos en
    tensión, y se resuelven exponiendo el contador en vez de fingir que no pasa.
    """

    def __init__(self) -> None:
        self.count = 0

    def drop(self) -> None:
        self.count += 1


def emit(
    call: LlmCall,
    sink: Callable[[dict[str, Any]], None],
    *,
    dropped: Dropped,
) -> bool:
    """Manda los atributos al `sink`. **Nunca lanza.** Devuelve si llegó.

    §6, la regla que no se negocia: si el almacén de trazas no responde, la aplicación
    sigue sirviendo y descarta el span contando cuántos. Una capa de observabilidad que
    puede tirar la aplicación es peor que no tenerla — y por eso el `except` es
    `Exception` a secas y no una lista de las que se nos ocurran hoy: el fallo puede
    venir de un socket, de un serializador o de una cola llena, y la respuesta correcta
    es la misma para todos.

    La traducción va DENTRO del `try` a propósito: un `LlmCall` con un campo imposible
    tampoco puede tumbar a quien lo llamó.
    """
    try:
        sink(to_attributes(call))
    except Exception:
        dropped.drop()
        return False
    return True
