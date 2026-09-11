"""Modelo interno de una llamada a modelo, y su traducción a OpenTelemetry.

`docs/CONTRACTS/otel-genai.md §2` lo exige así y el motivo está medido en el propio
contrato: **ningún atributo `gen_ai.*` es estable.** `gen_ai.system` pasó a
`gen_ai.provider.name` en v1.37.0 y `usage.prompt_tokens` a `usage.input_tokens` en
v1.27.0. Con el esquema externo metido por dentro, cada una de esas rupturas habría sido
una migración; con un modelo propio y un traductor, es un fichero.

    dominio → LlmCall (interno, estable, nuestro)
                  └─→ traductor → atributos de la versión pineada en otel-semconv.lock

`model.py` **no conoce un solo nombre `gen_ai.*`** y eso es un test, no una intención.
`otel.py` es el único sitio del repositorio que los escribe.
"""

from datawarden.telemetry.model import LlmCall
from datawarden.telemetry.otel import (
    MANDATORY,
    SEMCONV_VERSION,
    atributos_ausentes,
    emit,
    to_attributes,
)

__all__ = [
    "MANDATORY",
    "SEMCONV_VERSION",
    "LlmCall",
    "atributos_ausentes",
    "emit",
    "to_attributes",
]
