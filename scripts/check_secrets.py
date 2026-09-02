#!/usr/bin/env python
"""`G-SECRETS`: cero hallazgos NUEVOS. Es un AXIOMA y no se negocia.

En un proyecto cuya tesis es la garantía sobre el fallo, filtrar una credencial de
Athena o del proveedor de LLM invalida el argumento entero. Por eso
`propuesta_admisible: false`: no hay umbral estadístico que discutir.

**La línea base NO se regenera para silenciar un hallazgo; se resuelve el
hallazgo.** Lo que hay dentro hoy son cuatro falsos positivos auditados uno a uno y
anotados en `JOURNAL.md`; regenerarla para tapar un quinto sería exactamente el
atajo que la constitución llama anti-gaming.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gatelib import ROOT, record, run

BASELINE = ROOT / ".secrets.baseline"


def main() -> int:
    if not BASELINE.exists():
        print("check_secrets: FALLO · no existe .secrets.baseline")
        record("secrets.json", "G-SECRETS", value=1, detail={"error": "sin línea base"})
        return 1

    code, out = run(
        [sys.executable, "-m", "detect_secrets", "scan", "--baseline", ".secrets.baseline"]
    )
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    known = sum(len(v) for v in baseline["results"].values())

    record(
        "secrets.json",
        "G-SECRETS",
        value=0 if code == 0 else 1,
        detail={
            "baseline_findings": known,
            "baseline_files": sorted(baseline["results"]),
            "new_findings": code != 0,
        },
        command="make secrets",
    )
    if code:
        print(f"check_secrets: FALLO · hallazgos NUEVOS\n{out[-2000:]}")
        return 1
    print(f"check_secrets: ok · 0 hallazgos nuevos · {known} en la línea base, todos auditados")
    return 0


if __name__ == "__main__":
    sys.exit(main())
