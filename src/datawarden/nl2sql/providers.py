"""De dónde sale el SQL. Tres proveedores y un puerto, y ninguno decide nada.

**Por qué hay tres y no uno.** Cada uno resuelve un problema distinto y confundirlos
es lo que hace que una evaluación deje de ser reproducible:

- **`LocalProvider`** habla con Ollama en el host. Es el que genera de verdad, y el
  único que cuesta tiempo y electricidad. Los tags salen de `models.lock`, fijados
  **por digest y no por tag**, porque un tag de Ollama es móvil: `qwen3.5:9b-mlx`
  puede apuntar a otro peso dentro de tres meses y `G-RECOVERY` cambiaría de valor sin
  que nadie tocara una línea.
- **`RecordedProvider`** es una caché estilo VCR indexada por
  `sha256(prompt_id + version + entrada)`. Es lo que hace `make eval-recovery`
  **determinista y gratis**: la evaluación se repite en cualquier máquina, sin modelo
  y sin variar. `make eval-refresh` es lo que vuelve a llamar al modelo.
- **`ScriptedProvider`** devuelve lo que se le dijo, en orden. Existe para los tests
  del BUCLE, que es código y se prueba; el generador se mide.

**Ninguno valida nada.** Un provider produce texto; quién decide si ese texto se
ejecuta es el guard, y esa separación es la que impide que un modelo más listo se gane
permisos que no tiene.
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

#: Dónde vive la caché grabada. Se versiona: es lo que hace que la evaluación se pueda
#: repetir en otra máquina sin modelo y dé el mismo número.
CASSETTE_DIR: Final = pathlib.Path("evals/cassettes")


def cassette_dir_for(model_tag: str) -> pathlib.Path:
    """La carpeta de casetes DE UN MODELO. Un subdirectorio por modelo, no un cajón.

    **Nace de un fallo real del 2026-09-10.** Al pasar el generador de `qwen3.5:9b-mlx`
    a `gemma4:26b-mlx`, las 231 grabaciones del 9B se quedaron en la misma carpeta que
    las 127 nuevas, y `cassette_provenance` lo cazó: «las casetes vienen de dos modelos;
    mezclar dos modelos da un número que no es de ninguno de los dos». Tenía razón.

    Se podría haber resuelto borrando las viejas, y sería peor por dos motivos. Uno:
    `models.lock` conserva el digest del generador anterior justo para que sus informes
    sigan siendo reproducibles, y sin sus casetes esa promesa es falsa. Dos: el problema
    volvería el día del siguiente cambio de modelo, porque dependería de que alguien se
    acordara de limpiar. Separados por carpeta, **mezclarlos deja de ser posible**, que
    es la única forma de arreglo que no hay que recordar.
    """
    return CASSETTE_DIR / model_tag.replace(":", "-").replace("/", "-")


#: Los envoltorios que un modelo pone alrededor del SQL aunque se le pida que no.
_FENCES: Final = ("```sql", "```duckdb", "```")


def extract_sql(raw: str) -> str:
    """El SQL que hay dentro de lo que contestó el modelo. Sin adivinar nada.

    **Esto NO es tolerancia con el modelo: es no medir el envoltorio.** El prompt
    pide una consulta y nada más, y aun así un modelo instruido devuelve a veces un
    bloque de código o una línea de cortesía delante. Si eso llegara tal cual al
    guard, R001 lo rechazaría por no parsear y `G-RECOVERY` estaría midiendo cuántas
    veces el modelo obedece el formato en vez de cuántas veces se corrige, que son
    dos cosas distintas y solo una es la tesis del proyecto.

    Lo que NO hace, y es lo importante: no arregla el SQL, no le añade un `LIMIT`,
    no le quita una columna. Quien decide si esa consulta se ejecuta es el guard, y
    un limpiador que «ayudara» al modelo estaría regalando puntos a la métrica.
    """
    text = raw.strip()
    # `<think>...</think>` de los modelos con razonamiento: no es la respuesta.
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1].strip()
    for fence in _FENCES:
        if fence in text:
            after = text.split(fence, 1)[1]
            text = after.split("```", 1)[0].strip()
            break
    # Un punto y coma final convierte una sentencia en dos para R001.
    return text.rstrip().rstrip(";").strip()


@dataclass(frozen=True, slots=True)
class Request:
    """Lo que se le pide al modelo, con el rechazo anterior si lo hubo.

    Es un tipo y no tres argumentos sueltos porque **el rechazo es la mitad del
    ciclo**: sin él, reintentar es reintentar a ciegas, y `G-RECOVERY` mediría una
    tasa de reintento en vez de una de corrección.
    """

    question: str
    attempt: int = 1
    rejection: RejectionReason | None = None
    previous_sql: str = ""
    prompt_id: str = "nl2sql"
    prompt_version: str = "1"

    def cache_key(self) -> str:
        """`sha256(prompt_id + version + entrada)`. La clave de la caché grabada.

        Incluye el `prompt_id` y su versión a propósito: **cambiar el prompt cambia
        la clave**, así que una caché grabada con el prompt viejo no puede pasar por
        una medida del nuevo. Es el mismo error que `schema_version` evita en la
        cadena de auditoría, y se cierra igual.

        E incluye `previous_sql` por lo mismo. Dos intentos que corrigen consultas
        distintas con el mismo rechazo son peticiones DISTINTAS: el modelo ve un
        texto distinto y contesta otra cosa. Si compartieran clave, la casete del
        primero pasaría por respuesta del segundo y la medida sería de una petición
        que nunca se hizo.
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
    """Produce SQL. No lo valida, no lo ejecuta y no sabe qué es una política."""

    name: str

    def generate(self, request: Request) -> str: ...


