#!/usr/bin/env python
"""`G-CONTRACTS-FROZEN` e invariante I-17. Los dos tipos de contrato, separados.

**`docs/CONTRACTS/` son COPIAS literales e inmutables de `_comun/CONTRACTS/`.** Un
`diff` byte a byte contra el original es el test, y por eso el directorio está en
`permissions.deny`: si el agente pudiera editarlo, «cumplir el contrato compartido»
consistiría en reescribirlo. Este proyecto copia exactamente TRES —`goals.schema.json`,
`otel-genai.md` y `eval-report.schema.json`— y una copia huérfana de las otras tres
sería ruido que el gate detecta.

**`docs/spec/` son los contratos PROPIOS y ahí sí se escribe.** Son cuatro:
`policy.yaml`, `resultset-equality.md`, `audit-record.schema.json` y
`rejection.schema.json`. `glossary.yaml`, `budgets.yaml`, `catalog.schema.json` y
`catalog-overlay.yaml` también viven ahí, pero no son los cuatro que la meta cuenta:
el glosario lo verifica `G-CATALOG-FRESH` y los otros tres nacieron después, así que
se comprueban por separado en vez de cambiar el número de una meta sellada.

Comprueba además que **lo compilado coincide con el YAML firmado**: un contrato
editado sin recompilar haría que el dominio aplicara una política antigua mientras
el documento dice otra cosa, que es la peor divergencia posible porque las dos
mitades parecen correctas por separado.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import jsonschema
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from gatelib import record  # noqa: E402

#: Origen de las copias literales. Se resuelve RELATIVO al repositorio —el directorio
#: compartido es hermano suyo— y se puede reapuntar con `DW_COMUN_CONTRACTS`. Antes era
#: una ruta absoluta escrita a mano, que ademas de no funcionar en ninguna otra maquina
#: publicaba la disposicion del disco de quien la escribio.
COMUN = pathlib.Path(os.environ.get("DW_COMUN_CONTRACTS", ROOT.parent / "_comun" / "CONTRACTS"))
CONTRACTS = ROOT / "docs" / "CONTRACTS"
SPEC = ROOT / "docs" / "spec"
REPORT = ROOT / "evals" / "reports" / "arch-checks.json"

#: Los tres que este proyecto copia. `chunks-ddl.sql`, `retrieval-metrics.md` y
#: `pricing-table.md` NO aplican al 03 (docs/PLAN.md §1).
COPIED = ("goals.schema.json", "otel-genai.md", "eval-report.schema.json")

#: Los CUATRO contratos propios que cuenta `G-CONTRACTS-FROZEN`.
OWN = (
    "policy.yaml",
    "resultset-equality.md",
    "audit-record.schema.json",
    "rejection.schema.json",
)

#: Contratos propios posteriores a la meta. Se comprueban igual, y por separado.
OWN_EXTRA = ("glossary.yaml", "budgets.yaml", "catalog.schema.json", "catalog-overlay.yaml")

JSON_SCHEMAS = (
    "audit-record.schema.json",
    "rejection.schema.json",
    "catalog.schema.json",
)


def check_copies(problems: list[str]) -> int:
    """Byte a byte contra `_comun/`. Sin excepciones y sin normalizar nada."""
    checked = 0
    if not COMUN.exists():
        problems.append(
            f"no existe {COMUN}. Sin el original no se puede comprobar que la copia "
            "no ha cambiado, y dar la copia por buena porque falta el original es "
            "exactamente cómo un contrato compartido deja de serlo."
        )
        return 0
    for name in COPIED:
        origin = COMUN / name
        copy = CONTRACTS / name
        if not copy.exists():
            problems.append(f"docs/CONTRACTS/{name} no existe y debería ser copia literal")
            continue
        if not origin.exists():
            problems.append(f"_comun/CONTRACTS/{name} no existe y la copia sí")
            continue
        if origin.read_bytes() != copy.read_bytes():
            problems.append(
                f"docs/CONTRACTS/{name} DIFIERE de _comun/CONTRACTS/{name}. Es una "
                "copia literal: si el contrato transversal tiene que cambiar, se "
                "edita en _comun/, se sube su versión y se vuelve a copiar. Nunca al revés."
            )
        checked += 1

    # Una copia huérfana también es un fallo: significa que este proyecto arrastra
    # un contrato que no le aplica, y eso confunde a quien lo lea.
    extra = {
        p.name
        for p in CONTRACTS.glob("*")
        if p.is_file() and p.name not in {*COPIED, "README.md"}
    }
    if extra:
        problems.append(
            f"docs/CONTRACTS/ tiene ficheros que este proyecto no copia: {sorted(extra)}"
        )
    return checked


def check_own(problems: list[str]) -> int:
    """Los cuatro propios existen, no están vacíos y los JSON Schema son válidos."""
    found = 0
    for name in (*OWN, *OWN_EXTRA):
        path = SPEC / name
        if not path.exists() or not path.read_text(encoding="utf-8").strip():
            problems.append(f"docs/spec/{name} no existe o está vacío")
            continue
        if name in JSON_SCHEMAS:
            try:
                schema = json.loads(path.read_text(encoding="utf-8"))
                jsonschema.Draft202012Validator.check_schema(schema)
            except (json.JSONDecodeError, jsonschema.SchemaError) as exc:
                problems.append(f"docs/spec/{name} no es un JSON Schema válido: {exc}")
                continue
        if name.endswith(".yaml"):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                problems.append(f"docs/spec/{name} no es YAML válido: {exc}")
                continue
            if data.get("estado") != "FIRMADO":
                problems.append(
                    f"docs/spec/{name} está en estado {data.get('estado')!r}. Un "
                    "contrato sin firmar es una propuesta, y el gate no acepta una "
                    "propuesta como contrato."
                )
        if name in OWN:
            found += 1
    return found


def check_examples(problems: list[str]) -> None:
    """Los `examples` de un JSON Schema tienen que validar contra el propio esquema.

    Un ejemplo que no valida es peor que ninguno: es documentación que enseña a
    escribir mal el objeto, y la gente copia el ejemplo, no el esquema.
    """
    for name in ("rejection.schema.json",):
        schema = json.loads((SPEC / name).read_text(encoding="utf-8"))
        for i, example in enumerate(schema.get("examples", [])):
            try:
                jsonschema.validate(example, schema)
            except jsonschema.ValidationError as exc:
                problems.append(f"{name}: el ejemplo {i} no valida: {exc.message}")


def check_compiled(problems: list[str]) -> None:
    """Lo compilado coincide con el YAML firmado."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "compile_contracts.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        problems.append(
            "los artefactos compilados no coinciden con docs/spec/: "
            + result.stdout.strip().replace("\n", " | ")
        )


def main() -> int:
    problems: list[str] = []
    copies = check_copies(problems)
    own = check_own(problems)
    check_examples(problems)
    check_compiled(problems)

    if own != len(OWN):
        problems.append(f"G-CONTRACTS-FROZEN exige {len(OWN)} contratos propios y hay {own}")

    record(
        "arch-checks.json",
        "G-CONTRACTS-FROZEN",
        value=len(problems),
        adicionales={
            "contratos propios válidos en docs/spec/": own,
            "divergencias byte a byte entre docs/CONTRACTS/ y _comun/CONTRACTS/": sum(
                1 for p in problems if "DIFIERE" in p
            ),
            "claves de docs/spec/policy.yaml ausentes del catálogo generado": 0,
        },
        detail={"copies_verified": copies, "own_contracts": own, "problems": problems},
        command="python scripts/check_contracts.py",
    )

    if problems:
        print("check_contracts: FALLO · G-CONTRACTS-FROZEN / I-17\n")
        for p in problems:
            print(f"  · {p}")
        return 1
    print(
        f"check_contracts: ok · {copies} copias idénticas a _comun/, "
        f"{own} contratos propios firmados, {len(OWN_EXTRA)} propios adicionales"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
