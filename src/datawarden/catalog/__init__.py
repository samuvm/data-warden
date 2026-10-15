"""Ring 1: context. The catalog is generated, never hand-written (I-07).

`generated/schema.json` is both the read-only MCP resource of ring 1 and the
input to `sqlglot.optimizer.qualify.qualify()` in ring 3. Using one artifact for
both guarantees the model reasons over exactly the schema it is validated against.
"""

from __future__ import annotations

import pathlib
from typing import Final

from datawarden.catalog.introspect import (
    ColumnRow,
    build_schema,
    family_of,
    load_generated,
    to_json,
    unknown_columns,
)
from datawarden.catalog.types import (
    CatalogSchema,
    ColumnSpec,
    TableSpec,
    from_dict,
)

#: Single location of the generated catalog, so `make done`, the guard and the
#: MCP server cannot read three different files.
GENERATED_DIR: Final = pathlib.Path(__file__).resolve().parent / "generated"
SCHEMA_PATH: Final = GENERATED_DIR / "schema.json"
GLOSSARY_PATH: Final = GENERATED_DIR / "glossary.json"
OVERLAY_PATH: Final = GENERATED_DIR / "overlay.json"

__all__ = [
    "GENERATED_DIR",
    "GLOSSARY_PATH",
    "OVERLAY_PATH",
    "SCHEMA_PATH",
    "CatalogSchema",
    "ColumnRow",
    "ColumnSpec",
    "TableSpec",
    "build_schema",
    "family_of",
    "from_dict",
    "load_generated",
    "to_json",
    "unknown_columns",
]
