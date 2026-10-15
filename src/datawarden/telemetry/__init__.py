"""Internal model of an LLM call and its translation to OpenTelemetry.

Required by `docs/CONTRACTS/otel-genai.md` section 2: no `gen_ai.*` attribute is
stable. `gen_ai.system` became `gen_ai.provider.name` in v1.37.0 and
`usage.prompt_tokens` became `usage.input_tokens` in v1.27.0. With an internal model
and a translator, each such break touches one file.

    domain -> LlmCall (internal, stable)
                  └─> translator -> attributes of the version pinned in otel-semconv.lock

`model.py` knows no `gen_ai.*` name (enforced by a test); `otel.py` is the only
module that writes them.
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
