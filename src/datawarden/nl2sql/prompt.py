"""Carga y compone los prompts. **El texto vive en `prompts/*.md`, nunca aquí.**

`CLAUDE.md` lo prohíbe explícitamente —«escribir prompts inline en `.py`»— y no es una
manía de orden. Un prompt es el parámetro más sensible de todo el sistema: cambia el
número de `G-RECOVERY` sin cambiar una línea de lógica. Metido en un `.py` se edita sin
pensarlo y se despliega sin registrarlo; en un fichero propio con `id` y `version` en
el frontmatter, **cambiarlo cambia también la clave de la caché grabada**, así que una
medida vieja no puede pasar por una medida del prompt nuevo.

Esa es la garantía que sostiene la evaluación: el informe publica `{prompt_id, version,
sha256}` y el sha se calcula sobre el fichero. Dos números medidos con prompts
distintos no se pueden comparar, y aquí no se pueden confundir.
"""

from __future__ import annotations

import functools
import hashlib
import pathlib
from dataclasses import dataclass
from typing import Final

from datawarden.nl2sql.providers import Request

PROMPTS_DIR: Final = pathlib.Path(__file__).resolve().parents[3] / "prompts"

#: Cuántas relaciones del catálogo entran en el prompt. Todas serían 32 tablas y 428
#: columnas: un prompt de decenas de miles de caracteres que ni cabe cómodo ni ayuda.
#: El catálogo completo se sirve como recurso MCP en la fase 7; aquí va el resumen.
MAX_CATALOG_TABLES: Final = 32


@dataclass(frozen=True, slots=True)
class Prompt:
    """Una plantilla con su procedencia. El sha es del FICHERO, no del texto compuesto."""

    prompt_id: str
    version: str
    target_model: str
    body: str
    sha256: str


@functools.lru_cache(maxsize=8)
def load(name: str) -> Prompt:
    """Lee `prompts/<name>.md` con su frontmatter. Cacheado: no cambia en caliente."""
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        message = (
            f"no existe {path}. Los prompts viven en `prompts/*.md` con frontmatter "
            "`id`/`version`/`modelo_destino`, nunca inline en un `.py`: un prompt "
            "cambia el número de G-RECOVERY sin cambiar una línea de lógica."
        )
        raise FileNotFoundError(message)

    raw = path.read_text(encoding="utf-8")
    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    meta, _, body = _split_frontmatter(raw)
    return Prompt(
        prompt_id=meta.get("id", name),
        version=str(meta.get("version", "0")),
        target_model=meta.get("modelo_destino", "desconocido"),
        body=body.strip(),
        sha256=sha,
    )


def _split_frontmatter(raw: str) -> tuple[dict[str, str], str, str]:
    """Frontmatter YAML mínimo, leído a mano y sin dependencia.

    Son tres claves de una línea cada una: meter un parser de YAML para esto añadiría
    una dependencia en el camino de arranque a cambio de nada.
    """
    if not raw.startswith("---"):
        return {}, "", raw
    end = raw.find("\n---", 3)
    if end == -1:
        return {}, "", raw
    head = raw[3:end]
    meta: dict[str, str] = {}
    for line in head.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            meta[key.strip()] = value.strip().strip('"')
    return meta, head, raw[end + 4 :]


def render(request: Request, *, catalog: str = "") -> str:
    """Compone el prompt del intento. Con el rechazo anterior si lo hubo.

    **Sin el rechazo dentro no hay ciclo de corrección**, hay un bucle que reintenta a
    ciegas, y `G-RECOVERY` estaría midiendo una tasa de reintento en vez de una de
    corrección. Por eso el bloque de reintento es una plantilla propia y versionada:
    es tan parte de la medida como la primera.
    """
    base = load(request.prompt_id if request.prompt_id != "nl2sql" else "nl2sql")
    feedback = ""
    if request.rejection is not None:
        retry = load("nl2sql-retry")
        feedback = (
            retry.body.replace("{previous_sql}", "")
            .replace("{rule_id}", request.rejection.rule_id)
            .replace("{code}", request.rejection.code)
            .replace("{message}", request.rejection.message)
            .replace("{suggestion}", request.rejection.suggestion)
        )
    return (
        base.body.replace("{catalog}", catalog or _catalog_summary())
        .replace("{question}", request.question)
        .replace("{feedback}", feedback)
    )


@functools.lru_cache(maxsize=1)
def _catalog_summary() -> str:
    """El catálogo GENERADO, resumido. Nunca uno escrito a mano.

    Es el mismo artefacto contra el que se valida (`catalog/generated/schema.json`), y
    que sean el mismo no es economía: es lo que garantiza que el modelo razone sobre
    exactamente el esquema contra el que se le va a juzgar.
    """
    from datawarden.catalog import SCHEMA_PATH, load_generated

    schema = load_generated(SCHEMA_PATH).published()
    lines: list[str] = []
    for table in schema.tables[:MAX_CATALOG_TABLES]:
        columns = ", ".join(c.name for c in table.columns)
        lines.append(f"- **{table.name}** ({table.kind}): {columns}")
    return "\n".join(lines)
