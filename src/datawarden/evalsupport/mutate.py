"""AST mutation over a seed corpus. The other half of `G-WRITE-BLOCK`.

`docs/RULES.md §7`, error 8, rules out the obvious option: a grammar-based generator
of valid SQL for Hypothesis is a one- to two-week subproject. Mutating the trees of a
seed corpus costs a tenth of that.

Premise: a mutated attack is still an attack. If `DELETE FROM t` is rejected, it
must also be rejected when wrapped in a CTE, with comments between tokens, with
changed casing, or `UNION`ed with a legitimate query. Each mutation is a classic
evasion; applying them to the attack corpus yields thousands of cases nobody wrote by
hand and that do not resemble what the rule author had in mind.

In the other direction, a mutation of a legitimate query that the guard accepts must
still satisfy the invariants (no writes, no unexpanded star). Without that half,
"reject everything" would satisfy the goal.

Deterministic: the seed is an argument, not global `random` state, so every
counterexample is reproducible.
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterator
from typing import Final

import sqlglot
from sqlglot import expressions as exp
from sqlglot.errors import SqlglotError

#: Dialect synonyms. `substr` vs `substring` does not change semantics; a rule keyed
#: on the name rather than the node would break, which is what this looks for.
_SYNONYMS: Final[tuple[tuple[str, str], ...]] = (
    ("substr(", "substring("),
    ("upper(", "ucase("),
    ("count(", "COUNT("),
    ("!=", "<>"),
    (" AND ", " and "),
    (" OR ", " or "),
)

#: Comments injected between tokens: the top evasion against a keyword-matching
#: validator, and a no-op against a tree.
_COMMENTS: Final = ("/**/", "/* x */", "/*SELECT*/", "--\n", "/*\n*/")


def _tokens(sql: str) -> list[str]:
    return re.findall(r"\w+|\W", sql)


def inject_comment(sql: str, rng: random.Random) -> str:
    """Insert a comment between two arbitrary tokens."""
    tokens = _tokens(sql)
    if len(tokens) < 2:
        return sql
    at = rng.randrange(1, len(tokens))
    return "".join(tokens[:at]) + rng.choice(_COMMENTS) + "".join(tokens[at:])


def flip_case(sql: str, rng: random.Random) -> str:
    """Randomize letter case character by character."""
    return "".join(c.upper() if rng.random() < 0.5 else c.lower() for c in sql)


def swap_synonym(sql: str, rng: random.Random) -> str:
    """Replace a function with its dialect synonym."""
    frm, to = rng.choice(_SYNONYMS)
    return sql.replace(frm, to, 1)


def pad_whitespace(sql: str, rng: random.Random) -> str:
    """Replace spaces with newlines and tabs."""
    filler = rng.choice(["\n", "\t", "  ", "\r\n", " \n "])
    return sql.replace(" ", filler, rng.randint(1, 3))


def wrap_in_cte(sql: str, rng: random.Random) -> str:
    """Wrap in a CTE: the evasion that hides a write behind a SELECT."""
    name = f"m{rng.randrange(1000)}"
    # S608 flags injection by concatenation, which is the purpose here: this module
    # builds adversarial SQL to feed the guard. The rule stays active elsewhere in
    # `src/`, where real queries are built from a validated AST.
    return f"WITH {name} AS ({sql}) SELECT * FROM {name}"  # noqa: S608


def wrap_in_subquery(sql: str, rng: random.Random) -> str:
    name = f"s{rng.randrange(1000)}"
    return f"SELECT * FROM ({sql}) AS {name}"  # noqa: S608


def add_union(sql: str, rng: random.Random) -> str:
    """Union with a legitimate query, hiding the attack in one branch."""
    inocente = rng.choice(
        [
            "SELECT 1 AS x",
            "SELECT customer_sk AS x FROM dim_customer",
            "SELECT 2 AS x",
        ]
    )
    return (
        f"{sql} UNION ALL {inocente}" if rng.random() < 0.5 else f"{inocente} UNION ALL {sql}"
    )


def rename_aliases(sql: str, rng: random.Random) -> str:
    """Rename table aliases. An alias does not change where a value comes from."""
    try:
        tree = sqlglot.parse_one(sql, dialect="duckdb")
    except SqlglotError:
        # `SqlglotError`, not `ParseError`: an unbalanced quote is raised by the
        # tokenizer, a sibling exception. A crashing mutation would halt the corpus
        # on exactly the malformed inputs most worth mutating.
        return sql
    for i, table in enumerate(tree.find_all(exp.Table)):
        if table.alias:
            table.set(
                "alias", exp.TableAlias(this=exp.to_identifier(f"z{i}{rng.randrange(99)}"))
            )
    return tree.sql(dialect="duckdb")


def push_into_having(sql: str, _rng: random.Random) -> str:
    """Move the `WHERE` condition into `HAVING`, a different tree position."""
    try:
        tree = sqlglot.parse_one(sql, dialect="duckdb")
    except SqlglotError:
        # `SqlglotError`, not `ParseError`: an unbalanced quote is raised by the
        # tokenizer, a sibling exception. A crashing mutation would halt the corpus
        # on exactly the malformed inputs most worth mutating.
        return sql
    select = tree.find(exp.Select)
    where = select.args.get("where") if select is not None else None
    if select is None or where is None:
        return sql
    select.set("where", None)
    select.set("group", exp.Group(expressions=[exp.Literal.number(1)]))
    select.set("having", exp.Having(this=where.this))
    return tree.sql(dialect="duckdb")


#: The mutations. Each is a documented evasion technique, not a random transformation,
#: which is why the list is short.
MUTATIONS: Final = (
    inject_comment,
    flip_case,
    swap_synonym,
    pad_whitespace,
    wrap_in_cte,
    wrap_in_subquery,
    add_union,
    rename_aliases,
    push_into_having,
)


def mutants(sql: str, *, rounds: int, seed: int) -> Iterator[tuple[str, str]]:
    """Yield `(mutation_name, mutated_sql)` deterministically.

    Each round applies all nine mutations to the previous round's output, so later
    rounds produce composed mutations (a comment inside a CTE inside a UNION), which
    is where hastily written validators break.
    """
    rng = random.Random(seed)  # noqa: S311 -- reproducible corpus, not cryptography
    current = [sql]
    for round_no in range(rounds):
        siguiente: list[str] = []
        for base in current:
            for mutation in MUTATIONS:
                try:
                    mutated = mutation(base, rng)
                except Exception:  # noqa: S112
                    # A crashing mutation is skipped rather than halting the corpus,
                    # which would leave already-broken inputs, the most interesting
                    # ones, untested.
                    continue
                if mutated and mutated != base:
                    yield (f"{mutation.__name__}@{round_no}", mutated)
                    siguiente.append(mutated)
        # Propagate only a few per round: full propagation grows exponentially
        # (nine mutations per input per round).
        current = siguiente[:3]
