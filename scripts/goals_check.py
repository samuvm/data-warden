#!/usr/bin/env python
"""Toda meta bloqueante en esta fase pasa su umbral. Punto 7 de la DoD.

**No vuelve a medir nada.** Lee los artefactos que dejaron los `check_*.py` y las
suites, y compara contra `docs/GOALS.yaml`. Es deliberado y es lo que hace el gate
auditable: los números quedan en `evals/reports/` con su comando al lado, y
cualquiera puede mirarlos después sin fiarse de que el proceso salió en verde.

**Una meta bloqueante sin medida es un FALLO, nunca un aprobado.** Es la regla del
Makefile llevada al gate: un gate que aprueba porque no encuentra nada que medir da
una señal verde falsa, y eso es peor que no tener gate.

**Una propuesta de bajar umbral sobre un axioma es por sí misma un fallo de gate.**
`propuesta_admisible: false` marca las ocho metas que no se negocian; si aparece una
propuesta `bajar-umbral` sobre cualquiera de ellas en `docs/PARA-SAMUEL.md`, esto
sale rojo y lo registra, sin mirar siquiera el número.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gatelib import ROOT, compare, read_meta

GOALS = ROOT / "docs" / "GOALS.yaml"
MAILBOX = ROOT / "docs" / "PARA-SAMUEL.md"


def axiom_violations(goals: dict[str, object]) -> list[str]:
    """Propuestas `bajar-umbral` sobre metas con `propuesta_admisible: false`.

    Tres precauciones, y las tres las obligó un falso positivo real: el propio buzón
    lleva al final una PLANTILLA con la línea `Tipo: bajar-umbral | cambiar-meta |
    ...` y el párrafo que enumera los ocho axiomas. Sin filtrarlo, el gate declaraba
    ocho intentos de bajar un axioma que nadie había hecho — y un gate que grita sin
    causa se acaba desactivando, que es peor que uno que no grita.

    1. Se descartan los bloques de código (la plantilla vive dentro de uno).
    2. Solo cuenta un `Tipo:` cuyo valor sea EXACTAMENTE `bajar-umbral`, no la lista
       de opciones separadas por `|`.
    3. Y solo si el bloque nombra el axioma en su campo `Afecta a:`, que es donde la
       constitución dice que va la meta afectada.
    """
    axioms = {
        m["id"]
        for m in goals["metas"]  # type: ignore[index]
        if not m.get("propuesta_admisible", True)
    }
    if not MAILBOX.exists():
        return []
    text = re.sub(r"```.*?```", "", MAILBOX.read_text(encoding="utf-8"), flags=re.DOTALL)
    found: list[str] = []
    for block in re.split(r"^#{2,3} PROPUESTA ", text, flags=re.MULTILINE)[1:]:
        tipo = re.search(r"^\*{0,2}Tipo:?\*{0,2}\s*(.+)$", block, flags=re.MULTILINE)
        if tipo is None or tipo.group(1).strip().split()[0] != "bajar-umbral":
            continue
        afecta = re.search(r"^\*{0,2}Afecta a:?\*{0,2}\s*(.+)$", block, flags=re.MULTILINE)
        alcance = afecta.group(1) if afecta else block
        found.extend(
            f"la propuesta `{block.splitlines()[0].strip()}` pide bajar {axiom}, que "
            "es un AXIOMA (`propuesta_admisible: false`). El intento es por sí mismo "
            "un fallo de gate: cero es cero y no hay umbral que negociar."
            for axiom in sorted(axioms)
            if axiom in alcance
        )
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--milestone", type=int, required=True)
    args = parser.parse_args()

    goals = yaml.safe_load(GOALS.read_text(encoding="utf-8"))
    problems: list[str] = list(axiom_violations(goals))
    evaluated: list[dict[str, object]] = []

    for meta in goals["metas"]:
        blocking_from = meta.get("bloqueante_desde_fase")
        if blocking_from is None or blocking_from > args.milestone:
            continue
        artifact = pathlib.PurePosixPath(meta["artefacto"]).name
        measured = read_meta(artifact, meta["id"])
        if measured is None:
            problems.append(
                f"{meta['id']} bloquea desde la fase {blocking_from} y NO tiene medida "
                f"en evals/reports/{artifact}. Se mide con: {meta['comando']}. "
                "Una meta bloqueante sin medida es un fallo, nunca un aprobado."
            )
            continue

        umbral = meta["umbral"]
        ok = compare(umbral["operador"], float(measured["value"]), float(umbral["valor"]))
        detail: list[str] = []
        if not ok:
            detail.append(
                f"{measured['value']} {umbral['operador']} {umbral['valor']} es falso"
            )
        for extra in umbral.get("adicionales") or []:
            label = extra["etiqueta"]
            got = measured.get("adicionales", {}).get(label)
            if got is None:
                detail.append(f"falta el umbral adicional «{label}»")
                continue
            if not compare(extra["operador"], float(got), float(extra["valor"])):
                detail.append(f"«{label}»: {got} {extra['operador']} {extra['valor']} es falso")
        if detail:
            problems.append(f"{meta['id']} · " + " · ".join(detail))
        evaluated.append(
            {
                "id": meta["id"],
                "blocking_from": blocking_from,
                "measured": measured["value"],
                "threshold": umbral["valor"],
                "operator": umbral["operador"],
                "unit": umbral["unidad"],
                "passed": not detail,
                "axiom": not meta.get("propuesta_admisible", True),
            }
        )

    summary = ROOT / "evals" / "reports" / "goals-check.json"
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text(
        json.dumps(
            {
                "milestone": args.milestone,
                "evaluated": evaluated,
                "problems": problems,
                "passed": not problems,
            },
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    for row in evaluated:
        mark = "ok  " if row["passed"] else "FALLO"
        axiom = " · AXIOMA" if row["axiom"] else ""
        print(
            f"  {mark} {row['id']:20s} {row['measured']!s:>10s} "
            f"{row['operator']} {row['threshold']} {row['unit']}{axiom}"
        )

    if problems:
        print(f"\ngoals_check: FALLO · {len(problems)} problemas en la fase {args.milestone}\n")
        for p in problems:
            print(f"  · {p}")
        return 1
    print(
        f"\ngoals_check: ok · {len(evaluated)} metas bloqueantes hasta la fase "
        f"{args.milestone}, todas por encima de su umbral"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
