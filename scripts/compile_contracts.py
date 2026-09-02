#!/usr/bin/env python
"""Compila los contratos YAML firmados a los artefactos JSON que consume `src/`.

DOS MOTIVOS, Y NINGUNO ES ESTÉTICO.

**1 · El dominio no parsea YAML.** `PyYAML` no está en `[project.dependencies]`:
entra en el entorno como dependencia TRANSITIVA de `langgraph` y de
`detect-secrets`, y `uv add` está en `ask`. Un módulo de `src/` que importara algo
no declarado sería exactamente la clase de dependencia invisible que rompe un
despliegue seis meses después. En `scripts/` es otra cosa: son herramientas del
constructor, corren en el entorno de desarrollo y `check_gate_config.py` ya lo
hacía. La propuesta de declararla está en `docs/PARA-SAMUEL.md` como **P-002**.

**2 · El guard está en el camino crítico de cada consulta.** `G-GUARD-P95` exige
p95 <= 25 ms. Parsear tres ficheros YAML para saber si `dim_customer.email` está
enmascarada para `analyst` es tiempo tirado en el peor sitio posible. El JSON
compilado se carga una vez y se indexa por `tabla.columna`.

Lo que este script NO hace: decidir nada. Traduce. Si el YAML dice `deny`, el JSON
dice `deny`; y `scripts/check_contracts.py` comprueba que el sha del origen
coincide con el que quedó grabado en el artefacto, para que un YAML editado sin
recompilar sea un fallo de gate y no una divergencia silenciosa.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = ROOT / "docs" / "spec"
PRINCIPAL_GEN = ROOT / "src" / "datawarden" / "principal" / "generated"
CATALOG_GEN = ROOT / "src" / "datawarden" / "catalog" / "generated"

ROLES = ("admin", "analyst", "finance", "ops")
LEVELS = ("allow", "mask", "deny")


def sha256_of(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: pathlib.Path, payload: dict[str, Any]) -> None:
    """Escritura canónica: claves ordenadas, UTF-8 y salto final.

    Determinista por obligación: estos ficheros se comparan por sha256 y un
    `json.dumps` sin `sort_keys` cambia de sha entre versiones de Python.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load(path: pathlib.Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


def require_signed(name: str, data: dict[str, Any], problems: list[str]) -> None:
    if data.get("estado") != "FIRMADO":
        problems.append(
            f"{name}: estado es {data.get('estado')!r} y no FIRMADO. Un contrato sin "
            "firmar es una propuesta, y compilar una propuesta la convierte en hecho "
            "consumado sin que nadie lo haya decidido."
        )


def compile_policy(problems: list[str]) -> dict[str, Any]:
    source = SPEC / "policy.yaml"
    data = load(source)
    require_signed("policy.yaml", data, problems)

    columns: dict[str, Any] = {}
    for row in data["columnas"]:
        ref = str(row["columna"]).lower()
        if ref in columns:
            problems.append(f"policy.yaml: {ref} aparece dos veces")
        levels = {}
        for role in ROLES:
            level = row.get(role)
            if level not in LEVELS:
                problems.append(f"policy.yaml: {ref} · {role} = {level!r}, no es un nivel")
            levels[role] = level
        columns[ref] = {
            "levels": levels,
            "data_type": row.get("tipo_dato"),
            "generalized": (row.get("generalizada") or None),
            "transformation": row.get("transformacion"),
            "keep_last_n": row.get("n"),
            "derived_from": [str(d).lower() for d in (row.get("derivada_de") or [])],
            "admin_exception": bool(row.get("excepcion_admin", False)),
            "published_in_catalog": bool(row.get("catalogo_publicado", True)),
        }

    excluded = [
        str(item["columna"]).lower()
        for item in (data.get("columnas_excluidas_del_catalogo") or [])
    ]
    # Coherencia entre las dos formas de decir lo mismo: la lista de nivel superior
    # y el campo por fila. Si divergen, el catálogo publicaría una columna que la
    # matriz cree excluida, y nadie lo vería hasta que apareciese en una respuesta.
    by_row = {ref for ref, spec in columns.items() if not spec["published_in_catalog"]}
    if by_row != set(excluded):
        problems.append(
            "policy.yaml: `columnas_excluidas_del_catalogo` dice "
            f"{sorted(excluded)} y las filas con `catalogo_publicado: false` dicen "
            f"{sorted(by_row)}. Tienen que decir lo mismo."
        )

    return {
        "version": data["version"],
        "source": "docs/spec/policy.yaml",
        "source_sha256": sha256_of(source),
        "signed_by": data.get("firmado_por"),
        "signed_on": str(data.get("firmado_el")),
        "roles": list(ROLES),
        "levels": list(LEVELS),
        "default_level": "allow",
        "deterministic_masking": bool(data["enmascarado"]["determinista"]),
        "pepper_from": data["enmascarado"]["pimienta_desde"],
        "forbidden_positions_in_mask": list(data["posiciones_prohibidas_en_mask"]),
        "excluded_from_catalog": sorted(excluded),
        "columns": columns,
    }


def compile_budgets(problems: list[str]) -> dict[str, Any]:
    source = SPEC / "budgets.yaml"
    data = load(source)
    require_signed("budgets.yaml", data, problems)
    roles: dict[str, Any] = {}
    for role in ROLES:
        row = data["roles"][role]
        if row["soft_gb"] > row["hard_gb"]:
            problems.append(
                f"budgets.yaml: {role} tiene soft_gb ({row['soft_gb']}) por encima de "
                f"hard_gb ({row['hard_gb']}). Un aviso que salta después del rechazo "
                "no avisa de nada."
            )
        roles[role] = {
            "soft_bytes": round(row["soft_gb"] * data["unidad"]["base"]),
            "hard_bytes": round(row["hard_gb"] * data["unidad"]["base"]),
            "soft_gb": row["soft_gb"],
            "hard_gb": row["hard_gb"],
            "max_rows": int(row["max_rows"]),
            "soft_is_calibrated": row.get("origen_soft") != "agente_por_calibrar",
        }
    return {
        "version": data["version"],
        "source": "docs/spec/budgets.yaml",
        "source_sha256": sha256_of(source),
        "signed_by": data.get("firmado_por"),
        "unit_base": data["unidad"]["base"],
        "measures": data["unidad"]["que_se_mide"],
        "roles": roles,
    }


def compile_glossary(problems: list[str]) -> dict[str, Any]:
    source = SPEC / "glossary.yaml"
    data = load(source)
    require_signed("glossary.yaml", data, problems)
    return {
        "version": data["version"],
        "source": "docs/spec/glossary.yaml",
        "source_sha256": sha256_of(source),
        "reviewed_by": data.get("revisado_por"),
        "domain": data["dominio"],
        "grains": data["granos"],
        "critical_definitions": data["definiciones_criticas"],
        "metrics": data["metricas"],
        "tables": data["tablas"],
        "traps": data["trampas"],
    }


def compile_overlay(problems: list[str]) -> dict[str, Any]:
    source = SPEC / "catalog-overlay.yaml"
    data = load(source)
    require_signed("catalog-overlay.yaml", data, problems)
    return {
        "version": data["version"],
        "source": "docs/spec/catalog-overlay.yaml",
        "source_sha256": sha256_of(source),
        "default_level": data["clasificacion_por_defecto"]["nivel"],
        "deprecated": {
            str(row["columna"]).lower(): {
                "reason": row["motivo"],
                "use_instead": row["usar_en_su_lugar"],
            }
            for row in data["deprecadas"]
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="no escribe: falla si lo compilado no coincide con el YAML de origen",
    )
    args = parser.parse_args()

    problems: list[str] = []
    artefacts = {
        PRINCIPAL_GEN / "policy.json": compile_policy(problems),
        PRINCIPAL_GEN / "budgets.json": compile_budgets(problems),
        CATALOG_GEN / "glossary.json": compile_glossary(problems),
        CATALOG_GEN / "overlay.json": compile_overlay(problems),
    }

    if problems:
        print("compile_contracts: FALLO\n")
        for p in problems:
            print(f"  · {p}")
        return 1

    stale: list[str] = []
    for path, payload in artefacts.items():
        rendered = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != rendered:
                stale.append(str(path.relative_to(ROOT)))
        else:
            dump(path, payload)

    if stale:
        print("compile_contracts --check: FALLO · artefactos desincronizados\n")
        for s in stale:
            print(f"  · {s}")
        print("\n  Alguien editó un contrato de docs/spec/ y no recompiló. Ejecuta:")
        print("      uv run python scripts/compile_contracts.py")
        return 1

    verb = "verificados" if args.check else "compilados"
    print(f"compile_contracts: ok · {len(artefacts)} artefactos {verb}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
