"""Anillo 1 · el contexto. El catálogo se genera; nunca se escribe a mano (I-07).

Lo que este paquete produce —`generated/schema.json`— es a la vez el recurso MCP
de solo lectura del anillo 1 y la entrada de `sqlglot.optimizer.qualify.qualify()`
del anillo 3. Que sean el mismo artefacto no es economía: es lo que garantiza que
el modelo razone sobre exactamente el mismo esquema contra el que se le valida.
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

#: Dónde vive el catálogo generado. Un solo sitio, para que `make done`, el guard
#: y el servidor MCP no puedan mirar tres ficheros distintos.
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
