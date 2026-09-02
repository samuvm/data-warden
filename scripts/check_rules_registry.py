#!/usr/bin/env python
"""I-01 · **Ninguna regla se borra, se debilita ni se retira. Nunca.**

Y un `rule_id` **no se reutiliza jamás**. Es la invariante que impide el atajo más
tentador de todo el proyecto: el día que una regla estorbe, quitarla y que la suite
pase. Con esto, quitarla es un fallo de gate; retirarla exige un ADR y una propuesta
aprobada, y el id queda como `RETIRED` con sus casos migrados.

Falla si, respecto del registro guardado:

- **desaparece un `rule_id`** — la regla dejó de existir;
- **baja `n_reject_cases`** — la regla sigue ahí y prueba menos;
- **un caso migra de `reject` a `accept`** — la regla sigue ahí, prueba lo mismo, y
  lo que antes se rechazaba ahora se acepta. Es la forma más silenciosa de las tres
  y la que ninguna cuenta de casos detectaría.

El registro vive en `.claude/state/rules-registry.json`, que **lo escribe el gate**
y el agente tiene en `deny`. Aquí se compara; escribirlo es cosa de `make done`.
"""

from __future__ import annotations

import json
import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from datawarden.guard.registry import RULES
from gatelib import ROOT, record

REGISTRY = ROOT / ".claude" / "state" / "rules-registry.json"
CASES = ROOT / "tests" / "unit" / "guard" / "cases"


def snapshot() -> dict[str, dict[str, object]]:
    """El estado actual de las catorce reglas y de sus casos."""
    out: dict[str, dict[str, object]] = {}
    for rule in RULES:
        path = CASES / f"{rule.rule_id}.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
        rejects = data.get("reject") or []
        out[rule.rule_id] = {
            "code": rule.code,
            "severity": str(rule.severity),
            "families": sorted(rule.families),
            "n_accept_cases": len(data.get("accept") or []),
            "n_reject_cases": len(rejects),
            "reject_ids": sorted(c["id"] for c in rejects),
        }
    return out


def main() -> int:
    current = snapshot()
    problems: list[str] = []

    if REGISTRY.exists():
        previous = json.loads(REGISTRY.read_text(encoding="utf-8"))["rules"]
        for rule_id, before in previous.items():
            after = current.get(rule_id)
            if after is None:
                problems.append(
                    f"{rule_id} HA DESAPARECIDO del registro de reglas. Retirar una "
                    "regla exige ADR y propuesta aprobada, y el id queda como RETIRED "
                    "con sus casos migrados. Nunca se borra"
                )
                continue
            if after["n_reject_cases"] < before["n_reject_cases"]:
                problems.append(
                    f"{rule_id}: los casos reject han bajado de "
                    f"{before['n_reject_cases']} a {after['n_reject_cases']}. Una regla "
                    "que prueba menos es una regla debilitada"
                )
            perdidos = set(before["reject_ids"]) - set(after["reject_ids"])
            if perdidos:
                problems.append(
                    f"{rule_id}: casos reject que ya no están: {sorted(perdidos)}. Si "
                    "alguno pasó a `accept`, eso es lo que antes se rechazaba y ahora "
                    "se acepta, que es la forma más silenciosa de debilitar una regla"
                )
            if after["code"] != before["code"]:
                problems.append(
                    f"{rule_id}: el `code` ha cambiado de {before['code']} a "
                    f"{after['code']}. El code agrupa métricas históricas: cambiarlo "
                    "parte la serie en dos sin que nada avise"
                )

    record(
        "attack-dev.json",
        "G-RULES-REGISTRY",
        value=len(problems),
        detail={"rules": current, "problems": problems, "registry_exists": REGISTRY.exists()},
        command="python scripts/check_rules_registry.py",
    )

    if problems:
        print("check_rules_registry: FALLO · I-01\n")
        for p in problems:
            print(f"  · {p}")
        return 1
    estado = "comparado con el registro" if REGISTRY.exists() else "sin registro previo todavía"
    print(f"check_rules_registry: ok · {len(current)} reglas, {estado}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
