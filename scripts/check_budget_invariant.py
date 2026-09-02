#!/usr/bin/env python
"""`G-BUDGET-ESCAPE` · corre las dos mitades y deja el número en su artefacto.

La meta tiene dos partes y hacen falta las dos: **el invariante por contador** —cero
consultas caras llegan al motor, medido mirando si el motor se movió— y **el número
por reloj**: el rechazo de una consulta de gigabytes en <= 200 ms. El reloj es lo que
convierte «el código dice que no ejecuta» en evidencia, porque escanear 3 GB no cabe
en 200 ms ni en el mejor disco.
"""

from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gatelib import ROOT, record, run

PROPERTY = "tests/property/test_budget_invariant.py"
WALLCLOCK = "tests/integration/test_budget_wallclock.py"


def main() -> int:
    code_p, out_p = run(
        [sys.executable, "-m", "pytest", PROPERTY, "--hypothesis-profile=gate", "--no-header"]
    )
    code_w, out_w = run([sys.executable, "-m", "pytest", WALLCLOCK, "--no-header"])

    passed = sum(
        int(m.group(1)) for m in (re.search(r"(\d+) passed", o) for o in (out_p, out_w)) if m
    )
    failed = sum(
        int(m.group(1)) for m in (re.search(r"(\d+) failed", o) for o in (out_p, out_w)) if m
    )

    wall_ms = _measure_wallclock()
    record(
        "budget-invariant.json",
        "G-BUDGET-ESCAPE",
        value=failed,
        adicionales={"rechazo por reloj de una consulta cara sobre 3 GB (ms)": wall_ms},
        detail={
            "property_suite": PROPERTY,
            "wallclock_suite": WALLCLOCK,
            "tests_passed": passed,
            "tests_failed": failed,
            "como_se_mide": (
                "Contador de proceso en engines.base: la propiedad exige delta == 0 "
                "cuando el coste estimado supera el presupuesto. No se mira el "
                "veredicto, se mira si el motor se movió."
            ),
        },
        command=(
            "pytest tests/property/test_budget_invariant.py "
            "&& pytest tests/integration/test_budget_wallclock.py"
        ),
    )

    if code_p or code_w or failed:
        print(f"check_budget_invariant: FALLO\n{out_p[-1500:]}\n{out_w[-1500:]}")
        return 1
    print(
        f"check_budget_invariant: ok · {passed} tests, invariante por contador en verde, "
        f"rechazo por reloj en {wall_ms:.1f} ms (<= 200)"
    )
    return 0


def _measure_wallclock() -> float:
    """Vuelve a cronometrar el rechazo, aparte de pytest, para dejar el número.

    Medirlo dentro del test daría el tiempo del test —con sus fixtures y su
    recolección— y no el del rechazo. Un número de latencia mide una cosa concreta o
    no mide nada.
    """
    import time

    sys.path.insert(0, str(ROOT / "src"))
    from datawarden.catalog import SCHEMA_PATH, load_generated
    from datawarden.catalog.statistics import load as load_stats
    from datawarden.cost import STATISTICS_PATH
    from datawarden.cost.screen import screen
    from datawarden.domain.types import Principal, Role, RoleSource
    from datawarden.principal import BUDGETS_PATH, POLICY_PATH
    from datawarden.principal.budgets import load_budgets
    from datawarden.principal.policy import load_policy

    schema = load_generated(SCHEMA_PATH)
    policy = load_policy(POLICY_PATH)
    budgets = load_budgets(BUDGETS_PATH)
    stats = load_stats(STATISTICS_PATH)
    principal = Principal(id="reloj", role=Role.OPS, source=RoleSource.CLI_FLAG)

    muestras = []
    for _ in range(20):
        inicio = time.perf_counter()
        screen(
            "SELECT * FROM fact_payment_attempt",
            principal=principal,
            schema=schema,
            policy=policy,
            budgets=budgets,
            stats=stats,
        )
        muestras.append((time.perf_counter() - inicio) * 1000.0)
    muestras.sort()
    return round(muestras[len(muestras) // 2], 3)


if __name__ == "__main__":
    sys.exit(main())
