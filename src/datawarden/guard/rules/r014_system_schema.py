"""R014: no system schemas.

`docs/spec/policy.yaml` excludes `dim_merchant.traffic_weight` from the published
catalog, but the engine still knows about it:

    SELECT column_name FROM information_schema.columns WHERE table_name = 'dim_merchant'

returns the excluded column, and with it the name of everything the catalog chose not
to publish. Reading the engine's catalog underneath the published one turns a policy
decision into a suggestion.

Runs before R004, which would also reject ("table not in catalog"), because the message
matters: telling the caller that an existing table does not exist invites retries.
This rule states what happened and where to look instead.
"""

from __future__ import annotations

from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.allowlist import SYSTEM_SCHEMAS
from datawarden.guard.rule import PASS, PRE_QUALIFY, GuardContext, RuleResult, reject
from datawarden.guard.rules import messages


class SystemSchemaRule:
    """No system schema or system table, qualified or bare."""

    rule_id = "R014"
    code = "system_schema_access"
    severity = Severity.SECURITY
    summary = "Engine metadata is never queried, only the published catalog"
    families: tuple[str, ...] = ("esquema_de_sistema", "enumeracion_de_catalogo")
    phase = PRE_QUALIFY

    def check(self, ctx: GuardContext) -> RuleResult:
        for table in ctx.tree.find_all(exp.Table):
            for part in (table.db, table.catalog, table.name):
                if part and part.lower() in SYSTEM_SCHEMAS:
                    message, suggestion = messages.system_schema(part.lower())
                    return reject(
                        self,
                        message=message,
                        suggestion=suggestion,
                        position=Position.STATEMENT,
                        subject=part.lower(),
                        retryable=False,
                    )
        return PASS


RULE = SystemSchemaRule()
