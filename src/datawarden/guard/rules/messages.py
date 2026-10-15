"""Rejection wording, and only wording; excluded from mutation testing.

The exclusion covers the literal text, not its composition. A position table that
returns the wrong label produces a message saying WHERE when the column was in a
GROUP BY, and that is a bug, not prose. The line between the two:

- Stays in the rule and is measured: the `Position`, the `subject`, whether the
  rejection is `retryable`, which policy level was violated, whether an alternative is
  published. These are decisions, and the case corpus asserts each of them through the
  `posicion`, `sujeto` and `reintentable` fields.
- Lives here and is not measured: the words used to report them. A mutant that drops a
  word from a sentence reveals no defect; catching it would require asserting messages
  word by word, which yields a suite that breaks on every rewording (the same reason
  `docs/RULES.md` §2 forbids asserting generated SQL).

These texts are expected to change: actionable messages are what `G-RECOVERY` measures,
and they are tuned against how the model responds to each one.

Mechanism: `[tool.mutmut].do_not_mutate` lists this file, so mutmut copies it unmutated
and it yields zero mutants. It leaves both denominators (`G-MUT-GUARD` and
`G-MUTATION`) instead of moving from one to the other.

Nothing is decided here: no comparison on the tree, no `Position`, no `retryable`.
`scripts/check_mutation_scope.py` enforces this, because logic that slipped in would
leave the measured scope unnoticed.
"""

from __future__ import annotations

from typing import Final

from datawarden.domain.types import Position

#: `Position` -> phrase. A rendering table only: the rule decides the position from the
#: tree and the corpus asserts it by value; this only picks the words.
POSITION_TEXT: Final[dict[Position, str]] = {
    Position.PROJECTION: "in the SELECT list",
    Position.WHERE: "in a WHERE predicate",
    Position.JOIN_ON: "in a JOIN condition",
    Position.GROUP_BY: "in a GROUP BY",
    Position.ORDER_BY: "in an ORDER BY",
    Position.HAVING: "in a HAVING clause",
    Position.QUALIFY: "in a QUALIFY clause",
    Position.FUNCTION_ARGUMENT: "as an argument to a function",
    Position.WINDOW_PARTITION: "in a window PARTITION BY",
    Position.SUBQUERY: "inside a subquery",
    Position.CTE: "inside a CTE",
    Position.STATEMENT: "in the statement",
    Position.UNKNOWN: "somewhere the guard could not place",
}


def position_text(position: Position) -> str:
    """The position in words. Never empty: a silent rejection redirects nothing."""
    return POSITION_TEXT.get(position, "in the query")


# ------------------------------------------------------------------------ R001 ---


def not_a_query(kind: str) -> tuple[str, str]:
    return (
        f"the statement is a {kind.upper()} and only queries are accepted; "
        "this server answers questions about data, it does not run engine commands",
        "ask for the data itself with a SELECT. To find out what tables and columns "
        "exist, read the catalog resource instead of querying the engine's own metadata",
    )


def branch_not_a_query(kind: str) -> tuple[str, str]:
    return (
        f"one branch of the set operation is a {kind} and not a query; every branch of "
        "a UNION, EXCEPT or INTERSECT has to be a SELECT of its own",
        "write each branch as a full SELECT with its own FROM. A bare expression on "
        "one side of a UNION is not a query",
    )


# ------------------------------------------------------------------------ R002 ---


def node_not_allowed(name: str) -> tuple[str, str]:
    return (
        f"the query uses a {name} construct, which is not on the allowlist of "
        "analytical constructs this server accepts",
        "rewrite the question using plain SELECT, JOIN, WHERE, GROUP BY, ORDER BY and "
        "window functions. If this construct is genuinely needed, it has to be added "
        "to the allowlist as a decision",
    )


# ------------------------------------------------------------------------ R003 ---


