"""Catalog types. Pure, frozen, with no engine imports.

The catalog is ring 1 and also the input to ring 3: `qualify()` cannot resolve an
alias, expand `SELECT *` or attribute a column to a table without the schema. It is
a dedicated type rather than a `dict` each layer interprets its own way.

Deliberately not represented here:

- Column sensitivity. That lives in `docs/spec/policy.yaml`; mixing it in would let
  a catalog regeneration change an access decision.
- Business meaning. That lives in `docs/spec/glossary.yaml`.
- Row counts and bytes. They change with the dataset profile; including them would
  make the catalog sha depend on row counts and void `G-CATALOG-FRESH`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

#: Canonical type families. The exact engine type name is kept separately: engines
#: spell the same type `VARCHAR` or `string`, and a guard rule comparing engine type
#: names would break when the engine changes.
FAMILY_INTEGER: Final = "integer"
FAMILY_DECIMAL: Final = "decimal"
FAMILY_FLOAT: Final = "float"
FAMILY_TEXT: Final = "text"
FAMILY_BOOLEAN: Final = "boolean"
FAMILY_DATE: Final = "date"
FAMILY_TIMESTAMP: Final = "timestamp"
FAMILY_TIME: Final = "time"
FAMILY_BLOB: Final = "blob"
FAMILY_OTHER: Final = "other"

FAMILIES: Final = frozenset(
    {
        FAMILY_INTEGER,
        FAMILY_DECIMAL,
        FAMILY_FLOAT,
        FAMILY_TEXT,
        FAMILY_BOOLEAN,
        FAMILY_DATE,
        FAMILY_TIMESTAMP,
        FAMILY_TIME,
        FAMILY_BLOB,
        FAMILY_OTHER,
    }
)

#: Relation kinds. Views matter: they resolve the traps declared in the glossary
#: (`v_attempt_dedup`, `v_payment_intent`), so the catalog must flag them for the
#: generator to prefer the right view over the raw table.
KIND_TABLE: Final = "table"
KIND_VIEW: Final = "view"


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """A column of the live schema."""

    name: str
    engine_type: str
    family: str
    nullable: bool
    ordinal: int
    #: `False` if `docs/spec/policy.yaml` excludes it from the ring 1 catalog. It is
    #: still listed because the schema describes what exists; publishing is a
    #: separate decision.
    published: bool = True
    #: `True` if `docs/spec/catalog-overlay.yaml` marks it deprecated. The engine
    #: cannot know this: the column still exists but should no longer be used.
    deprecated: bool = False
    deprecated_reason: str | None = None
    #: The `base_table.column` sources of this column: itself for a base table, all
    #: sources for a view column. This keeps `v_customer.full_name`
    #: (`concat(first_name, last_name_1)`) from escaping a policy that matches only
    #: on `table.column`.
    derives_from: tuple[str, ...] = ()
    #: `False` when sqlglot could not trace lineage (in practice, a recursive CTE)
    #: and `derives_from` holds the dependency closure instead of the exact source.
    #: Published so the limitation is visible.
    lineage_resolved: bool = True

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "engine_type": self.engine_type,
            "family": self.family,
            "nullable": self.nullable,
            "ordinal": self.ordinal,
            "published": self.published,
            "deprecated": self.deprecated,
            "derives_from": list(self.derives_from),
            "lineage_resolved": self.lineage_resolved,
        }
        if self.deprecated_reason is not None:
            out["deprecated_reason"] = self.deprecated_reason
        return out


@dataclass(frozen=True, slots=True)
class TableSpec:
    """A table or view of the live schema."""

    name: str
    kind: str
    columns: tuple[ColumnSpec, ...]

    def column(self, name: str) -> ColumnSpec | None:
        """The column with that name, or `None`. Case-insensitive."""
        lowered = name.lower()
        for col in self.columns:
            if col.name.lower() == lowered:
                return col
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "columns": [c.to_dict() for c in self.columns],
        }


@dataclass(frozen=True, slots=True)
class CatalogSchema:
    """The full schema as it exists in the engine.

    Input to `sqlglot.optimizer.qualify.qualify()`: once the tree is qualified
    against it, aliases, nested CTEs and `SELECT *` are already resolved and the
    guard does not need to chase them.
    """

    dialect: str
    tables: tuple[TableSpec, ...]

    def table(self, name: str) -> TableSpec | None:
        lowered = name.lower()
        for table in self.tables:
            if table.name.lower() == lowered:
                return table
        return None

    @property
    def table_names(self) -> tuple[str, ...]:
        return tuple(t.name for t in self.tables)

    def published(self) -> CatalogSchema:
        """The ring 1 catalog: without the columns the policy excludes.

        Computed rather than stored, so the policy and the published catalog cannot
        drift apart when only one file is regenerated.
        """
        return CatalogSchema(
            dialect=self.dialect,
            tables=tuple(
                TableSpec(
                    name=t.name,
                    kind=t.kind,
                    columns=tuple(c for c in t.columns if c.published),
                )
                for t in self.tables
            ),
        )

    def sqlglot_schema(self) -> dict[str, object]:
        """The `{table: {column: type}}` map consumed by `qualify()`.

        Built from the full schema, not the published one: without excluded columns
        `qualify()` could not attribute them, and the guard would reject them as
        unknown columns instead of for the actual reason.
        """
        out: dict[str, object] = {
            t.name: {c.name: c.engine_type for c in t.columns} for t in self.tables
        }
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "dialect": self.dialect,
            "tables": [t.to_dict() for t in self.tables],
        }


def from_dict(payload: dict[str, Any]) -> CatalogSchema:
    """Rebuild a `CatalogSchema` from the generated JSON."""
    return CatalogSchema(
        dialect=payload["dialect"],
        tables=tuple(
            TableSpec(
                name=t["name"],
                kind=t["kind"],
                columns=tuple(
                    ColumnSpec(
                        name=c["name"],
                        engine_type=c["engine_type"],
                        family=c["family"],
                        nullable=c["nullable"],
                        ordinal=c["ordinal"],
                        published=c.get("published", True),
                        deprecated=c.get("deprecated", False),
                        deprecated_reason=c.get("deprecated_reason"),
                        derives_from=tuple(c.get("derives_from", ())),
                        lineage_resolved=c.get("lineage_resolved", True),
                    )
                    for c in t["columns"]
                ),
            )
            for t in payload["tables"]
        ),
    )
