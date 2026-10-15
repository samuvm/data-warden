"""Where the role comes from (`G-ROLE-SPOOF`).

Lives in `service/`, not `mcp/`, because it is not a protocol concern: an HTTP header
is caller-chosen exactly like MCP `_meta`, so both transports need the same answer.

MCP spec 2026-07-28 removed sessions (`Mcp-Session-Id`), the `initialize` handshake
and `ping`, so the caller's role cannot come from a protocol session, and everything
the client says about itself is transported data. `_meta`, `arguments` and
caller-chosen HTTP headers are all data. If any of them could set the role, ring 4
(masking) would be moot: one JSON field would grant `admin` and expose names, emails
and national IDs. `RoleSource` has no value meaning "the client said so"; this module
is the other half.

Accepted sources, none chosen by the client:

- `SERVER_PROCESS`: set by whoever starts the process. The stdio case: a desktop
  client spawns the server, which inherits the role from the user's configuration,
  as `psql` inherits the OS user.
- `PRINCIPAL_TOKEN`: a server-verified token. Under Streamable HTTP the SDK exposes
  it via `authenticated_principal`, from the token verifier, not the request body.
- `CLI_FLAG`: a command-line flag for development, labelled as such in every audit
  record.
"""

from __future__ import annotations

import os
from typing import Any, Final

from datawarden.domain.types import Principal, Role, RoleSource

#: Keys a client might use to smuggle a role. Not a denylist that decides anything:
#: the role is never read from request data. The list only detects the attempt so it
#: is recorded in the audit trail.
SPOOF_KEYS: Final = frozenset(
    {"role", "rol", "roles", "principal", "principal_id", "scope", "scopes", "as_role"}
)

#: Environment variable that sets the role for the stdio server.
ROLE_ENV: Final = "WARDEN_ROLE"


def from_server_process(default: Role = Role.ANALYST) -> Principal:
    """The process role, set by whoever starts the server, never by the caller.

    An unknown value neither escalates nor raises: it falls back to the default,
    the most restricted of the four roles.
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
    """An identity the server verified, not what the request claimed."""
    return Principal(id=subject, role=role, source=RoleSource.PRINCIPAL_TOKEN)


def spoof_attempts(*payloads: Any) -> tuple[str, ...]:
    """Suspicious keys present in the request; for auditing, not for deciding.

    If a client sends `_meta.role="admin"`, the audit record shows the attempt and
    the role actually used, so spoofing attempts are distinguishable after the fact.
    """
    found: list[str] = []
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key in payload:
            if str(key).lower() in SPOOF_KEYS:
                found.append(str(key))
    return tuple(sorted(set(found)))