def function_not_allowed(name: str, *, dangerous: bool) -> tuple[str, str]:
    """Wording for a rejected function; `dangerous` is decided by the rule.

    Whether the function reads from outside the process is a security decision that
    also governs `retryable`; it is made in `r003` against `KNOWN_DANGEROUS`. This only
    picks the words.
    """
    if dangerous:
        return (
            f"the function {name}() reads from outside the query — files, the network "
            "or the engine process — and is never executed here",
            "every answer this server gives comes from the catalog tables. Ask the "
            "question against them",
        )
    return (
        f"the function {name}() is not on the allowlist; the guard cannot reason about "
        "what an unknown function does, so it refuses it",
        "use a standard SQL function. If this one is genuinely needed, it has to be "
        "added to the allowlist as a decision, with its case",
    )


# ------------------------------------------------------------------------ R004 ---


def table_function(name: str, *, dangerous: bool) -> tuple[str, str]:
    """Wording for a rejected table function; `dangerous` is decided by the rule.

    Whether the function reads from outside the process (files, network) is decided in
    `r004` against `KNOWN_DANGEROUS`; this only decides whether the sentence says so.
    """
    danger = " it reads from outside the query;" if dangerous else ""
    return (
        f"the FROM clause uses the table function {name}(), not a table;{danger} "
        "only relations from the published catalog can be read",
        "name one of the catalog tables directly. The catalog resource lists every "
        "relation this server can read",
    )


def qualified_relation(qualifier: str, name: str) -> tuple[str, str]:
    return (
        f"the query names {qualifier}.{name}: a relation qualified with another "
        "database or schema. This server serves exactly one catalog and never reaches "
        "outside it",
        f"name the relation without a qualifier: {name}. If it is not in the catalog "
        "resource, it is not readable from here",
    )


def relation_unknown(name: str) -> tuple[str, str]:
    return (
        f"relation {name} is not in the generated catalog and cannot be read",
        "read the catalog resource and use one of the relations it lists. If the name "
        "looks close to an existing one, it is probably a typo in the table name",
    )


# ------------------------------------------------------------------------ R005 ---


def cartesian_join(left: str, right: str) -> tuple[str, str]:
    return (
        f"the join between {left or 'a subquery'} and {right or 'a subquery'} has no "
        "ON or USING condition, so it is a cartesian product between two large relations",
        "add the join key. Facts join dimensions through their surrogate key "
        "(`merchant_sk`, `customer_sk`, `card_sk`); the catalog resource lists them",
    )


# ------------------------------------------------------------------------ R006 ---


def offset_too_large(value: int, maximum: int) -> tuple[str, str]:
    return (
        f"OFFSET {value} is outside the allowed range [0, {maximum}]; a large offset "
        "makes the engine produce and discard rows, which costs the same as returning them",
        "narrow the question with a WHERE predicate instead of paging deep into the "
        "result. Filtering is what makes a query cheap",
    )


def limit_asks_for_nothing(value: int) -> tuple[str, str]:
    return (
        f"LIMIT {value} asks for no rows at all; this server answers questions about "
        "data, and the shape of a table is published in the catalog",
        "read the catalog resource for the columns and their types, or ask for a "
        "positive number of rows",
    )


def limit_not_literal(clause: str, rendered: str) -> tuple[str, str]:
    return (
        f"the {clause} is a {rendered} and not a literal number, so the number of rows "
        "cannot be known before running the query",
        f"write a plain number: `{clause} 100`. A row cap that depends on the data is "
        "not a cap",
    )


# ------------------------------------------------------------------------ R007 ---


def nesting_too_deep(deepest: int, maximum: int) -> tuple[str, str]:
    return (
        f"the query nests queries {deepest} levels deep and the limit is {maximum}; "
        "the cost of a correlated subquery at that depth cannot be estimated before "
        "running it",
        "flatten the query with CTEs at the top level, or aggregate in steps so each "
        "level answers one thing",
    )


# ------------------------------------------------------------------------ R008 ---


def lineage_unknown(name: str) -> tuple[str, str]:
    return (
        f"the guard cannot resolve where column {name} comes from, so it cannot tell "
        "whether the access policy applies to it",
        "reference the column from its table directly instead of through the construct "
        "that hides it. A column whose origin cannot be followed is refused, not "
        "assumed safe",
    )


