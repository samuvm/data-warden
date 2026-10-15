"""SQL providers: one port, three implementations, none of which decides anything.

- `LocalProvider` calls Ollama on the host. Models are pinned in `models.lock` by
  digest, not tag, because Ollama tags move and `G-RECOVERY` would change with no
  code change.
- `RecordedProvider` is a VCR-style cache keyed by
  `sha256(prompt_id + version + input)`. It makes `make eval-recovery`
  deterministic and free on any machine; `make eval-refresh` re-records.
- `ScriptedProvider` returns canned answers in order, for testing the loop.

No provider validates anything. A provider produces text; the guard decides whether
it runs, so a more capable model cannot gain permissions.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final, Protocol, runtime_checkable

from datawarden.domain.types import RejectionReason
from datawarden.telemetry import LlmCall, emit
from datawarden.telemetry.otel import Dropped

#: Recorded cache location. Versioned so evals reproduce on another machine without
#: a model.
CASSETTE_DIR: Final = pathlib.Path("evals/cassettes")


def cassette_dir_for(model_tag: str) -> pathlib.Path:
    """Cassette directory for one model; one subdirectory per model.

    When the generator moved from `qwen3.5:9b-mlx` to `gemma4:26b-mlx`, old and new
    recordings shared a directory and `cassette_provenance` flagged the mix: a
    number from two models belongs to neither. Deleting the old ones would break
    reproducibility of earlier reports (`models.lock` keeps the previous digest for
    that reason). Per-model directories make mixing impossible.
    """
    return CASSETTE_DIR / model_tag.replace(":", "-").replace("/", "-")


#: Wrappers models put around SQL even when asked not to.
_FENCES: Final = ("```sql", "```duckdb", "```")


def extract_sql(raw: str) -> str:
    """Extract the SQL from the model's answer without guessing.

    Instruction-tuned models sometimes wrap the query in a code fence or a polite
    preamble. Passed through as-is, R001 would reject it as unparseable and
    `G-RECOVERY` would measure format compliance instead of self-correction.

    It never repairs the SQL, adds a `LIMIT` or drops a column: the guard decides,
    and a helpful cleaner would inflate the metric.
    """
    text = raw.strip()
    # `<think>...</think>` from reasoning models is not the answer.
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1].strip()
    for fence in _FENCES:
        if fence in text:
            after = text.split(fence, 1)[1]
            text = after.split("```", 1)[0].strip()
            break
    # A trailing semicolon makes one statement two for R001.
    return text.rstrip().rstrip(";").strip()


@dataclass(frozen=True, slots=True)
class Request:
    """A generation request, including the previous rejection if any.

    Without the rejection a retry is blind, and `G-RECOVERY` would measure a retry
    rate instead of a correction rate.
    """

    question: str
    attempt: int = 1
    rejection: RejectionReason | None = None
    previous_sql: str = ""
    prompt_id: str = "nl2sql"
    prompt_version: str = "1"

    def cache_key(self) -> str:
        """`sha256(prompt_id + version + input)`: the recorded-cache key.

        Includes the prompt id and version so that changing the prompt changes the
        key, and an old recording cannot pass for a measurement of the new prompt.

        Includes `previous_sql` because two attempts correcting different queries
        with the same rejection are different requests; sharing a key would replay
        the first answer for the second.
        """
        payload = json.dumps(
            {
                "prompt_id": self.prompt_id,
                "prompt_version": self.prompt_version,
                "question": self.question,
                "attempt": self.attempt,
                "previous_sql": self.previous_sql,
                "rejection": None
                if self.rejection is None
                else {
                    "rule_id": self.rejection.rule_id,
                    "code": self.rejection.code,
                    "message": self.rejection.message,
                    "suggestion": self.rejection.suggestion,
                },
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@runtime_checkable
class Provider(Protocol):
    """Produces SQL. Does not validate it, execute it, or know about policy."""

    name: str

    def generate(self, request: Request) -> str: ...


@dataclass
class ScriptedProvider:
    """Returns canned answers in order, for loop tests.

    Records the requests it received so tests can check what was passed to the next
    attempt.
    """

    respuestas: list[str]
    name: str = "scripted"
    recibido: list[Request] = field(default_factory=list)

    def generate(self, request: Request) -> str:
        self.recibido.append(request)
        if not self.respuestas:
            return ""
        return self.respuestas.pop(0)


@dataclass
class RecordedProvider:
    """Recorded cache that makes `make eval-recovery` deterministic and free.

    A cache miss is an error, not a fallback to `LocalProvider`; silently falling
    back would mix recorded and live answers into an irreproducible number.
    Re-recording is explicit via `make eval-refresh`.
    """

    directory: pathlib.Path = CASSETTE_DIR
    name: str = "recorded"

    def generate(self, request: Request) -> str:
        path = self.directory / f"{request.cache_key()}.json"
        if not path.exists():
            message = (
                f"no recording for this request ({path.name}). "
                "`make eval-recovery` never calls the model. If the corpus or the "
                "prompt changed, re-record with `make eval-refresh`, which requires "
                "the local model (Ollama)."
            )
            raise KeyError(message)
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return str(payload["sql"])

    def record(
        self, request: Request, sql: str, *, model: str, thinking: bool | None = None
    ) -> pathlib.Path:
        """Record an answer with everything needed to audit it later.

        Model and thinking mode are stored in the cassette itself, because replay
        reports provenance from the cassettes rather than from invocation flags (which
        once reported a reasoning mode the recordings did not use).
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{request.cache_key()}.json"
        path.write_text(
            json.dumps(
                {
                    "prompt_id": request.prompt_id,
                    "prompt_version": request.prompt_version,
                    "question": request.question,
                    "attempt": request.attempt,
                    "rejection_code": None
                    if request.rejection is None
                    else request.rejection.code,
                    "previous_sql": request.previous_sql,
                    "model": model,
                    "thinking": thinking,
                    "sql": sql,
                },
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return path


@dataclass
class LocalProvider:
    """Ollama on the host, never in compose.

    Docker on macOS does not pass through the GPU, so containerized Ollama ruins
    latency. The model is pinned by digest from `models.lock`, not by tag.
    """

    model: str
    name: str = "local"
    endpoint: str = "http://localhost:11434/api/generate"
    #: `qwen3.5:9b-mlx` in reasoning mode takes ~70 s per call, and `gemma4:26b-mlx`
    #: is twice the size (`REC-R002-3` timed out at 600 s). A timeout becomes an
    #: `INTERNAL` rejection that evals would count as a failed recovery, measuring
    #: the clock instead of the model. Latency is still visible in the report.
    timeout_s: float = 1200.0
    #: Reasoning mode. `None` keeps the model default; `False` disables it. Reported
    #: because it changes results: the same query took 0.3 s without reasoning and
    #: 71 s with it.
    think: bool | None = None
    #: Zero temperature and a fixed seed. With default temperature, two consecutive
    #: `make eval-refresh` runs with the same configuration gave 0.8214 and 0.9286.
    #: Cassettes make replay deterministic, but recording must be too. The seed is
    #: reported.
    temperature: float = 0.0
    seed: int = 20260903
    #: Output token cap. Without `num_predict`, `gemma4:26b-mlx` on `REC-R002-3`
    #: (rewriting a `LIKE ... ESCAPE` that R002 rejects) kept generating and never
    #: returned, even with a 1200 s timeout. With `num_predict=1500` it returns in
    #: 29 s with `done_reason: "length"`, and the truncated answer is rejected by the
    #: guard: a genuine failed recovery instead of a discarded measurement.
    max_tokens: int = 1500
    #: Sink for per-call OTel attributes. `None` (the default) discards them;
    #: generation must not depend on telemetry.
    telemetry: Callable[[dict[str, Any]], None] | None = None
    #: `app.spans.dropped` from `docs/CONTRACTS/otel-genai.md` section 4. Per instance,
    #: not global, so two providers do not share the counter.
    dropped: Dropped = field(default_factory=Dropped)

    def generate(self, request: Request) -> str:
        import urllib.request

        from datawarden.nl2sql.prompt import render

        payload_out: dict[str, Any] = {
            "model": self.model,
            "prompt": render(request),
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "seed": self.seed,
                "num_predict": self.max_tokens,
            },
        }
        if self.think is not None:
            payload_out["think"] = self.think
        body = json.dumps(payload_out).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310  (fixed local endpoint)
            self.endpoint, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
        # Emitted after reading, not wrapping the call: per section 6 of the OTel
        # contract an observability failure never breaks the application.
        if self.telemetry is not None:
            emit(self.llm_call(payload), self.telemetry, dropped=self.dropped)
        return extract_sql(str(payload.get("response", "")))

    def llm_call(self, payload: dict[str, Any]) -> LlmCall:
        """Map an Ollama response to the internal model, with no OTel names.

        Ollama field names (`prompt_eval_count`, `eval_count`, `done_reason`) map to
        internal ones here; only `telemetry/otel.py` knows `gen_ai.*`. Two separate
        translations keep spec changes and Ollama changes from leaking into each other.

        `gen_ai.response.model` comes from `payload["model"]`, not `self.model`: the
        contract distinguishes the model that actually answered.
        """
        from urllib.parse import urlparse

        servidor = urlparse(self.endpoint)
        return LlmCall(
            operation="chat",
            provider="ollama",
            requested_model=self.model,
            responded_model=str(payload.get("model") or self.model),
            input_tokens=int(payload.get("prompt_eval_count") or 0),
            output_tokens=int(payload.get("eval_count") or 0),
            finish_reasons=(str(payload.get("done_reason") or "stop"),),
            server_address=servidor.netloc or servidor.path,
            temperature=self.temperature,
            # `gen_ai.request.max_tokens` reflects the parameter actually sent.
            max_tokens=self.max_tokens,
            # Ollama reports nanoseconds; `app.ttft_ms` is in milliseconds.
            ttft_ms=_ns_a_ms(payload.get("prompt_eval_duration")),
        )


def _ns_a_ms(nanosegundos: object) -> float | None:
    """Ollama nanoseconds to milliseconds. `None` if absent, which is not the same as 0."""
    if not isinstance(nanosegundos, int):
        return None
    return round(nanosegundos / 1_000_000, 3)
