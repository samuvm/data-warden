#!/usr/bin/env python
"""En `guard/` la unidad no es la función: **es el caso.** `docs/RULES.md §3`.

Una regla tiene una única función pública —`check`—, así que «un test por función»
quedaría satisfecho por un solo test y no probaría nada. La regla específica es:

> Toda regla registrada tiene >= 3 casos `accept` y >= 3 casos `reject` en
> `tests/unit/guard/cases/<rule_id>.yaml`, y **cada caso `reject` asierta el
> `rule_id` exacto que disparó**, no solo que hubo rechazo.

Lo segundo es lo que impide el fallo silencioso más común del proyecto: un caso que
se cree cubierto por R008 y que en realidad para R002 por accidente. El día que R002
cambie, R008 tiene un agujero y nadie se entera.

**Y se exige además que cada regla sea la que dispara en al menos tres casos de todo
el corpus.** Sin eso, una regla podría tener seis casos en su fichero y no haber
rechazado nunca nada: existiría sin defender nada.
"""

from __future__ import annotations

import pathlib
import sys
from collections import Counter

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from datawarden.guard.registry import BY_ID
from gatelib import ROOT, record

CASES = ROOT / "tests" / "unit" / "guard" / "cases"
MIN_CASES = 3


def main() -> int:
    problems: list[str] = []
    fires = Counter[str]()
    per_rule: dict[str, dict[str, int]] = {}

    for rule_id in sorted(BY_ID):
        path = CASES / f"{rule_id}.yaml"
        if not path.exists():
            problems.append(f"{rule_id}: no existe {path.relative_to(ROOT)}")
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        accepts = data.get("accept") or []
        rejects = data.get("reject") or []
        per_rule[rule_id] = {"accept": len(accepts), "reject": len(rejects)}
        if len(accepts) < MIN_CASES:
            problems.append(f"{rule_id}: {len(accepts)} casos accept y hacen falta {MIN_CASES}")
        if len(rejects) < MIN_CASES:
            problems.append(f"{rule_id}: {len(rejects)} casos reject y hacen falta {MIN_CASES}")
        for case in rejects:
            declared = case.get("rule_id")
            if not declared:
                problems.append(
                    f"{rule_id}/{case.get('id')}: el caso reject no declara qué regla "
                    "dispara. Un rechazo sin regla nombrada es un acierto por casualidad"
                )
                continue
            if declared not in BY_ID:
                problems.append(
                    f"{rule_id}/{case.get('id')}: declara {declared}, que no existe"
                )
                continue
            fires[declared] += 1

    for rule_id in sorted(BY_ID):
        if fires[rule_id] < MIN_CASES:
            problems.append(
                f"{rule_id} dispara en solo {fires[rule_id]} casos de TODO el corpus y "
                f"hacen falta {MIN_CASES}: una regla que nunca rechaza nada existe sin "
                "defender nada"
            )

    record(
        "attack-dev.json",
        "G-COV-RULE",
        value=len(problems),
        detail={"per_rule": per_rule, "fires": dict(fires), "problems": problems},
        command="python scripts/check_rule_coverage.py",
    )

    if problems:
        print("check_rule_coverage: FALLO\n")
        for p in problems:
            print(f"  · {p}")
        return 1
    total = sum(v["accept"] + v["reject"] for v in per_rule.values())
    print(
        f"check_rule_coverage: ok · {len(per_rule)} reglas, {total} casos, "
        f"y cada regla dispara en >= {MIN_CASES} de ellos"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
