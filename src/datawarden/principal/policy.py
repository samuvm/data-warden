"""Access policy: role, column and position in the tree.

The policy is a scale of position, not of trust. Masking the output alone is not
enough: if a column can be filtered on, it can be reconstructed by asking questions
without ever seeing its value. Birth dates between 1930 and 2010 span 29,220 days,
so about fifteen queries with different literals pin down a person's exact date.
Hence `mask` allows the column in a direct projection and rejects it in `WHERE`,
`JOIN ON`, `GROUP BY`, `ORDER BY`, `HAVING`, `QUALIFY`, inside a function and as a
window partition key.

Loaded from `principal/generated/policy.json`, compiled by
`scripts/compile_contracts.py` from the signed YAML. The domain does not parse YAML
(PyYAML is not a dependency), and the guard is on every query's critical path:
`G-GUARD-P95` requires p95 <= 25 ms.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final

from datawarden.domain.types import Position, Role


class Level(StrEnum):
    """Access level of a column for a role."""

    ALLOW = "allow"
    MASK = "mask"
    DENY = "deny"


#: The signed YAML names positions in Spanish because business owners read it; the
#: domain uses `Position`. The mapping lives here, in one place, not across the guard.
_POSITION_ALIASES: Final[dict[str, Position]] = {
    "where": Position.WHERE,
    "join_on": Position.JOIN_ON,
    "group_by": Position.GROUP_BY,
    "order_by": Position.ORDER_BY,
    "having": Position.HAVING,
    "qualify": Position.QUALIFY,
    "argumento_de_funcion": Position.FUNCTION_ARGUMENT,
    "function_argument": Position.FUNCTION_ARGUMENT,
    "clave_de_particion_de_ventana": Position.WINDOW_PARTITION,
    "window_partition": Position.WINDOW_PARTITION,
}


@dataclass(frozen=True, slots=True)
class ColumnPolicy:
    """One column's row in the matrix."""

    column: str
    levels: Mapping[Role, Level]
    #: Business classification as signed in the policy. R012 reasons over this, not
    #: over a hardcoded list, so reclassifying a column needs no Python change.
    data_type: str | None = None
    generalized: str | None = None
    transformation: str | None = None
    keep_last_n: int | None = None
    derived_from: tuple[str, ...] = ()
    admin_exception: bool = False
    published_in_catalog: bool = True


@dataclass(frozen=True, slots=True)
class DerivationViolation:
    """An `allow` column derived from a column that is not `allow` for the same role.

    `segment_code` embeds `age_band` verbatim: if `age_band` were ever restricted,
    `segment_code` would be a bypass. There is no conflict today; this check keeps
    it that way.
    """

    derived: str
    source: str
    role: Role


@dataclass(frozen=True, slots=True)
class AccessPolicy:
    """The full matrix, indexed for microsecond lookups."""

    columns: Mapping[str, ColumnPolicy]
    default_level: Level = Level.ALLOW
    forbidden_positions_in_mask: frozenset[Position] = frozenset()
    deterministic_masking: bool = False
    pepper_from: str = ""
    excluded_from_catalog: frozenset[str] = frozenset()
    signed_by: str | None = None
    source_sha256: str = ""
    _restricted: Mapping[Role, frozenset[str]] = field(default_factory=dict, repr=False)

    def column(self, name: str) -> ColumnPolicy:
        """The column's row, or a default row if it is not in the matrix.

        Returning a row instead of `None` keeps the default in one place; otherwise
        every caller re-implements it and the defaults drift.
        """
        found = self.columns.get(name.lower())
        if found is not None:
            return found
        return ColumnPolicy(
            column=name.lower(),
            levels=MappingProxyType(dict.fromkeys(Role, self.default_level)),
        )

    def level_for(self, name: str, role: Role) -> Level:
        return self.column(name).levels[role]

    def is_position_allowed(self, name: str, role: Role, position: Position) -> bool:
        """Whether the column may appear at this position for this role.

        `allow` anywhere; `deny` nowhere, not even the projection; `mask` only where
        it does not open the side channel.
        """
        level = self.level_for(name, role)
        if level is Level.ALLOW:
            return True
        if level is Level.DENY:
            return False
        return position not in self.forbidden_positions_in_mask

    def generalized_for(self, name: str) -> str | None:
        """The column published in its place, if the policy declares one."""
        return self.column(name).generalized

    def restricted_columns(self, role: Role) -> frozenset[str]:
        """Columns that are not `allow` for the role.

        Precomputed at load time: scanning 300 columns per query would not fit the
        25 ms budget of `G-GUARD-P95`.
        """
        return self._restricted.get(role, frozenset())

    def derivation_violations(self) -> tuple[DerivationViolation, ...]:
        """Every `allow` column whose source is not `allow` for the same role.

        A column absent from the matrix defaults to `allow`, so deriving from it is
        not a violation.
        """
        violations: list[DerivationViolation] = []
        for name, spec in sorted(self.columns.items()):
            for source in spec.derived_from:
                for role in Role:
                    if spec.levels[role] is Level.ALLOW and self.level_for(
                        source, role
                    ) is not (Level.ALLOW):
                        violations.append(
                            DerivationViolation(derived=name, source=source, role=role)
                        )
        return tuple(violations)

    def undeclared_admin_denials(self) -> tuple[str, ...]:
        """Columns hidden from admin without a declared `excepcion_admin`.

        The contract states that admin sees everything, with exactly one signed
        exception. Declaring it in data keeps this check from flagging it, and keeps
        anyone from "fixing" it by granting the column to admin.
        """
        return tuple(
            name
            for name, spec in sorted(self.columns.items())
            if spec.levels[Role.ADMIN] is not Level.ALLOW and not spec.admin_exception
        )


def policy_from_dict(payload: dict[str, Any]) -> AccessPolicy:
    """Build the policy from the compiled JSON. Pure: no disk, no YAML."""
    default = Level(payload.get("default_level", "allow"))
    columns: dict[str, ColumnPolicy] = {}
    for name, spec in payload["columns"].items():
        columns[name.lower()] = ColumnPolicy(
            column=name.lower(),
            levels=MappingProxyType({r: Level(spec["levels"][r.value]) for r in Role}),
            data_type=spec.get("data_type"),
            generalized=spec.get("generalized"),
            transformation=spec.get("transformation"),
            keep_last_n=spec.get("keep_last_n"),
            derived_from=tuple(s.lower() for s in spec.get("derived_from", ())),
            admin_exception=bool(spec.get("admin_exception", False)),
            published_in_catalog=bool(spec.get("published_in_catalog", True)),
        )

    forbidden = frozenset(
        _POSITION_ALIASES[p]
        for p in payload.get("forbidden_positions_in_mask", ())
        if p in _POSITION_ALIASES
    )
    restricted = {
        role: frozenset(n for n, c in columns.items() if c.levels[role] is not Level.ALLOW)
        for role in Role
    }
    return AccessPolicy(
        columns=MappingProxyType(columns),
        default_level=default,
        forbidden_positions_in_mask=forbidden,
        deterministic_masking=bool(payload.get("deterministic_masking", False)),
        pepper_from=str(payload.get("pepper_from", "")),
        excluded_from_catalog=frozenset(
            c.lower() for c in payload.get("excluded_from_catalog", ())
        ),
        signed_by=payload.get("signed_by"),
        source_sha256=str(payload.get("source_sha256", "")),
        _restricted=MappingProxyType(restricted),
    )


def load_policy(path: pathlib.Path) -> AccessPolicy:
    """Load the compiled policy. The only function in this module that touches disk."""
    return policy_from_dict(json.loads(path.read_text(encoding="utf-8")))
