"""`LlmCall`: what this project knows about a model call, under its own names.

No field is named like an OpenTelemetry attribute (`requested_model` and
`responded_model` rather than `request.model` and `response.model`, `provider` rather
than `provider.name`), so the next spec rename touches the translator, not the
domain.

Standard and project-specific facts live together and are split on emit: cost,
prompt identity and time to first token are not part of the GenAI spec and go under
`app.*`, which `otel.py` enforces.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LlmCall:
    """A completed model invocation. Frozen: it is a fact, not state."""

    #: `chat` or `embeddings`; the spec's operation name.
    operation: str
    #: Who served the model: `ollama`, `aws.bedrock`, ...
    provider: str
    #: The requested model, as pinned in `models.lock`.
    requested_model: str
    #: The model that actually answered; may differ from the requested one.
    responded_model: str
    input_tokens: int
    output_tokens: int
    #: Why generation stopped. Plural per the spec: a response may carry several.
    finish_reasons: tuple[str, ...]
    #: Server called. Required even when it is `localhost`.
    server_address: str

    # --- "if applicable" in the contract: emitted only when set ---------------
    temperature: float | None = None
    max_tokens: int | None = None
    #: Only set on error; its presence is the error signal.
    error_type: str | None = None

    # --- project extensions, emitted under `app.*`, never `gen_ai.*` ---------
    cost_eur: float | None = None
    #: Pricing table version, so historical reports remain reproducible.
    pricing_version: str | None = None
    prompt_id: str | None = None
    prompt_version: int | None = None
    prompt_sha256: str | None = None
    #: Time to first token.
    ttft_ms: float | None = None
    #: Whether the client disconnected mid-stream. Those tokens are still counted.
    client_disconnected: bool | None = None
