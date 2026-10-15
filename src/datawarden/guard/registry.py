"""Rule registry: explicit, ordered and immutable.

Rules are imported one by one rather than discovered with a glob. Auto-discovery would
make the order depend on the filesystem, and order is semantics because it decides
which rule reports the rejection; it would also let a rule drop out silently when a
file is renamed. Here, removing a rule is a visible line in the diff, and
`scripts/check_rules_registry.py` forbids it (I-01).

The order is by specificity, not alphabetical. The first rule that rejects provides the
message, so earlier rules must be the ones that best explain the failure:

- R013 first: it bounds the size of the tree the other thirteen walk. Run later, an
  AST bomb would exhaust the budget before reaching it.
- R010 before R001: a `DELETE` is a `DELETE` whether at the root or hidden in a CTE.
  With R001 first, a CTE hiding a write would get "only SELECT is accepted" on a query
  that starts with SELECT.
- R014 before R004: `information_schema` exists. Saying "that table does not exist"
  when it does invites retries; R014 states what happened.
- R008 before R012: both reject `PARTITION BY birth_date` for `analyst`, and both are
  right, but R008 also knows the policy publishes `age_band` as an alternative and says
  so, while R012 can only say "that group is one person". The attack corpus requires
  each case to be stopped by the rule that explains it best. With this order,
  `GROUP BY national_id` under ADMIN still lands on R012, which is correct: R008 has no
  objection when the role may see the column, but aggregating down to an individual is
  a different problem.
- R006 last: it is the only rule that rewrites in most cases, clamping to the role's
  `max_rows`. Clamping earlier would make the other rules reason about a tree
  different from the one submitted.
"""

from __future__ import annotations

from typing import Final

from datawarden.guard.rule import POST_QUALIFY, PRE_QUALIFY, Rule
from datawarden.guard.rules import (
    r001_single_read_statement,
    r002_node_allowlist,
    r003_function_allowlist,
    r004_catalog_scope,
    r005_join_predicate,
    r006_row_limit,
    r007_nesting_depth,
    r008_column_policy,
    r009_star_expansion,
    r010_no_write_node,
    r011_set_operation_scope,
    r012_group_cardinality,
    r013_tree_size,
    r014_system_schema,
)

#: All fourteen rules, in execution order.
RULES: Final[tuple[Rule, ...]] = (
    r013_tree_size.RULE,
    r010_no_write_node.RULE,
    r001_single_read_statement.RULE,
    r014_system_schema.RULE,
    r004_catalog_scope.RULE,
    r002_node_allowlist.RULE,
    r003_function_allowlist.RULE,
    r009_star_expansion.RULE,
    r005_join_predicate.RULE,
    r007_nesting_depth.RULE,
    r011_set_operation_scope.RULE,
    r008_column_policy.RULE,
    r012_group_cardinality.RULE,
    r006_row_limit.RULE,
)

PRE_RULES: Final = tuple(r for r in RULES if r.phase == PRE_QUALIFY)
POST_RULES: Final = tuple(r for r in RULES if r.phase == POST_QUALIFY)

#: `rule_id` -> rule. Used by the parametrised case corpus and the architecture checks.
BY_ID: Final[dict[str, Rule]] = {rule.rule_id: rule for rule in RULES}

#: Every attack family that some rule claims to stop (I-14).
FAMILIES: Final[frozenset[str]] = frozenset(f for rule in RULES for f in rule.families)
