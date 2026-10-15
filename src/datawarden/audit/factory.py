"""Builds the audited executor. `audit/` is the only place where it can live.

`datawarden.engines` is imported only from here and from `audit/executor.py`; the
import-linter contract "the engine is reachable only through the audit (I-06)"
forbids it everywhere else. `mcp/`, `http/` and the CLI all need an executor, and if
each built its own `DuckDBEngine` there would be three paths to the engine that skip
the audit.

Everything the rings need is resolved here, and missing pieces fail at startup. An
MCP server that starts and then fails on the first query is worse than one that does
not start: the client already believes it is connected.
"""

from __future__ import annotations

import pathlib
from typing import Final

from datawarden.audit.executor import AuditedExecutor
from datawarden.audit.store import AuditStore
from datawarden.mask.config import MaskConfig

#: Default location of the audit chain, created if missing. The warehouse database
#: is never created: an empty one would turn "data not found" into "no rows".
#:
#: Relative to the package, not the working directory: when a desktop app launches
#: the server it picks the working directory, and a chain that moves between runs is
#: not a chain.
DEFAULT_AUDIT_DB: Final = pathlib.Path(__file__).resolve().parents[3] / "var" / "audit.sqlite3"


class MissingDatasetError(RuntimeError):
    """The warehouse is not where it was said to be. Distinct from an engine failure."""


def build_executor(
    *,
    database: pathlib.Path,
    audit_db: pathlib.Path = DEFAULT_AUDIT_DB,
    mask: MaskConfig,
) -> AuditedExecutor:
    """The executor with all rings wired, or an error that says what is missing.

    The engine's `setup_sql` installs the `warden_hash` macro with the same pepper the
    masker receives. If they differed, the rewritten tree would call a macro keyed
    with a different secret and masked values would stop being stable across runs,
    which the signed contract forbids.
    """
    from datawarden.catalog import SCHEMA_PATH, load_generated
    from datawarden.catalog.statistics import load as load_stats
    from datawarden.cost import STATISTICS_PATH
    from datawarden.engines.duckdb_engine import DuckDBEngine
    from datawarden.mask.macro import macro_ddl
    from datawarden.principal import BUDGETS_PATH, POLICY_PATH
    from datawarden.principal.budgets import load_budgets
    from datawarden.principal.policy import load_policy

    if not database.exists():
        # Suggest the profile that is actually missing. A fixed `PROFILE=dev` hint
        # was useless once the server defaulted to `full`.
        perfil = "dev" if "cierzo-dev" in database.name else "full"
        message = (
            f"{database} does not exist. The warehouse is never created empty: a "
            "server without data turns 'warehouse not found' into 'no rows', and the "
            "caller cannot tell them apart. Generate it with "
            f"`make dataset PROFILE={perfil}`"
            + (
                ", or use the small profile with `make dataset PROFILE=dev` and "
                "`--database datagen/out/cierzo-dev.duckdb`."
                if perfil == "full"
                else "."
            )
        )
        raise MissingDatasetError(message)
    if not SCHEMA_PATH.exists():
        message = (
            "no generated catalog found. The catalog is never written by hand (I-07): "
            "run `warden catalog build`."
        )
        raise MissingDatasetError(message)

    audit_db.parent.mkdir(parents=True, exist_ok=True)
    return AuditedExecutor(
        engine=DuckDBEngine(database, setup_sql=(macro_ddl(mask),)),
        store=AuditStore(str(audit_db)),
        schema=load_generated(SCHEMA_PATH),
        policy=load_policy(POLICY_PATH),
        budgets=load_budgets(BUDGETS_PATH),
        stats=load_stats(STATISTICS_PATH),
        mask=mask,
    )
