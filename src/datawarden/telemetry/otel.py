"""The translator: the only module that writes `gen_ai.*` attributes.

`docs/CONTRACTS/otel-genai.md` section 4 lists the mandatory attributes and section 6
states that an observability failure never breaks the application. Both are
properties of the boundary with the standard, not of the domain.

`to_attributes` returns a dict instead of touching a span, so the contract can be
checked without an SDK and the same dict serves spans, logs and reports. Emitting is
`emit`'s job.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

from datawarden.telemetry.model import LlmCall

#: Semantic conventions version this module translates to.
#:
#: Deliberately duplicated in `otel-semconv.lock`, and a test checks they match: the
#: lock file is the pin, this constant is what the code believes it implements.
#: Bumping the pin without reviewing the translator is what section 3 forbids.
SEMCONV_VERSION: Final = "v1.42.0"

#: Standard prefix. No project-specific attribute goes here.
GEN_AI: Final = "gen_ai."

#: Verbatim list from section 4. A span missing any of them is broken.
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

#: The "if applicable" attributes from section 4, emitted only when set.
OPTIONAL: Final[tuple[str, ...]] = (
    "gen_ai.request.temperature",
    "gen_ai.request.max_tokens",
    "error.type",
)

#: Every `gen_ai.*` name this project may emit. An allowlist, as in the guard: an
#: invented `gen_ai.*` name breaks third-party tooling, and a denylist cannot forbid
#: names nobody has thought of yet.
ALLOWED_GEN_AI: Final[frozenset[str]] = frozenset(
    name for name in (*MANDATORY, *OPTIONAL) if name.startswith(GEN_AI)
)


def to_attributes(call: LlmCall) -> dict[str, Any]:
    """Attributes of a model-invocation span, in the pinned version.

    Project attributes go under `app.*` and standard ones under `gen_ai.*`; this is
    the single place that decides it.
    """
    attributes: dict[str, Any] = {
        "gen_ai.operation.name": call.operation,
        # `provider.name`, not `system`: renamed in v1.37.0, though the old name is
        # still common in examples.
        "gen_ai.provider.name": call.provider,
        "gen_ai.request.model": call.requested_model,
        "gen_ai.response.model": call.responded_model,
        # `input`/`output`, not `prompt`/`completion`: renamed in v1.27.0.
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
    """Missing mandatory attributes. Only an empty result is acceptable."""
    return tuple(name for name in MANDATORY if name not in attributes)


def gen_ai_inventados(attributes: dict[str, Any]) -> tuple[str, ...]:
    """`gen_ai.*` names not defined by the standard. Must be empty.

    Half of `G-OTEL-ATTRS`. Checked on emitted attributes rather than by reading code.
    """
    return tuple(
        sorted(
            name
            for name in attributes
            if name.startswith(GEN_AI) and name not in ALLOWED_GEN_AI
        )
    )


class Dropped:
    """Count of dropped spans: the `app.spans.dropped` counter from section 4.

    A measured drop beats a silent one. "0 % trace loss" and "a full queue drops
    without blocking" are in tension, resolved by exposing the counter.
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
    """Send the attributes to `sink`. Never raises; returns whether it was delivered.

    Per section 6, if the trace store does not respond the application keeps serving
    and counts the dropped span. The `except` is a bare `Exception` because failures
    may come from a socket, a serializer or a full queue, and the response is the same.

    Translation is inside the `try` so a malformed `LlmCall` cannot break the caller.
    """
    try:
        sink(to_attributes(call))
    except Exception:
        dropped.drop()
        return False
    return True
