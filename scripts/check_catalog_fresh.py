#!/usr/bin/env python
"""`G-CATALOG-FRESH`: el catálogo generado coincide con el esquema vivo.

`PROJECT.md` §2 exige «catálogo autogenerado desde el esquema real, no escrito a
mano» y no daba forma de comprobarlo. Esto la da: se regenera el catálogo AHORA
contra el motor y se compara byte a byte con el que está en el árbol. Cero
divergencias.

**Por qué byte a byte y no «que tenga las mismas tablas».** Porque la comparación
laxa es la que deja pasar el caso real: alguien añade una columna sensible, el
esquema cambia, el fichero no, y la política sigue sin tener fila para ella. El
sha es lo único que no se puede cumplir a medias.

Comprueba además las tres cosas que hacen que el catálogo signifique algo:

- que valida contra `docs/spec/catalog.schema.json` (la forma es un contrato);
- que TODA clave de `docs/spec/policy.yaml` existe en el catálogo (I-07: las
  claves de la política son un subconjunto exacto del catálogo generado);
- que toda columna anotada en `docs/spec/catalog-overlay.yaml` existe: una
  anotación sobre una columna que ya no está es una anotación que nadie ejercita.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import jsonschema

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from datawarden.catalog import (  # noqa: E402
    SCHEMA_PATH,
    load_generated,
    to_json,
    unknown_columns,
)
from datawarden.catalog.build import generate  # noqa: E402
from gatelib import record  # noqa: E402

DEFAULT_DATABASE = ROOT / "datagen" / "out" / "cierzo-dev.duckdb"
CATALOG_CONTRACT = ROOT / "docs" / "spec" / "catalog.schema.json"
POLICY_JSON = ROOT / "src" / "datawarden" / "principal" / "generated" / "policy.json"
OVERLAY_JSON = ROOT / "src" / "datawarden" / "catalog" / "generated" / "overlay.json"
REPORT = ROOT / "evals" / "reports" / "arch-checks.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=str(DEFAULT_DATABASE))
    args = parser.parse_args()

    problems: list[str] = []

    if not SCHEMA_PATH.exists():
        print(
            "check_catalog_fresh: FALLO · no hay catálogo generado.\n"
            "  El catálogo NO se escribe a mano (I-07): `uv run warden catalog build`."
        )
        return 1

    on_disk = SCHEMA_PATH.read_text(encoding="utf-8")

    # 1 · La forma es un contrato.
    contract = json.loads(CATALOG_CONTRACT.read_text(encoding="utf-8"))
    try:
        jsonschema.validate(json.loads(on_disk), contract)
    except jsonschema.ValidationError as exc:
        problems.append(f"no valida contra catalog.schema.json: {exc.message}")

    # 2 · Se regenera y se compara. Es el corazón de la meta.
    database = pathlib.Path(args.database)
    if not database.exists():
        problems.append(
            f"no existe {database}: sin esquema vivo no se puede comprobar la "
            "frescura del catálogo, y dar por bueno el fichero porque no hay contra "
            "qué compararlo es exactamente la señal verde falsa que el Makefile "
            "prohíbe. Genera el dataset con `make dataset PROFILE=dev`."
        )
    else:
        regenerated = to_json(generate(database))
        if regenerated != on_disk:
            problems.append(
                "el catálogo del árbol NO coincide con el esquema vivo. Alguien "
                "cambió el esquema y no regeneró, o editó el fichero a mano. "
                "Ejecuta `uv run warden catalog build` y revisa el diff: si aparece "
                "una columna nueva, hay que clasificarla en docs/spec/policy.yaml."
            )

    schema = load_generated(SCHEMA_PATH)

    # 3 · I-07: las claves de la política son subconjunto del catálogo.
    policy = json.loads(POLICY_JSON.read_text(encoding="utf-8"))
    missing = unknown_columns(schema, policy["columns"])
    if missing:
        problems.append(
            f"{len(missing)} columnas de policy.yaml NO existen en el catálogo: "
            f"{missing[:6]}. Una política que protege una columna que ya no existe "
            "da una falsa sensación de cobertura, y nada la ejercita nunca."
        )

    # 4 · Lo mismo para el overlay.
    overlay = json.loads(OVERLAY_JSON.read_text(encoding="utf-8"))
    missing_overlay = unknown_columns(schema, overlay["deprecated"])
    if missing_overlay:
        problems.append(
            f"columnas anotadas en catalog-overlay.yaml que no existen: {missing_overlay}"
        )

    unresolved = [
        f"{t.name}.{c.name}" for t in schema.tables for c in t.columns if not c.lineage_resolved
    ]

    record(
        "arch-checks.json",
        "G-CATALOG-FRESH",
        value=len(problems),
        detail={
            "tables": len(schema.tables),
            "columns": sum(len(t.columns) for t in schema.tables),
            "unpublished_columns": sum(
                1 for t in schema.tables for c in t.columns if not c.published
            ),
            "lineage_unresolved": unresolved,
            "policy_columns": len(policy["columns"]),
            "problems": problems,
        },
        command="python scripts/check_catalog_fresh.py",
    )

    if problems:
        print("check_catalog_fresh: FALLO · G-CATALOG-FRESH\n")
        for p in problems:
            print(f"  · {p}")
        return 1

    print(
        f"check_catalog_fresh: ok · {len(schema.tables)} relaciones, "
        f"{sum(len(t.columns) for t in schema.tables)} columnas, "
        f"{len(policy['columns'])} clasificadas, "
        f"{len(unresolved)} con linaje sin resolver (declaradas)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