def column_policy(
    base: str,
    role: str,
    position: Position,
    *,
    denied: bool,
    written: str | None,
    alternative: str | None,
) -> tuple[str, str]:
    """Column-policy rejection, with the detail that makes it actionable.

    Every branch here was decided by the rule: whether the level was `deny`, whether the
    column was reached under another name, and whether the policy publishes an
    alternative. This module picks words; `r008` decides.
    """
    where = position_text(position)
    if denied:
        message = f"column {base} is denied for role {role} and appears {where}"
    else:
        message = (
            f"column {base} is masked for role {role}: it may only appear as a direct "
            f"projection, and it appears {where}"
        )
    if written is not None:
        message += f", reached through {written}"

    if alternative is not None:
        suggestion = (
            f"use {alternative} instead: the policy publishes it as the generalised "
            "answer to the same question, and it is visible to your role"
        )
    elif not denied:
        suggestion = (
            "move the column to the SELECT list, where it is returned masked. What is "
            "not allowed is filtering, grouping or ordering by it — that would let the "
            "value be reconstructed one question at a time"
        )
    else:
        suggestion = (
            "this column is not available to your role in any position. Ask the "
            "question in terms of the aggregates and generalised columns the catalog "
            "publishes"
        )
    return message, suggestion


# ------------------------------------------------------------------------ R009 ---


def star_survived(where: str) -> tuple[str, str]:
    return (
        f"a * survived qualification inside {where}; the guard would then be deciding "
        "about columns it has never seen",
        "name the columns you want. The catalog resource lists them, and naming them "
        "is also what keeps the query cheap",
    )


# ------------------------------------------------------------------------ R010 ---


def write_node_present(kind: str, position: Position) -> tuple[str, str]:
    """The write node, and where it was.

    `position` is decided by `r010` from the node's ancestry and asserted by value in the
    corpus; this only renders it.
    """
    return (
        f"the tree contains a {kind.upper()} node {position_text(position)}; this "
        "system is read-only by construction and never writes",
        "rephrase the question as something to READ. If you need to know what would "
        "change, describe it with a SELECT that counts or lists the affected rows",
    )


# ------------------------------------------------------------------------ R011 ---


def values_branch() -> tuple[str, str]:
    return (
        "one branch of the set operation is a VALUES list, so it would add rows that "
        "do not come from the warehouse",
        "every branch has to read from a catalog relation. If you need a constant, put "
        "it in the projection of a branch that reads real rows",
    )


def too_many_branches(branches: int, maximum: int) -> tuple[str, str]:
    return (
        f"the query chains {branches} set-operation branches and the limit is "
        f"{maximum}; each branch is another scan",
        "use a single query with an IN or a JOIN against a dimension instead of "
        "unioning one branch per value",
    )


# ------------------------------------------------------------------------ R012 ---


def having_group_too_small(comparison: str, value: int, minimum: int) -> tuple[str, str]:
    return (
        f"the HAVING clause asks for groups whose count is {value} or fewer, and the "
        f"minimum group size is {minimum}; groups that small are individuals with the "
        "shape of an aggregate",
        f"ask for groups of at least {minimum} rows, or drop the HAVING and read the "
        "distribution of group sizes instead",
    )


def group_by_identifier(base: str, data_type: str, alternative: str | None) -> tuple[str, str]:
    """Grouping by an identifier. The alternative is published by the policy."""
    message = (
        f"grouping by {base} makes each group one person: it is a {data_type} and "
        "every distinct value identifies an individual"
    )
    if alternative is not None:
        suggestion = (
            f"group by {alternative} instead, which is the generalised column the "
            "policy publishes"
        )
    else:
        suggestion = "group by a generalised column so that every group covers many rows"
    return message, suggestion


# ------------------------------------------------------------------------ R013 ---


def tree_too_large(nodes: int, maximum: int) -> tuple[str, str]:
    return (
        f"the parsed tree has {nodes} nodes and the limit is {maximum}; a query this "
        "large cannot be validated within the guard budget",
        "split the question into smaller queries, or aggregate earlier so the "
        "expression tree stays within the limit",
    )


# ------------------------------------------------------------------------ R014 ---


def system_schema(part: str) -> tuple[str, str]:
    return (
        f"the query reads {part}, which is the engine's own metadata; the published "
        "catalog is the only description of this warehouse that this server serves",
        "read the catalog resource to find out which tables and columns exist. What "
        "the engine knows and what this server publishes are deliberately not the "
        "same thing",
    )
