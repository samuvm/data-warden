"""The same system over HTTP.

Shows the domain does not depend on the transport: the same four operations are
served over two transports without touching `guard/`, `cost/`, `mask/` or `audit/`,
which can be checked by reading this file's imports.

It imports the same things as `mcp/`: the audited executor from the `audit/` factory
and the tools from `service/tools.py`. No policy logic lives here; a rule added here
would apply to one transport only.

The role does not come from here either: headers, body and parameters are
caller-chosen data, like MCP `_meta` (`G-ROLE-SPOOF`).

A rejection is answered with 200, not 4xx, as in MCP: it is not a protocol error but
an answer carrying the rule, reason and alternative. The body's `outcome` says
whether it is rows or a rejection.
"""

from __future__ import annotations

import pathlib
from typing import Any, Final

from datawarden.domain.types import Principal
from datawarden.mask.config import MaskConfig
from datawarden.service.principal import from_server_process
from datawarden.service.tools import WardenTools

#: Version reported by `/health`; matches the MCP server version.
API_VERSION: Final = "0.7.0"


def build_app(
    *,
    database: pathlib.Path,
    audit_db: pathlib.Path | None = None,
    principal: Principal | None = None,
    mask: MaskConfig | None = None,
) -> Any:
    """Build the FastAPI app on the same audited executor MCP uses.

    `mask` can be injected (tests and the gate do); in a deployment it comes from the
    environment. Without either, `load_mask_config` refuses to build.
    """
    from fastapi import Body, FastAPI

    from datawarden.audit.factory import DEFAULT_AUDIT_DB, build_executor
    from datawarden.mask.config import load_mask_config
    from datawarden.principal import POLICY_PATH
    from datawarden.principal.policy import load_policy

    mask = mask or load_mask_config(load_policy(POLICY_PATH))
    executor = build_executor(
        database=database, audit_db=audit_db or DEFAULT_AUDIT_DB, mask=mask
    )
    tools = WardenTools(executor=executor, principal=principal or from_server_process())

    app = FastAPI(
        title="Data Warden",
        version=API_VERSION,
        description=(
            "The same guard, budget, masking and audit as the MCP server. A rejection "
            "is returned with 200 and `outcome: rejected`: it is the answer, not a "
            "protocol error."
        ),
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        """Alive with the catalog loaded. Does not touch the engine."""
        return {
            "status": "ok",
            "version": API_VERSION,
            "tables": len(executor.schema.published().tables),
        }

    @app.get("/catalog")
    def catalog() -> dict[str, Any]:
        """Published relations; the same discovery path as in MCP."""
        return tools.describe_table()

    @app.get("/tables/{table}")
    def describe(table: str) -> dict[str, Any]:
        return tools.describe_table(table)

    @app.get("/tables/{table}/sample")
    def sample(table: str, limit: int = 10) -> dict[str, Any]:
        return tools.sample_table(table, limit=limit)

    @app.post("/query")
    def query(
        question_sql: str = Body(..., embed=True),
        question: str | None = Body(None, embed=True),
    ) -> dict[str, Any]:
        """Execute if allowed; respond 200 whether it executes or rejects."""
        return tools.run_query(question_sql, question=question)

    @app.post("/explain")
    def explain(question_sql: str = Body(..., embed=True)) -> dict[str, Any]:
        return tools.explain_cost(question_sql)

    return app