@dataclass
class ScriptedProvider:
    """Devuelve lo que se le dijo, en orden. Para los tests del BUCLE.

    Guarda lo que recibió, porque la mitad de lo que hay que comprobar del bucle es
    **qué se le pasó al intento siguiente**.
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
    """La caché grabada. `make eval-recovery` determinista y gratis.

    **Un fallo de caché es un error, no una llamada al modelo.** Si al no encontrar la
    entrada se cayera al `LocalProvider`, `make eval-recovery` dejaría de ser gratis y
    determinista sin avisar, y el número saldría de una mezcla de grabado y generado
    que nadie podría reproducir. Para regrabar está `make eval-refresh`, que es
    explícito.
    """

    directory: pathlib.Path = CASSETTE_DIR
    name: str = "recorded"

    def generate(self, request: Request) -> str:
        path = self.directory / f"{request.cache_key()}.json"
        if not path.exists():
            message = (
                f"no hay grabación para esta petición ({path.name}). "
                "`make eval-recovery` NO llama al modelo: es determinista y gratis a "
                "propósito. Si el corpus o el prompt han cambiado, regraba con "
                "`make eval-refresh`, que sí exige el modelo local de Q-007."
            )
            raise KeyError(message)
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return str(payload["sql"])

    def record(
        self, request: Request, sql: str, *, model: str, thinking: bool | None = None
    ) -> pathlib.Path:
        """Graba una respuesta. **Con TODO lo que hace falta para volver a leerla.**

        El modelo y el modo van en la grabación y no solo en el informe del día que
        se grabó: una casete sin decir de dónde salió no se puede auditar, y mezclar
        casetes de dos modelos —o del mismo modelo en dos modos— produciría un número
        que no es de ninguno de los dos.

        Y no es teórico: la reproducción publicaba el modo que traía el flag de la
        invocación, no el que había producido las casetes. Decía `razonador=sí` sobre
        un número medido sin razonador. Se arregló haciendo que la procedencia SALGA
        DE LAS CASETES, que es el único sitio donde es cierta.
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
    """Ollama en el HOST, nunca en compose.

    Q-007 lo dice y es una decisión medida: Docker en macOS no pasa la GPU, y meter
    Ollama dentro destruye la latencia. El modelo se fija por digest desde
    `models.lock`, no por tag.
    """

    model: str
    name: str = "local"
    endpoint: str = "http://localhost:11434/api/generate"
    #: **1200 s, y el mismo argumento que llevó de 120 a 600.** Medido el 2026-09-03:
    #: `qwen3.5:9b-mlx` en modo razonador tarda ~70 s por llamada, y una punta por
    #: encima del tope se convierte en un rechazo `INTERNAL` que la evaluación contaría
    #: como «no se recuperó». Estaría midiendo el reloj y publicándolo como si fuera el
    #: modelo.
    #:
    #: Se sube el 2026-09-10 al pasar el generador a `gemma4:26b-mlx`, que es el doble
    #: de grande: con 600 s, `REC-R002-3` murió con `TimeoutError` y la medida lo tuvo
    #: que descartar como fallo de MEDIDA. Subirlo no ablanda nada —un modelo lento
    #: sigue siendo lento y se ve en el informe—; lo que evita es publicar un cero que
    #: es del reloj y no del modelo.
    timeout_s: float = 1200.0
    #: Modo razonador. `None` deja el del modelo; `False` lo apaga. Va al informe
    #: porque cambia el número: la misma consulta salía en 0,3 s sin razonar y en
    #: 71 s razonando, y dos medidas con modos distintos no son comparables.
    think: bool | None = None
    #: **Temperatura CERO y semilla fija, y no es un detalle.** Con la temperatura
    #: por defecto, dos `make eval-refresh` seguidos con la MISMA configuración
    #: dieron 0,8214 y 0,9286 el 2026-09-03. Las casetes hacen determinista la
    #: reproducción, pero si la grabación no lo es, el número no se puede volver a
    #: obtener: sería un número sin comando que lo reproduzca, que es justo lo que
    #: este proyecto no admite. La semilla va al informe con todo lo demás.
    temperature: float = 0.0
    seed: int = 20260903
    #: **Tope de tokens de salida. Sin él, un prompt puede colgar la evaluación entera.**
    #:
    #: Medido el 2026-09-10 con `gemma4:26b-mlx` y el caso `REC-R002-3` —reescribir un
    #: `LIKE ... ESCAPE` que R002 rechaza—: sin `num_predict`, Ollama se queda generando
    #: y no vuelve. Reproducido tres veces con el modelo ya cargado, a 240 s, 200 s y
    #: 300 s. Con `num_predict=1500` vuelve en 29 s y `done_reason: "length"`.
    #:
    #: Subir el `timeout_s` NO lo arreglaba, y ese fue el primer intento: con 1200 s
    #: seguía muriendo. El problema no es que el modelo sea lento, es que no para.
    #:
    #: Y la diferencia importa para lo que se publica: sin tope, el caso salía como
    #: `INTERNAL` y la evaluación tenía que DESCARTARLO como fallo de medida; con tope,
    #: sale una respuesta truncada que el guard rechaza, o sea **una recuperación
    #: fallida de verdad**, que es lo que hay que contar. Un tope no ablanda la medida:
    #: convierte un agujero en un dato.
    max_tokens: int = 1500
    #: A dónde van los atributos OTel de cada llamada. `None` los tira, y es el valor
    #: por defecto a propósito: medir no puede ser obligatorio para poder generar.
    telemetry: Callable[[dict[str, Any]], None] | None = None
    #: `app.spans.dropped` de `docs/CONTRACTS/otel-genai.md §4`. Va aquí y no en un
    #: global para que dos proveedores no se pisen el contador.
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
        req = urllib.request.Request(  # noqa: S310  — endpoint fijo y local
            self.endpoint, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
        # **La emisión va DESPUÉS de leer y no envuelve la llamada**: §6 del contrato
        # OTel dice que un fallo de observabilidad nunca tumba la aplicación, y `emit`
        # ya no lanza — pero además así queda claro que generar no depende de medir.
        if self.telemetry is not None:
            emit(self.llm_call(payload), self.telemetry, dropped=self.dropped)
        return extract_sql(str(payload.get("response", "")))

    def llm_call(self, payload: dict[str, Any]) -> LlmCall:
        """La respuesta de Ollama, en el modelo INTERNO. Ni un nombre del estándar.

        Los nombres de la izquierda son de Ollama (`prompt_eval_count`, `eval_count`,
        `done_reason`) y los de la derecha son nuestros. La traducción al estándar pasa
        después, en `telemetry/otel.py`, y es la única que conoce `gen_ai.*`. Son dos
        traducciones seguidas a propósito: así una ruptura de la spec no llega hasta
        aquí, y un cambio de Ollama no llega hasta el estándar.

        `gen_ai.response.model` sale de `payload["model"]` y no de `self.model`: es el
        modelo que respondió DE VERDAD, que es justo lo que el contrato pide distinguir.
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
            # Ahora `gen_ai.request.max_tokens` lleva un valor de verdad: es un
            # parámetro que se envía, no un campo decorativo del modelo interno.
            max_tokens=self.max_tokens,
            # Ollama da la latencia en NANOsegundos; `app.ttft_ms` va en milisegundos.
            ttft_ms=_ns_a_ms(payload.get("prompt_eval_duration")),
        )


def _ns_a_ms(nanosegundos: object) -> float | None:
    """Nanosegundos de Ollama a milisegundos. `None` si no vino, que no es lo mismo que 0."""
    if not isinstance(nanosegundos, int):
        return None
    return round(nanosegundos / 1_000_000, 3)
