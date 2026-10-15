"""Generate `catalog/generated/schema.json` from the live schema.

This is the only writer of that file. It is a module rather than a script so that
`warden catalog build` and `scripts/check_catalog_fresh.py` produce byte-identical
text; the latter checks the former's sha.

Annotations the engine cannot provide (columns excluded from publication and
deprecated columns) arrive precompiled to JSON by `scripts/compile_contracts.py`;
the domain does not parse YAML.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from datawarden.catalog import OVERLAY_PATH, SCHEMA_PATH
from datawarden.catalog.introspect import introspect_duckdb, to_json
from datawarden.catalog.types import CatalogSchema
from datawarden.principal import POLICY_PATH


def _load_json(path: pathlib.Path) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


def generate(database: pathlib.Path) -> CatalogSchema:
    """Live schema plus the two annotations the engine does not know about."""
    policy = _load_json(POLICY_PATH)
    overlay = _load_json(OVERLAY_PATH)
    return introspect_duckdb(
        database,
        excluded_columns=policy["excluded_from_catalog"],
        deprecated_columns={k: v["reason"] for k, v in overlay["deprecated"].items()},
    )


def write(schema: CatalogSchema, path: pathlib.Path = SCHEMA_PATH) -> str:
    """Write the catalog and return the exact text written to disk."""
    rendered = to_json(schema)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")
    return rendered
