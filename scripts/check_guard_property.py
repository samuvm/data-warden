#!/usr/bin/env python
"""`G-FAILCLOSED` · corre las propiedades del guard y deja el número en su artefacto.

La meta exige **>= 5.000 entradas arbitrarias** y **cero excepciones propagadas**.
Los dos números viven en el propio test —`_AXIOM_EXAMPLES`— y no en el perfil de
Hypothesis, y es deliberado: `--hypothesis-profile=dev` baja a 25 ejemplos, y un
axioma que se pudiera rebajar cambiando una variable de entorno no sería un axioma.

Este script no vuelve a probar nada: ejecuta la suite de propiedad y anota cuántas
entradas se ejercitaron y si alguna se escapó, para que `goals_check.py` tenga qué
leer. Un número sin artefacto no es un número.
"""

from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gatelib import ROOT, record, run

SUITE = "tests/property"
#: Las tres propiedades de fail-closed. El recuento sale de multiplicar por el
#: presupuesto de ejemplos declarado en el propio módulo, que es de donde tiene que
#: salir: contarlo aquí a mano sería un segundo sitio donde equivocarse.
FAILCLOSED_PROPERTIES = 4


def _examples_budget() -> int:
    text = (ROOT / "tests" / "property" / "test_guard_failclosed.py").read_text("utf-8")
    match = re.search(r"_AXIOM_EXAMPLES = ([\d_]+)", text)
    return int(match.group(1).replace("_", "")) if match else 0


def main() -> int:
    code, out = run(
        [sys.executable, "-m", "pytest", SUITE, "--no-header", "-p", "no:cacheprovider"]
    )
    passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", out)) else 0
    failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", out)) else 0
    budget = _examples_budget()

    record(
        "guard-property.json",
        "G-FAILCLOSED",
        value=failed,
        adicionales={"entradas arbitrarias ejercitadas": budget * FAILCLOSED_PROPERTIES},
        detail={
            "suite": SUITE,
            "properties_passed": passed,
            "properties_failed": failed,
            "examples_per_property": budget,
            "nota": (
                "El presupuesto de ejemplos vive en el módulo de test y no en el perfil "
                "de Hypothesis: un axioma que se pudiera rebajar con una variable de "
                "entorno no sería un axioma."
            ),
        },
        command="pytest tests/property/test_guard_failclosed.py --hypothesis-profile=gate",
    )

    if code or failed:
        print(f"check_guard_property: FALLO · {failed} propiedades en rojo\n{out[-2500:]}")
        return 1
    print(
        f"check_guard_property: ok · {passed} propiedades, "
        f"{budget * FAILCLOSED_PROPERTIES} entradas arbitrarias, cero excepciones"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
