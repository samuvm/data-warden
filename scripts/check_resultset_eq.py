#!/usr/bin/env python
"""`G-RESULTSET-EQ`: los casos de la comparación de resultsets, verdes y contados.

La meta pide >= 12 casos y CERO en rojo. Lo que hace este script es dejar el número
en su artefacto: sin él, `goals_check.py` no tendría nada que leer y la meta sería
una promesa. Es la diferencia entre «la suite pasó» y «la meta se midió».

No reimplementa la comparación: ejecuta la suite que la especifica y cuenta.
"""

from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gatelib import record, run

SUITE = "tests/unit/evalsupport"


def main() -> int:
    # SIN `-q`: `addopts` de pyproject ya lo lleva, y dos `-q` seguidos suprimen la
    # línea de resumen que este script necesita leer. Es la clase de detalle que
    # deja un número en cero sin que nada falle.
    code, out = run(
        [sys.executable, "-m", "pytest", SUITE, "--no-header", "-p", "no:cacheprovider"]
    )
    passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", out)) else 0
    failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", out)) else 0

    record(
        "arch-checks.json",
        "G-RESULTSET-EQ",
        value=passed,
        adicionales={"casos en rojo": failed},
        detail={"suite": SUITE, "exit_code": code},
        command=f"pytest {SUITE} -q",
    )
    if code or failed:
        print(f"check_resultset_eq: FALLO · {failed} casos en rojo\n{out[-2000:]}")
        return 1
    print(
        f"check_resultset_eq: ok · {passed} casos de docs/spec/resultset-equality.md en verde"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
