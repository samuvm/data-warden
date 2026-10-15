"""The rule protocol.

Every rule shares one signature, which is what lets the case corpus parametrise over
rules: one rule, one module, one case file.

A rule is a pure function from `GuardContext` to `RuleResult`. It reads no files,
touches no engine, knows nothing about transport and never raises: if something goes
wrong inside, it returns a rejection. The guard's only `except Exception` lives in
`validator.validate` (I-04), and this contract is what makes that single safety net
sufficient.

A rule may rewrite the tree (R006 injects or clamps the `LIMIT`), which is why
`RuleResult` carries an optional tree. Mutating the tree in place would make it
impossible to tell which rule changed what, and the audit record could no longer explain
the query that ran.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from sqlglot import expressions as exp

from datawarden.catalog.types import CatalogSchema
from datawarden.domain.types import Principal, RejectionReason, Severity
from datawarden.principal.policy import AccessPolicy


@dataclass(frozen=True, slots=True)
class GuardContext:
    """Everything a rule needs, already resolved and indexed.

    For post-qualify rules the tree arrives qualified by
    `sqlglot.optimizer.qualify.qualify()` against the generated catalog: aliases, nested
    CTEs and `SELECT *` are already resolved, so no rule has to work out which table a
    column belongs to. The alternative, each rule resolving aliases on its own, would be
    fourteen implementations of the same thing and fourteen places to get it wrong.
    """

    #: The input string, for diagnostics and measurement only. It is never executed:
    #: what runs is `tree.sql(dialect)` (I-02).
    raw_sql: str
    tree: exp.Expression
    schema: CatalogSchema
    policy: AccessPolicy
    principal: Principal
    dialect: str
    max_rows: int
    #: Monotonic-clock deadline. A guard that takes too long is a guard that is down, so
    #: every query carries its own expiry.
    deadline: float | None = None
    #: `table.column` -> base columns. Copied from the catalog so it is not walked per
    #: column: `G-GUARD-P95` allows 25 ms for the whole query.
    lineage: dict[str, tuple[str, ...]] = field(default_factory=dict, repr=False)
    #: `id(Column node)` -> base columns, resolved within this query. This is what follows
    #: an alias through a CTE or a subquery: `c.b` is `dim_customer.birth_date` under
    #: another name, and a policy that did not follow it would leave the predicate side
    #: channel open.
    column_sources: dict[int, tuple[str, ...]] = field(default_factory=dict, repr=False)

    def sources_of(self, column: object, table: str, name: str) -> tuple[str, ...]:
        """Base columns behind this column node.

        Query lineage first (it follows aliases, CTEs and subqueries), catalog lineage
        only as a fallback. The order matters: query lineage is more specific, and the
        catalog knows nothing about relations the query itself defines.
        """
        found = self.column_sources.get(id(column))
        if found:
            return found
        return self.base_columns(table, name)

    def base_columns(self, table: str, column: str) -> tuple[str, ...]:
        """Base columns behind `table.column`, already resolved.

        Without lineage (a relation outside the catalog) the reference is returned as is.
        Deciding what to do with an unknown column is R004's job; this method does not
        invent a safe answer.
        """
        key = f"{table.lower()}.{column.lower()}"
        return self.lineage.get(key, (key,))


@dataclass(frozen=True, slots=True)
class RuleResult:
    """What a rule returns: a rejection, a rewritten tree, or nothing.

    Nothing is the normal case and therefore the default: a rule with no objection
    builds no object.
    """

    rejection: RejectionReason | None = None
    #: The rewritten tree, if the rule rewrote it. `None` means "untouched", which is not
    #: the same as "rewritten to an identical tree".
    tree: exp.Expression | None = None
    #: Audit notes: what the rule did, if anything.
    notes: tuple[str, ...] = ()

    @property
    def rejected(self) -> bool:
        return self.rejection is not None


#: The guard's two phases, and why there are two.
#:
#: `qualify()` resolves aliases, expands `SELECT *` and qualifies every column with its
#: table, which spares eleven of the fourteen rules any lookup. But qualifying a
#: `DROP TABLE` means nothing, and handing `qualify()` a tree that is not even a query
#: asks it to decide something outside its job. Rules that decide what kind of thing the
#: tree is therefore run first, on the raw tree; rules that reason about columns run
#: afterwards, on the qualified tree.
PRE_QUALIFY: str = "pre"
POST_QUALIFY: str = "post"

#: The clean result, shared by every rule with no objection. A singleton avoids
#: allocating fourteen objects per query inside a 25 ms p95 budget.
PASS = RuleResult()


@runtime_checkable
class Rule(Protocol):
    """A guard rule.

    The class attributes are not decorative: `rule_id` is what
    `scripts/check_rules_registry.py` tracks so that no rule ever disappears (I-01),
    `code` groups metrics, `severity` decides whether the rejection is a security event,
    and `families` makes it checkable that every attack family has a rule that stops it
    (I-14).
    """

    rule_id: str
    code: str
    severity: Severity
    summary: str
    families: tuple[str, ...]
    phase: str

    def check(self, ctx: GuardContext) -> RuleResult:
        """Decide on the tree. Never raises."""
        ...


def reject(
    rule: Rule,
    *,
    message: str,
    suggestion: str,
    position: object = None,
    subject: str | None = None,
    alternative: str | None = None,
    retryable: bool = True,
) -> RuleResult:
    """Build a rule's rejection with its metadata filled in.

    It exists so that no rule can report the wrong `rule_id`, the costliest silent
    failure in this design: a case believed covered by R008 that is in fact stopped by
    R002 by accident. The day R002 changes, R008 has a hole and nobody notices.
    """
    from datawarden.domain.types import Position

    return RuleResult(
        rejection=RejectionReason(
            rule_id=rule.rule_id,
            code=rule.code,
            message=message,
            suggestion=suggestion,
            severity=rule.severity,
            position=position if isinstance(position, Position) else Position.UNKNOWN,
            subject=subject,
            alternative=alternative,
            retryable=retryable,
            docs=f"docs/spec/rules/{rule.rule_id}.md",
        )
    )
