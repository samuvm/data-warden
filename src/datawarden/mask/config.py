"""Masking pepper, and the startup check that the signed contract still says what it said.

This module refuses to start if the contract changed. `docs/spec/policy.yaml`,
signed on 2026-09-01, declares `determinista: true` and `pimienta_desde: config`
("never a per-session or per-day salt").

With a per-session salt, the resultset of any query touching a `mask` column would
not be comparable across runs, and evaluation questions involving a masked column
could not have a reference answer. Checking the contract at load time keeps that
decision from regressing silently: if the YAML changes or a future compiler stops
emitting the field, loading stops instead of continuing with different behaviour.

Trade-off of a fixed pepper (documented in `docs/threat-model.md §4.2`): it enables
grouping and counting without revealing values, and stable reference answers. It
gives up correlation resistance: the same value always yields the same hash, so
anyone who knows a value recognizes it in a resultset, and anyone holding the pepper
can build a dictionary and reverse any low-cardinality column.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

from datawarden.principal.policy import AccessPolicy

#: Environment variable holding the pepper in a deployment. There is no default: a
#: default pepper is a public pepper, and `check_secrets.py` would rightly flag any
#: secret-looking literal in the code.
PEPPER_ENV: Final = "DATAWARDEN_MASK_PEPPER"

#: Minimum length. 32 characters keeps the pepper out of brute-force reach when the
#: attacker knows the algorithm (it is published in `docs/spec/policy.yaml`) and holds
#: a (value, hash) pair from a low-cardinality column.
MIN_PEPPER_LENGTH: Final = 32


@dataclass(frozen=True, slots=True)
class MaskConfig:
    """Masking configuration. Currently just the pepper."""

    pepper: str

    def __post_init__(self) -> None:
        if not self.pepper.strip():
            message = (
                "the masking pepper (`pimienta_desde` in docs/spec/policy.yaml) is "
                "empty. Without it, `sha256(value)` is a public dictionary: the hashed "
                "columns have bounded cardinality (an IP, a device fingerprint) and "
                "anyone with a hash table can reverse them. An empty pepper is not "
                f"weaker security, it is none. Set {PEPPER_ENV}."
            )
            raise ValueError(message)
        if len(self.pepper) < MIN_PEPPER_LENGTH:
            message = (
                f"the pepper has {len(self.pepper)} characters and the minimum is "
                f"{MIN_PEPPER_LENGTH}. The algorithm is published in "
                "docs/spec/policy.yaml, so the pepper is the only thing between the "
                "hash and the original value."
            )
            raise ValueError(message)


def load_mask_config(policy: AccessPolicy, *, pepper: str | None = None) -> MaskConfig:
    """Build the configuration after checking the contract still says what was signed.

    Tests and the gate pass `pepper` explicitly; a deployment reads it from the
    environment. There is no default on purpose: masking with a well-known pepper is
    worse than not masking, because it looks like protection without being one.
    """
    if not policy.deterministic_masking:
        message = (
            "docs/spec/policy.yaml declares `determinista: false`, which this masker "
            "does not support. The policy as signed (`firmado_el: 2026-09-01`) says "
            "`determinista: true` and `pimienta_desde: config`, with its reason: "
            "with a per-session salt, evaluation questions touching a masked column "
            "cannot have a reference answer. Changing this requires a newly signed "
            "policy, not a default value."
        )
        raise ValueError(message)
    if policy.pepper_from != "config":
        message = (
            f"docs/spec/policy.yaml declares `pimienta_desde: {policy.pepper_from!r}` "
            "and this masker only supports `config`. The signed contract says "
            "literally: never a per-session or per-day salt."
        )
        raise ValueError(message)
    return MaskConfig(pepper=pepper if pepper is not None else os.environ.get(PEPPER_ENV, ""))
