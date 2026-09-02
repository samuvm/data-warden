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
    prompt_id: str = "nl2sql"
    prompt_version: str = "1"

    def cache_key(self) -> str:
        """`sha256(prompt_id + version + entrada)`. La clave de la caché grabada.

        Incluye el `prompt_id` y su versión a propósito: **cambiar el prompt cambia
        la clave**, así que una caché grabada con el prompt viejo no puede pasar por
        una medida del nuevo. Es el mismo error que `schema_version` evita en la
        cadena de auditoría, y se cierra igual.
        """
        payload = json.dumps(
            {
                "prompt_id": self.prompt_id,
                "prompt_version": self.prompt_version,
                "question": self.question,
                "attempt": self.attempt,
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

    def record(self, request: Request, sql: str, *, model: str) -> pathlib.Path:
        """Graba una respuesta. Con el modelo que la produjo dentro.

        El modelo va en la grabación y no solo en el informe: una casete sin decir
        de qué modelo salió no se puede auditar, y mezclar casetes de dos modelos
        produciría un número que no es de ninguno.
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
                    "model": model,
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
    timeout_s: float = 120.0

    def generate(self, request: Request) -> str:
        import urllib.request

        from datawarden.nl2sql.prompt import render

        body = json.dumps(
            {"model": self.model, "prompt": render(request), "stream": False}
        ).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310  — endpoint fijo y local
            self.endpoint, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
        return str(payload.get("response", ""))
