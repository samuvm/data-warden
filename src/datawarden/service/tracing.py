"""The `traceparent` carried in `_meta`: transported data, never authority.

MCP spec 2026-07-28 propagates OTel trace context in `_meta`, linking this server's
trace with the caller's (see the field description in
`docs/spec/audit-record.schema.json`).

It is validated rather than copied because the client writes it and it ends up in
the audit record. External input is bounded before being stored, for the same reason
rejection fields are sanitized before entering a prompt.

A malformed `traceparent` is not an error, just `None`: trace linking is an
observability convenience and must not fail a legitimate query.
"""

from __future__ import annotations

import re
from typing import Any, Final

#: W3C Trace Context: `00-<32 hex trace-id>-<16 hex span-id>-<2 hex flags>`.
#: https://www.w3.org/TR/trace-context/
_TRACEPARENT: Final = re.compile(
    r"^(?P<version>[0-9a-f]{2})-(?P<trace>[0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$"
)

#: The all-zero `trace-id` is defined as invalid by the specification.
_INVALID_TRACE: Final = "0" * 32

#: Length cap checked before the regex, so oversized input is discarded early.
MAX_TRACEPARENT: Final = 128


def trace_id_from(meta: Any) -> str | None:
    """The `trace-id` of a well-formed `traceparent`, or `None`.

    Only the `trace-id` is returned: the `span-id` identifies the client's span, and
    storing it would suggest this server is that span.
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
        # Version `ff` is reserved as invalid by the specification.
        return None
    return found.group("trace")
