"""Genera `catalog/generated/schema.json` desde el esquema vivo.

Es la única forma de escribir ese fichero, y por eso mismo es un módulo y no un
script suelto: `warden catalog build` y `scripts/check_catalog_fresh.py` tienen que
producir EXACTAMENTE el mismo texto, porque el segundo comprueba el sha del
primero. Dos rutas de código que generan «lo mismo» acaban generando dos cosas.

Las anotaciones que el motor no puede dar —columnas excluidas de la publicación y
columnas obsoletas— llegan ya compiladas a JSON por `scripts/compile_contracts.py`.
El dominio no parsea YAML: ver el encabezado de ese script y la propuesta P-002.
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
    """El esquema vivo más las dos anotaciones que el motor no conoce."""
    policy = _load_json(POLICY_PATH)
    overlay = _load_json(OVERLAY_PATH)
    return introspect_duckdb(
        database,
        excluded_columns=policy["excluded_from_catalog"],
        deprecated_columns={k: v["reason"] for k, v in overlay["deprecated"].items()},
    )


def write(schema: CatalogSchema, path: pathlib.Path = SCHEMA_PATH) -> str:
    """Escribe el catálogo y devuelve el texto exacto que quedó en disco."""
    rendered = to_json(schema)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")
    return rendered
