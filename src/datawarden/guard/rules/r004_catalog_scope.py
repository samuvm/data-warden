"""R004: only tables from the generated catalog, and no table functions.

Two ways of pulling data from outside the scope, both closed here.

A table that is not in the catalog, checked on the `Table` nodes of the unqualified
tree.

A table function. `FROM read_parquet('/etc/passwd')` names a file reader, not a table.
sqlglot represents it as a `Table` whose content is not an identifier, and that
structural difference is what separates it from a real table, with no list of
forbidden functions needed. `KNOWN_DANGEROUS` exists only to produce a better message:
what stops the attack is absence from the allowlist, not presence on a denylist.

The other half of the rule, that every column exists, is enforced by `qualify()` with
`validate_qualify_columns=True`, and the validator maps that failure to this same
`rule_id`. That is deliberate: to the caller, "no such column" and "no such table" are
the same problem, and two rules would give two messages for the same confusion.
"""

from __future__ import annotations

from sqlglot import expressions as exp

from datawarden.domain.types import Position, Severity
from datawarden.guard.allowlist import KNOWN_DANGEROUS
from datawarden.guard.rule import PASS, PRE_QUALIFY, GuardContext, RuleResult, reject
from datawarden.guard.rules import messages


class CatalogScopeRule:
    """Every referenced relation exists in the generated catalog."""

    rule_id = "R004"
    code = "relation_out_of_scope"
    severity = Severity.SECURITY
    summary = "Every table in the tree exists in the generated catalog"
    families: tuple[str, ...] = (
        "tabla_fuera_de_ambito",
        "funcion_de_tabla",
        "columna_inexistente",
    )
    phase = PRE_QUALIFY

    def check(self, ctx: GuardContext) -> RuleResult:
        cte_names = {
            cte.alias_or_name.lower() for cte in ctx.tree.find_all(exp.CTE) if cte.alias_or_name
        }
        for table in ctx.tree.find_all(exp.Table):
            inner = table.this
            if not isinstance(inner, exp.Identifier):
                name = _function_name(inner)
                # The decision (whether the function reads from outside the process)
                # stays here and is measured; the wording lives in `messages`.
                message, suggestion = messages.table_function(
                    name, dangerous=name in KNOWN_DANGEROUS
                )
                return reject(
                    self,
                    message=message,
                    suggestion=suggestion,
                    position=Position.STATEMENT,
                    subject=name,
                    retryable=False,
                )
            name = table.name.lower()
            # A relation qualified with another database. In
            # `SELECT 1 FROM other_db.dim_customer` the name is `dim_customer`, which is
            # in the catalog. The catalog is a single schema (multiple schemas are out of
            # scope in `docs/DESIGN.md`), so any qualifier is an attempt to reach outside.
            qualifier = (table.db or table.catalog or "").lower()
            if qualifier:
                message, suggestion = messages.qualified_relation(qualifier, name)
                return reject(
                    self,
                    message=message,
                    suggestion=suggestion,
                    position=Position.STATEMENT,
                    subject=f"{qualifier}.{name}",
                    retryable=False,
                )
            if name in cte_names:
                continue
            if ctx.schema.table(name) is None:
                message, suggestion = messages.relation_unknown(name)
                return reject(
                    self,
                    message=message,
                    suggestion=suggestion,
                    position=Position.STATEMENT,
                    subject=name,
                    retryable=True,
                )
        return PASS


def _function_name(node: exp.Expression | None) -> str:
    """Name of the table function, lowercased and unadorned."""
    if node is None:
        return "unknown"
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return node.__class__.__name__.lower()


RULE = CatalogScopeRule()
