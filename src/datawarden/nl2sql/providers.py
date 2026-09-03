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
from dataclasses import dataclass, field
from typing import Any, Final, Protocol, runtime_checkable

from datawarden.domain.types import RejectionReason

#: Dónde vive la caché grabada. Se versiona: es lo que hace que la evaluación se pueda
#: repetir en otra máquina sin modelo y dé el mismo número.
CASSETTE_DIR: Final = pathlib.Path("evals/cassettes")

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
    #: **600 s y no 120.** Medido el 2026-09-03: `qwen3.5:9b-mlx` en modo razonador
    #: tarda ~70 s por llamada, y una punta por encima del tope se convertiría en un
    #: rechazo `INTERNAL` que la evaluación contaría como «no se recuperó». Estaría
    #: midiendo el reloj y publicándolo como si fuera el modelo.
    timeout_s: float = 600.0
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

    def generate(self, request: Request) -> str:
        import urllib.request

        from datawarden.nl2sql.prompt import render

        payload_out: dict[str, Any] = {
            "model": self.model,
            "prompt": render(request),
            "stream": False,
            "options": {"temperature": self.temperature, "seed": self.seed},
        }
        if self.think is not None:
            payload_out["think"] = self.think
        body = json.dumps(payload_out).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310  — endpoint fijo y local
            self.endpoint, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
        return extract_sql(str(payload.get("response", "")))
