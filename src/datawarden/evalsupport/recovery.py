"""Expansion of the `G-RECOVERY` corpus seeds. Pure and deterministic.

Only the expansion lives here, because of I-05. Reading the corpus, including the
role each case is validated with, lives in `scripts/recoverylib.py`:
`scripts/check_role_source.py` forbids any published module from taking a role out
of a dict, with no exemption for evaluation fixtures. `G-ROLE-SPOOF` is an axiom.

What remains has no role and still matters: which SQL string reaches the guard. That
is tested and covered.

Repetition exists because three R013 seeds must exceed 4,000 tree nodes to trigger
the rule, and writing them out would be thirty kilobytes of `+ 1` in a reviewable
file. Expansion is declarative, and the check re-validates the expanded SQL against
the guard: what is checked is what is sent, not the template.
"""

from __future__ import annotations

from typing import Any, Final

#: Signed corpus, relative to the repository root. Read by `scripts/`.
CORPUS_PATH: Final = "evals/golden/recovery.yaml"

#: The two placeholders expansion substitutes. Each extra form would be one more way
#: for the measured SQL to differ from the SQL read.
REPEAT_MARK: Final = "{repeticion}"
BRANCH_MARK: Final = "{union_de_ramas}"


def expand(seed: str, repetition: dict[str, Any] | None) -> str:
    """Apply the declared repetition, if any. Deterministic."""
    if repetition is None:
        return " ".join(seed.split())
    times = int(repetition["veces"])
    if BRANCH_MARK in seed:
        template = str(repetition["plantilla"])
        joiner = str(repetition["union"])
        branches = [template.replace("{i}", str(index)) for index in range(times)]
        return seed.replace(BRANCH_MARK, joiner.join(branches))
    fragment = str(repetition["fragmento"])
    return seed.replace(REPEAT_MARK, fragment * times)
