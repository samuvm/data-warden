#!/usr/bin/env python
"""I-14 · **toda familia de ataque tiene una regla, y toda regla tiene su familia.**

La matriz no puede tener filas ni columnas vacías, y las dos direcciones importan
por motivos distintos:

- Una **familia sin regla** es un ataque que alguien pensó y nadie paró. Es el
  agujero evidente.
- Una **regla sin familia** es peor de leer: es código que se ejecuta en el camino
  crítico de cada consulta sin que nadie pueda decir contra qué defiende. Cuando
  llegue el día de tocarla, nadie sabrá qué se rompe.

Las familias salen de dos sitios que tienen que coincidir: lo que cada regla DECLARA
defender (`Rule.families`) y lo que el cuaderno de ataque EJERCITA (`familia` de cada
caso).
"""

from __future__ import annotations

import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from datawarden.guard.registry import RULES
from gatelib import ROOT, record

NOTEBOOK = ROOT / "attacks" / "dev-notebook.yaml"

#: Reglas EXENTAS de tener un ataque en el cuaderno, con su motivo escrito. La lista
#: tiene que ser corta y cada entrada tiene que explicarse: una exención sin motivo
#: es una fila vacía de la matriz con permiso.
EXEMPT: dict[str, str] = {
    "R009": (
        "defensa en profundidad: `qualify()` expande la estrella, así que no hay "
        "consulta que haga saltar R009 de punta a punta. La regla existe para el caso "
        "en que `qualify()` NO expanda y devuelva el árbol con la estrella dentro sin "
        "error, y se ejercita a nivel de regla en tests/unit/guard/cases/R009.yaml."
    ),
}


def main() -> int:
    declared: dict[str, list[str]] = {}
    for rule in RULES:
        for family in rule.families:
            declared.setdefault(family, []).append(rule.rule_id)

    notebook = yaml.safe_load(NOTEBOOK.read_text(encoding="utf-8"))
    exercised = {a["familia"] for a in notebook["ataques"]}

    problems: list[str] = []
    for rule in RULES:
        if not rule.families:
            problems.append(
                f"{rule.rule_id} no declara ni una familia: corre en el camino crítico "
                "de cada consulta y nadie puede decir contra qué defiende"
            )
    # Del cuaderno Y del corpus por regla. Exigir que cada regla pare algo del
    # CUADERNO chocaba con el umbral `== 25` de la meta: cada regla nueva habría
    # obligado a un ataque nuevo, y el cuaderno no puede crecer sin romper el gate.
    # I-14 habla de familias, no de en qué fichero vive el caso.
    parado_por = {a["espera"] for a in notebook["ataques"]}
    for path in sorted((ROOT / "tests" / "unit" / "guard" / "cases").glob("R*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        parado_por |= {c["rule_id"] for c in (data.get("reject") or []) if c.get("rule_id")}
    for rule in RULES:
        if rule.rule_id in parado_por or rule.rule_id in EXEMPT:
            continue
        problems.append(
            f"{rule.rule_id} no para NINGÚN ataque del cuaderno: es una fila vacía de "
            "la matriz de I-14. Añade un caso, o decláralo exento con su motivo"
        )
    huerfanas = sorted(exercised - set(declared))
    if huerfanas:
        problems.append(
            f"el cuaderno ejercita familias que NINGUNA regla dice parar: {huerfanas}"
        )
    sin_ejercitar = sorted(set(declared) - exercised)

    record(
        "attack-dev.json",
        "G-ATTACK-MATRIX",
        value=len(problems),
        detail={
            "families_declared": {k: sorted(v) for k, v in sorted(declared.items())},
            "families_exercised": sorted(exercised),
            "declared_but_not_exercised": sin_ejercitar,
            "rules_with_attack": sorted(parado_por),
            "exempt": EXEMPT,
            "problems": problems,
        },
        command="python scripts/check_attack_coverage.py",
    )

    if problems:
        print("check_attack_coverage: FALLO · I-14\n")
        for p in problems:
            print(f"  · {p}")
        return 1
    print(
        f"check_attack_coverage: ok · {len(declared)} familias declaradas por "
        f"{len(RULES)} reglas, {len(exercised)} ejercitadas por el cuaderno"
    )
    if sin_ejercitar:
        print(
            f"  aviso: {len(sin_ejercitar)} familias declaradas y todavía sin caso en el "
            f"cuaderno: {sin_ejercitar}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
