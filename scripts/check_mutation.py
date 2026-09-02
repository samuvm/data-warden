#!/usr/bin/env python
"""`G-MUTATION` y `G-MUT-GUARD` · **lo único que distingue cobertura de verificación.**

Es el mecanismo 5 de anti-gaming y el punto 6 de la Definition of Done, y su valor
está en una asimetría: un test que EJECUTA una línea la cubre; solo un test que
FALLA cuando esa línea cambia la verifica. La cobertura se puede fingir sin querer
—basta con importar el módulo—; la mutación no.

Dos umbrales, y son distintos a propósito. `guard/rules` exige **85 %**, porque ahí
vive la tesis del proyecto; el resto de los paquetes `testable`, **70 %**. Los dos
números viven en `docs/GOALS.yaml`, sellados por `thresholds.lock`, y `[tool.gate]`
de `pyproject.toml` los copia bajo la vigilancia de I-16.

## Cómo se cuenta, dicho antes de dar el número

    puntuacion = muertos / (muertos + supervivientes + sospechosos + timeouts + sin_tests)

**«Sin tests» cuenta como NO muerto**, y es la cuenta pesimista a propósito: un
mutante que ningún test toca es exactamente el hueco que esta meta existe para
encontrar. Excluirlos subiría el número sin que nada mejorase.
"""

from __future__ import annotations

import collections
import pathlib
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gatelib import ROOT, record, run

#: Lo que cuenta como muerto. El resto —superviviente, sospechoso, timeout y sin
#: tests— cuenta como vivo.
KILLED = "killed"

GUARD_RULES_PREFIX = "datawarden.guard.rules."


def parse_results() -> dict[str, collections.Counter[str]]:
    """`modulo -> {estado: cuantos}`, leído de `mutmut results --all`."""
    mutmut = str(pathlib.Path(sys.executable).parent / "mutmut")
    code, out = run([mutmut, "results", "--all", "true"])
    if code:
        message = f"`mutmut results` falló:\n{out[-2000:]}"
        raise RuntimeError(message)

    per_module: dict[str, collections.Counter[str]] = collections.defaultdict(
        collections.Counter
    )
    for raw in out.splitlines():
        line = raw.strip()
        if ": " not in line or not line.startswith("datawarden."):
            continue
        name, _, status = line.rpartition(": ")
        module = name
        for marker in (".x_", ".x__", ".xǁ"):
            if marker in module:
                module = module.split(marker)[0]
        per_module[module][status.strip()] += 1
    return per_module


def score(counter: collections.Counter[str]) -> tuple[float, int, int]:
    """`(puntuación, muertos, total)`. Un conjunto vacío no está al 0 %."""
    total = sum(counter.values())
    killed = counter.get(KILLED, 0)
    if total == 0:
        return (100.0, 0, 0)
    return (round(100.0 * killed / total, 2), killed, total)


def main() -> int:
    gate = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["gate"]
    floor_general = float(gate["mutantes_muertos_min"])
    floor_guard = float(gate["mutantes_muertos_min_guard"])

    if not (ROOT / "mutants").exists():
        print(
            "check_mutation: FALLO · no hay resultados de mutación.\n"
            "  Se generan con `mutmut run`, que `make mutation` ejecuta. Un check que\n"
            "  aprueba porque no encuentra su medida es una señal verde falsa."
        )
        return 1

    per_module = parse_results()
    guard_rules: collections.Counter[str] = collections.Counter()
    rest: collections.Counter[str] = collections.Counter()
    for module, counter in per_module.items():
        if module.startswith(GUARD_RULES_PREFIX):
            guard_rules.update(counter)
        else:
            rest.update(counter)

    guard_score, guard_killed, guard_total = score(guard_rules)
    rest_score, rest_killed, rest_total = score(rest)

    detail_per_module = {
        module: {
            "score": score(counter)[0],
            "killed": counter.get(KILLED, 0),
            "total": sum(counter.values()),
            **dict(sorted(counter.items())),
        }
        for module, counter in sorted(per_module.items())
    }

    record(
        "mutation.json",
        "G-MUT-GUARD",
        value=guard_score,
        detail={
            "scope": "src/datawarden/guard/rules",
            "killed": guard_killed,
            "total": guard_total,
            "floor": floor_guard,
        },
        command="make mutation",
    )
    record(
        "mutation.json",
        "G-MUTATION",
        value=rest_score,
        detail={
            "scope": "el resto de los paquetes de [tool.gate].testable",
            "killed": rest_killed,
            "total": rest_total,
            "floor": floor_general,
            "per_module": detail_per_module,
            "como_se_cuenta": (
                "muertos / (muertos + supervivientes + sospechosos + timeouts + sin "
                "tests). «Sin tests» cuenta como NO muerto a propósito: es el hueco "
                "que la meta existe para encontrar."
            ),
            "limite_declarado": (
                "tests/property y tests/integration quedan fuera de la pasada. La "
                "propiedad corre 5.000 ejemplos por axioma y multiplicarlo por 3.238 "
                "mutantes son horas; la integración necesita los 7,1 GB del dataset, "
                "que mutmut no copia a su árbol de trabajo. Los mutantes que solo "
                "esas suites matan salen como «sin tests» y bajan el número: es un "
                "número peor que el real, y es preferible a inflarlo."
            ),
        },
        command="make mutation",
    )

    print(
        f"  guard/rules   {guard_score:6.2f} %  ({guard_killed}/{guard_total})  "
        f">= {floor_guard:.0f}"
    )
    print(
        f"  resto         {rest_score:6.2f} %  ({rest_killed}/{rest_total})  "
        f">= {floor_general:.0f}"
    )
    print("\n  peores módulos:")
    for module, spec in sorted(detail_per_module.items(), key=lambda kv: kv[1]["score"])[:8]:
        print(f"    {spec['score']:6.2f} %  {spec['killed']:>4}/{spec['total']:<5} {module}")

    problems = []
    if guard_score < floor_guard:
        problems.append(f"G-MUT-GUARD {guard_score:.2f} % < {floor_guard:.0f} %")
    if rest_score < floor_general:
        problems.append(f"G-MUTATION {rest_score:.2f} % < {floor_general:.0f} %")
    if problems:
        print("\ncheck_mutation: FALLO · " + " · ".join(problems))
        return 1
    print("\ncheck_mutation: ok · los tests verifican, no solo cubren")
    return 0


if __name__ == "__main__":
    sys.exit(main())
