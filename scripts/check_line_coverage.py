#!/usr/bin/env python
"""`G-COV-LINE`: 90 % en los paquetes testable, 95 % en los cuatro críticos.

Los dos números viven en `docs/GOALS.yaml`, que está sellado por
`thresholds.lock`; `[tool.gate]` de `pyproject.toml` los copia y
`scripts/check_gate_config.py` comprueba que no divergen (I-16). Aquí solo se mide.

**Se mide por paquete y no en global.** Un global del 90 % se cumple con
`evalsupport` al 100 % y `guard` al 60 %, que es exactamente al revés de lo que
importa: la cobertura tiene que estar donde vive la tesis del proyecto. Por eso
`guard`, `mask`, `audit` y `principal` se miden aparte y contra el 95 %.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gatelib import ROOT, record

COVERAGE_JSON = ROOT / "evals" / "reports" / "coverage-contexts.json"


def percent(covered: int, total: int) -> float:
    """Un paquete sin líneas no está al 0 %: no tiene nada que cubrir todavía."""
    return 100.0 if total == 0 else round(100.0 * covered / total, 2)


def main() -> int:
    gate = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["gate"]
    minimum = float(gate["cobertura_linea_min"])
    minimum_critical = float(gate["cobertura_linea_min_critica"])
    critical = set(gate["paquetes_criticos"])

    if not COVERAGE_JSON.exists():
        print(
            "check_line_coverage: FALLO · falta la medida. Ejecuta `make coverage`.\n"
            "  Un check que aprueba porque no encuentra su medida es una señal verde falsa."
        )
        return 1

    data = json.loads(COVERAGE_JSON.read_text(encoding="utf-8"))
    files = data.get("files", {})

    by_package: dict[str, list[int]] = {p: [0, 0] for p in gate["testable"]}
    for relative, entry in files.items():
        parts = pathlib.PurePosixPath(relative).parts
        if len(parts) < 3 or parts[0] != "src" or parts[1] != "datawarden":
            continue
        package = parts[2]
        if package not in by_package:
            continue
        summary = entry["summary"]
        by_package[package][0] += summary["covered_lines"]
        by_package[package][1] += summary["num_statements"]

    failures: list[str] = []
    rows: dict[str, float] = {}
    worst_general = 100.0
    worst_critical = 100.0
    for package, (covered, total) in sorted(by_package.items()):
        value = percent(covered, total)
        rows[package] = value
        threshold = minimum_critical if package in critical else minimum
        if package in critical:
            worst_critical = min(worst_critical, value)
        else:
            worst_general = min(worst_general, value)
        if total and value < threshold:
            failures.append(f"{package}: {value:.2f} % < {threshold:.0f} % ({covered}/{total})")

    record(
        "coverage.json",
        "G-COV-LINE",
        value=worst_general,
        adicionales={"guard/, mask/, audit/, principal/ (%)": worst_critical},
        detail={"por_paquete": rows, "umbral": minimum, "umbral_critico": minimum_critical},
        command="make coverage && python scripts/check_line_coverage.py",
    )

    for package, value in sorted(rows.items()):
        mark = "crítico" if package in critical else ""
        print(f"  {package:14s} {value:6.2f} %  {mark}")

    if failures:
        print("\ncheck_line_coverage: FALLO · G-COV-LINE\n")
        for f in failures:
            print(f"  · {f}")
        return 1
    print(
        f"\ncheck_line_coverage: ok · peor paquete general {worst_general:.2f} % "
        f"(>= {minimum:.0f}), peor crítico {worst_critical:.2f} % (>= {minimum_critical:.0f})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
