"""`LlmCall`: lo que este proyecto sabe de una llamada a un modelo. **Nombres propios.**

Ni un campo se llama como un atributo de OpenTelemetry, y es deliberado hasta en los
detalles: `requested_model` y `responded_model` en vez de `request.model` y
`response.model`, `provider` en vez de `provider.name`. Si los nombres coincidieran, la
traducción parecería innecesaria y el día de la siguiente ruptura de la spec —que el
propio contrato documenta como algo que YA ha pasado tres veces— habría que tocar el
dominio en vez de un traductor.

**Lo propio y lo del estándar viven aquí juntos y se separan al emitir.** El coste, el
prompt y la latencia hasta el primer token no son parte de la spec GenAI; van a `app.*`.
Que estén en el mismo dataclass es correcto: son hechos de la misma llamada. Lo que no
sería correcto es publicarlos dentro de `gen_ai.*`, y de eso se encarga `otel.py`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LlmCall:
    """Una invocación de modelo, ya terminada. Congelada: es un hecho, no un estado."""

    #: `chat` o `embeddings`. Lo que la spec llama la operación.
    operation: str
    #: Quién sirvió el modelo: `ollama`, `aws.bedrock`…
    provider: str
    #: El modelo que se PIDIÓ, tal cual está en `models.lock`.
    requested_model: str
    #: El que respondió de verdad. Puede no coincidir, y ahí está la gracia de tenerlo.
    responded_model: str
    input_tokens: int
    output_tokens: int
    #: Por qué paró. La spec lo quiere en plural: una respuesta puede traer varias.
    finish_reasons: tuple[str, ...]
    #: El servidor al que se llamó. Obligatorio aunque sea `localhost`.
    server_address: str

    # --- «si aplica» en el contrato: se emiten solo cuando tienen valor -------
    temperature: float | None = None
    max_tokens: int | None = None
    #: Solo cuando hubo error. Su presencia ES la señal de error.
    error_type: str | None = None

    # --- extensiones PROPIAS. Salen bajo `app.*`, jamás bajo `gen_ai.*` ------
    cost_eur: float | None = None
    #: Sin la vigencia de la tabla de precios, un informe histórico no es reproducible.
    pricing_version: str | None = None
    prompt_id: str | None = None
    prompt_version: int | None = None
    prompt_sha256: str | None = None
    #: Latencia hasta el primer token.
    ttft_ms: float | None = None
    #: Si el cliente colgó a medias. Esos tokens ya se han pagado y se cuentan igual.
    client_disconnected: bool | None = None
