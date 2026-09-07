"""El `traceparent` que llega en `_meta`. **Dato transportado, nunca autoridad.**

La spec MCP 2026-07-28 propaga el contexto de traza de OTel en `_meta`, y eso encadena
la traza de este servidor con la de quien llama. `docs/spec/audit-record.schema.json` lo
dice en la `description` del campo con todas las letras: *«es DATO transportado y jamás
autoridad: encadena trazas, no concede permisos»*.

**Por qué se valida en vez de copiarse tal cual, que sería más corto.** Porque lo
escribe el cliente y acaba **dentro del registro de auditoría**, que es el artefacto que
este proyecto usa para certificar lo que pasó. Ya hubo un caso esta semana de texto del
cliente colándose donde no debía —el identificador que fabricaba secciones del prompt—,
y la lección se aplica igual aquí: **lo que viene de fuera se acota antes de guardarse.**

Un `traceparent` mal formado no es un error: es un `None`. Encadenar trazas es una
comodidad de observabilidad, y tumbar una consulta legítima porque el cliente mandó una
cabecera rara sería cambiar una defensa por una molestia.
"""

from __future__ import annotations

import re
from typing import Any, Final

#: W3C Trace Context: `00-<32 hex trace-id>-<16 hex span-id>-<2 hex flags>`.
#: https://www.w3.org/TR/trace-context/
_TRACEPARENT: Final = re.compile(
    r"^(?P<version>[0-9a-f]{2})-(?P<trace>[0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$"
)

#: El `trace-id` de 32 ceros está prohibido por la propia especificación: significa
#: «inválido». Aceptarlo llenaría la auditoría de trazas que no encadenan con nada.
_INVALID_TRACE: Final = "0" * 32

#: Tope antes de mirar siquiera la forma. Un `_meta` con un megabyte dentro no llega a
#: la expresión regular: se descarta por tamaño.
MAX_TRACEPARENT: Final = 128


def trace_id_from(meta: Any) -> str | None:
    """El `trace-id` de un `traceparent` bien formado, o `None`.

    Se devuelve el `trace-id` y no el `traceparent` entero a propósito: el `span-id`
    identifica el tramo DEL CLIENTE, y guardarlo daría a entender que este servidor es
    ese tramo. Lo que encadena es la traza.
    """
    if not isinstance(meta, dict):
        return None
    raw = meta.get("traceparent")
    if not isinstance(raw, str) or len(raw) > MAX_TRACEPARENT:
        return None
    found = _TRACEPARENT.match(raw.strip().lower())
    if found is None or found.group("trace") == _INVALID_TRACE:
        return None
    if found.group("version") == "ff":
        # `ff` está reservada como versión inválida por la especificación.
        return None
    return found.group("trace")
