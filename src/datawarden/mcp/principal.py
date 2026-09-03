"""De dónde sale el rol bajo un protocolo SIN SESIONES. **`G-ROLE-SPOOF`, axioma.**

La spec MCP 2026-07-28 eliminó las sesiones (`Mcp-Session-Id`), el handshake
`initialize` y `ping`. Eso tiene una consecuencia directa que este fichero existe
para no dejar en prosa:

> **El rol de quien pregunta ya no puede venir de la sesión de protocolo, porque no
> hay ninguna. Y todo lo que el cliente diga sobre sí mismo es DATO TRANSPORTADO.**

`_meta` es dato. `arguments` es dato. Una cabecera HTTP que el cliente elige es dato.
Si cualquiera de los tres pudiera fijar el rol, el anillo 4 —el enmascarado— sería
ficción: bastaría un campo en el JSON para autoconcederse `admin` y leer nombres,
correos y DNI. `RoleSource` ya impide escribirlo —no tiene ningún valor que
signifique «lo dijo el cliente»—; esto es la otra mitad.

**El argumento del proyecto sale REFORZADO, no debilitado.** «Da igual qué cliente se
conecte, no puede saltarse las reglas» es *más* cierto sin sesiones, porque el
servidor es aún más claramente el único guardián. Cambia el mecanismo, no la tesis.

**Las tres procedencias admitidas, y ninguna la elige el cliente:**

- `SERVER_PROCESS` — el rol lo fija quien arranca el proceso. Es el caso de stdio:
  un cliente de escritorio lanza el servidor y hereda el rol de la configuración del
  usuario, igual que un `psql` hereda el usuario del sistema.
- `PRINCIPAL_TOKEN` — un token verificado por el servidor. Bajo Streamable HTTP es
  el único camino honesto: la identidad la acuña y la comprueba el servidor, y el
  SDK la expone por `authenticated_principal`, que sale del verificador de tokens y
  no del cuerpo de la petición.
- `CLI_FLAG` — una bandera de la línea de órdenes. Es de desarrollo y va etiquetada
  como tal en cada registro de auditoría.
"""

from __future__ import annotations

import os
from typing import Any, Final

from datawarden.domain.types import Principal, Role, RoleSource

#: Las claves que un cliente podría intentar usar para colarse un rol. **No es una
#: lista negra que decida nada**: el rol no se lee NUNCA de los datos de la petición,
#: así que esta lista no protege — sirve para DETECTAR el intento y dejarlo en la
#: auditoría, que es lo que convierte un ataque en una señal.
SPOOF_KEYS: Final = frozenset(
    {"role", "rol", "roles", "principal", "principal_id", "scope", "scopes", "as_role"}
)

#: La variable de entorno con la que se arranca el servidor en modo stdio.
ROLE_ENV: Final = "WARDEN_ROLE"


def from_server_process(default: Role = Role.ANALYST) -> Principal:
    """El rol del PROCESO. Lo fija quien arranca el servidor, nunca quien pregunta.

    Un valor desconocido en la variable no escala a `admin` ni revienta: cae al rol
    por defecto, que es el más restringido de los cuatro. Fail-closed también aquí.
    """
    raw = os.environ.get(ROLE_ENV, "").strip().lower()
    try:
        role = Role(raw) if raw else default
    except ValueError:
        role = default
    return Principal(
        id=f"stdio:{os.environ.get('USER', 'desconocido')}",
        role=role,
        source=RoleSource.SERVER_PROCESS,
    )


def from_verified_token(subject: str, role: Role) -> Principal:
    """Una identidad que el SERVIDOR verificó. No lo que la petición afirmaba ser."""
    return Principal(id=subject, role=role, source=RoleSource.PRINCIPAL_TOKEN)


def spoof_attempts(*payloads: Any) -> tuple[str, ...]:
    """Las claves sospechosas que traía la petición. **Para auditar, no para decidir.**

    Devolver esto y registrarlo es lo que convierte «el rol no se lee de aquí» en una
    afirmación comprobable: si un cliente manda `_meta.role="admin"`, el registro de
    auditoría dice que lo intentó Y dice con qué rol se le respondió de verdad. Sin
    esto, un intento de suplantación y una petición normal serían indistinguibles a
    posteriori.
    """
    found: list[str] = []
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key in payload:
            if str(key).lower() in SPOOF_KEYS:
                found.append(str(key))
    return tuple(sorted(set(found)))
