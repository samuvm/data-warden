#!/usr/bin/env python
"""`G-GUARD-P95` · el guard no es el cuello de botella. **Se mide, no se supone.**

El protocolo está en `docs/GOALS.yaml` y se sigue literalmente: **corpus de 300
consultas, 50 calentamientos, 500 medidas, se descarta la primera.** Escribirlo en
la meta y no en el script es lo que impide que el número mejore cambiando cómo se
mide en vez de cambiando el código.

**El número solo significa algo con el hardware declarado** (`hardware_referencia`
de `GOALS.yaml`, y D-03: un solo proyecto encendido cada vez). Un p95 medido con
otro proyecto compitiendo por la memoria no vale, y por eso el informe guarda la
máquina y el aviso al lado del número.

El máximo absoluto —250 ms— **no baja nunca**, porque es a la vez el timeout de
fail-closed: pasado ese punto el guard rechaza en vez de seguir pensando.
"""

from __future__ import annotations

import pathlib
import platform
import statistics
import sys
import time

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from datawarden.catalog import SCHEMA_PATH, load_generated
from datawarden.domain.types import Principal, Role, RoleSource
from datawarden.guard.validator import validate
from datawarden.principal import BUDGETS_PATH, POLICY_PATH
from datawarden.principal.budgets import load_budgets
from datawarden.principal.policy import load_policy
from gatelib import ROOT, record

CORPUS_SIZE = 300
WARMUPS = 50
MEASUREMENTS = 500
CASES_DIR = ROOT / "tests" / "unit" / "guard" / "cases"


def build_corpus() -> list[tuple[Role, str]]:
    """300 consultas REALES: las del corpus del guard y las del cuaderno de ataque.

    Medir sobre `SELECT 1` daría un p95 excelente y no diría nada. Aquí entran las
    aceptadas y las rechazadas, porque el guard paga el coste de decidir en los dos
    casos y en producción va a ver los dos.
    """
    corpus: list[tuple[Role, str]] = []
    for path in sorted(CASES_DIR.glob("R*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for kind in ("accept", "reject"):
            for case in data.get(kind) or []:
                if "sql_generado" in case:
                    # Las bombas de AST se excluyen del banco de latencia A
                    # PROPÓSITO: su coste es el del rechazo por tamaño, y meterlas
                    # movería el p95 por un caso que el guard corta en el primer paso.
                    continue
                corpus.append((Role(case["rol"]), case["sql"]))

    notebook = yaml.safe_load((ROOT / "attacks" / "dev-notebook.yaml").read_text("utf-8"))
    corpus.extend(
        (Role(a["rol"]), a["sql"]) for a in notebook["ataques"] if "sql_generado" not in a
    )

    # Se repite el corpus hasta 300 en vez de inventar consultas: repetir mide el
    # mismo trabajo más veces, e inventar mediría otra cosa.
    while len(corpus) < CORPUS_SIZE:
        corpus.extend(corpus[: CORPUS_SIZE - len(corpus)])
    return corpus[:CORPUS_SIZE]


def main() -> int:
    schema = load_generated(SCHEMA_PATH)
    policy = load_policy(POLICY_PATH)
    budgets = load_budgets(BUDGETS_PATH)
    corpus = build_corpus()
    principals = {r: Principal(id="bench", role=r, source=RoleSource.CLI_FLAG) for r in Role}

    def once(index: int) -> float:
        role, sql = corpus[index % len(corpus)]
        start = time.perf_counter()
        validate(
            sql,
            principal=principals[role],
            schema=schema,
            policy=policy,
            max_rows=budgets.max_rows(role),
        )
        return (time.perf_counter() - start) * 1000.0

    for i in range(WARMUPS):
        once(i)

    # Se descarta la PRIMERA medida, como dice el protocolo: es la que paga las
    # cachés frías que el calentamiento no llegó a llenar.
    samples = [once(i) for i in range(MEASUREMENTS + 1)][1:]
    samples.sort()

    p50 = statistics.median(samples)
    p95 = samples[int(0.95 * len(samples)) - 1]
    p99 = samples[int(0.99 * len(samples)) - 1]
    worst = samples[-1]

    record(
        "guard-latency.json",
        "G-GUARD-P95",
        value=round(p95, 3),
        adicionales={
            "p99 (ms)": round(p99, 3),
            "máximo absoluto (ms) · es además el timeout de fail-closed": round(worst, 3),
        },
        detail={
            "protocol": {
                "corpus": len(corpus),
                "warmups": WARMUPS,
                "measurements": MEASUREMENTS,
                "first_discarded": True,
            },
            "p50_ms": round(p50, 3),
            "p95_ms": round(p95, 3),
            "p99_ms": round(p99, 3),
            "max_ms": round(worst, 3),
            "machine": f"{platform.machine()} · {platform.system()} {platform.release()}",
            "python": platform.python_version(),
            "aviso": (
                "El número solo significa algo con el hardware de referencia declarado "
                "en GOALS.yaml y con UN SOLO proyecto encendido (D-03). Un p95 medido "
                "con otro proyecto compitiendo por la memoria no vale."
            ),
        },
        command="make bench-guard",
    )

    print(
        f"bench_guard: p50 {p50:.3f} ms · p95 {p95:.3f} ms · p99 {p99:.3f} ms · "
        f"máx {worst:.3f} ms"
    )
    print(
        f"  protocolo: corpus {len(corpus)}, {WARMUPS} calentamientos, "
        f"{MEASUREMENTS} medidas, primera descartada"
    )
    print(f"  máquina: {platform.machine()} · {platform.system()} {platform.release()}")
    problems = []
    if p95 > 25:
        problems.append(f"p95 {p95:.2f} ms > 25 ms")
    if p99 > 60:
        problems.append(f"p99 {p99:.2f} ms > 60 ms")
    if worst > 250:
        problems.append(f"máximo {worst:.2f} ms > 250 ms, que además es el timeout")
    if problems:
        print("\nbench_guard: FALLO · " + " · ".join(problems))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
