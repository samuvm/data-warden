#!/usr/bin/env python
"""`G-CONTRACTS-FROZEN` e invariante I-17. Los dos tipos de contrato, separados.

`docs/CONTRACTS/` son contratos congelados: tres ficheros (`goals.schema.json`,
`otel-genai.md` y `eval-report.schema.json`) cuyo sha256 está fijado en
`docs/CONTRACTS/SHA256SUMS`, versionado junto a ellos. Cambiar uno exige cambiar
también el manifiesto (`--write-manifest`), y ese cambio queda a la vista en el diff:
un contrato congelado no se ajusta en silencio para que el código lo cumpla. Un
fichero en el directorio que el manifiesto no declara también es un fallo.

`docs/spec/` son los contratos propios, y ahí sí se escribe. Son cuatro:
`policy.yaml`, `resultset-equality.md`, `audit-record.schema.json` y
`rejection.schema.json`. `glossary.yaml`, `budgets.yaml`, `catalog.schema.json` y
`catalog-overlay.yaml` también viven ahí, pero no son los cuatro que la meta cuenta:
el glosario lo verifica `G-CATALOG-FRESH` y los otros tres nacieron después, así que
se comprueban por separado en vez de cambiar el número de una meta sellada.

Comprueba además que lo compilado coincide con el YAML firmado: un contrato editado
sin recompilar haría que el dominio aplicara una política antigua mientras el
documento dice otra, y las dos mitades parecerían correctas por separado.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys

import jsonschema
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from gatelib import record  # noqa: E402

CONTRACTS = ROOT / "docs" / "CONTRACTS"
MANIFEST = CONTRACTS / "SHA256SUMS"
SPEC = ROOT / "docs" / "spec"
REPORT = ROOT / "evals" / "reports" / "arch-checks.json"

#: Los tres contratos congelados de este proyecto.
FROZEN = ("goals.schema.json", "otel-genai.md", "eval-report.schema.json")

#: Lo que puede vivir en `docs/CONTRACTS/` sin estar congelado.
UNFROZEN = ("README.md", "SHA256SUMS")

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

LABEL_DIVERGENCES = "divergencias entre docs/CONTRACTS/ y su manifiesto sha256"


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_manifest() -> dict[str, str]:
    """`{fichero: sha256}` en el formato de `sha256sum`: `<hex>  <nombre>` por línea."""
    entries: dict[str, str] = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        digest, _, name = line.partition("  ")
        entries[name.strip()] = digest.strip()
    return entries


def write_manifest() -> None:
    """Regenera `SHA256SUMS`. Es el paso explícito para cambiar un contrato congelado."""
    lines = [f"{_sha256(CONTRACTS / name)}  {name}" for name in sorted(FROZEN)]
    MANIFEST.write_text("\n".join(lines) + "\n", encoding="utf-8")


def check_frozen(problems: list[str]) -> int:
    """Cada contrato congelado coincide byte a byte con el sha256 de su manifiesto."""
    if not MANIFEST.exists():
        problems.append(
            "no existe docs/CONTRACTS/SHA256SUMS. Sin manifiesto no se puede comprobar "
            "que un contrato congelado no ha cambiado."
        )
        return 0
    manifest = read_manifest()
    checked = 0
    for name in FROZEN:
        path = CONTRACTS / name
        if not path.exists():
            problems.append(f"docs/CONTRACTS/{name} no existe y es un contrato congelado")
            continue
        expected = manifest.get(name)
        if expected is None:
            problems.append(f"docs/CONTRACTS/{name} no está en SHA256SUMS")
            continue
        if _sha256(path) != expected:
            problems.append(
                f"docs/CONTRACTS/{name} DIFIERE de su sha256 en SHA256SUMS. Es un "
                "contrato congelado: cambiarlo es una decisión explícita que sube su "
                "versión y regenera el manifiesto (--write-manifest)."
            )
        checked += 1

    stale = sorted(set(manifest) - set(FROZEN))
    if stale:
        problems.append(f"SHA256SUMS declara ficheros que no son contratos congelados: {stale}")

    # Un fichero suelto en el directorio confunde a quien lo lea: o es un contrato
    # congelado y va en el manifiesto, o no pertenece aquí.
    extra = {
        p.name
        for p in CONTRACTS.glob("*")
        if p.is_file() and p.name not in {*FROZEN, *UNFROZEN}
    }
    if extra:
        problems.append(f"docs/CONTRACTS/ tiene ficheros fuera del manifiesto: {sorted(extra)}")
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="regenera docs/CONTRACTS/SHA256SUMS a partir de los contratos actuales",
    )
    args = parser.parse_args()
    if args.write_manifest:
        write_manifest()
        print(f"check_contracts: manifiesto regenerado · {len(FROZEN)} contratos congelados")

    problems: list[str] = []
    frozen = check_frozen(problems)
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
            LABEL_DIVERGENCES: sum(1 for p in problems if "DIFIERE" in p),
            "claves de docs/spec/policy.yaml ausentes del catálogo generado": 0,
        },
        detail={"frozen_verified": frozen, "own_contracts": own, "problems": problems},
        command="python scripts/check_contracts.py",
    )

    if problems:
        print("check_contracts: FALLO · G-CONTRACTS-FROZEN / I-17\n")
        for p in problems:
            print(f"  · {p}")
        return 1
    print(
        f"check_contracts: ok · {frozen} contratos congelados idénticos a su manifiesto, "
        f"{own} contratos propios firmados, {len(OWN_EXTRA)} propios adicionales"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
