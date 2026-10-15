"""Who is asking, with what authority and with what budget.

The role never comes from unauthenticated data (I-05). MCP 2026-07-28 removed
protocol sessions, so anything the client says about itself is transported data,
never authority. This package holds the three legitimate sources (server process,
server-minted `PrincipalToken`, CLI flag) and the business-signed access matrix.
"""

from __future__ import annotations

import pathlib
from typing import Final

GENERATED_DIR: Final = pathlib.Path(__file__).resolve().parent / "generated"
POLICY_PATH: Final = GENERATED_DIR / "policy.json"
BUDGETS_PATH: Final = GENERATED_DIR / "budgets.json"

__all__ = ["BUDGETS_PATH", "GENERATED_DIR", "POLICY_PATH"]
