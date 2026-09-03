#!/usr/bin/env python
"""`G-ROLE-SPOOF` · el rol nunca viene de datos no autenticados. **AXIOMA, == 0.**

Corre la suite adversarial y deja el número en su artefacto. La suite es la medida;
esto es lo que la convierte en algo que `goals_check.py` pueda leer sin volver a
ejecutar nada.

**Qué cuenta como un caso, y por qué se cuenta así.** Un caso es una petición
envenenada —`_meta.role="admin"`, `arguments.role="admin"`, `scopes`, `principal`—
que consigue alterar el principal efectivo. La suite lo comprueba comparando **los
bytes de la respuesta** contra la de la petición limpia del mismo principal, no
mirando si existe una comprobación en el código: comprobar «hay una comprobación»
deja pasar el día que la comprobación mire la clave equivocada.

Cero no admite negociación: `propuesta_admisible: false`.
"""

from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gatelib import ROOT, record, run

SUITE = "tests/adversarial/test_role_spoofing.py"


def main() -> int:
    if not (ROOT / SUITE).exists():
        print(f"check_role_spoof: FALLO · no existe {SUITE}")
        record(
            "mcp-conformance.json",
            "G-ROLE-SPOOF",
            value=1.0,
            detail={"error": f"no existe {SUITE}"},
            command=f"pytest {SUITE}",
        )
        return 1

    # SIN `-q` EXTRA. `pyproject.toml` ya lo lleva en `addopts`, y añadir otro sube a
    # `-qq`, que **suprime la línea de resumen**: el contador salía 0 y el check
    # aprobaba igual por el código de salida. Un gate que aprueba porque no encuentra
    # nada que medir da una señal verde falsa, que es peor que no tener gate.
    code, out = run([sys.executable, "-m", "pytest", SUITE, "--no-header"])
    passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", out)) else 0
    failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", out)) else 0

    # Un fallo de la suite ES un caso de suplantación: el número que publica la meta
    # es «cuántas peticiones envenenadas alteraron el principal efectivo».
    spoofs = failed if failed else (0 if code == 0 else 1)

    # Y CERO TESTS EJECUTADOS NO ES CERO SUPLANTACIONES. Si la suite deja de
    # recolectar —un renombrado, un error de importación, un marcador mal puesto—,
    # el axioma saldría verde sobre nada.
    if passed == 0:
        print(f"check_role_spoof: FALLO · la suite no ejecutó ni un test.\n{out[-1500:]}")
        record(
            "mcp-conformance.json",
            "G-ROLE-SPOOF",
            value=1.0,
            detail={"suite": SUITE, "tests_passed": 0, "error": "0 tests recolectados"},
            command=f"pytest {SUITE}",
        )
        return 1

    record(
        "mcp-conformance.json",
        "G-ROLE-SPOOF",
        value=float(spoofs),
        detail={
            "suite": SUITE,
            "tests_passed": passed,
            "tests_failed": failed,
            "como_se_mide": (
                "se despacha por `dispatch()`, que es el `tools/call` del servidor, "
                "con `_meta` y `arguments` envenenados, y se exige que la respuesta "
                "sea BYTE A BYTE la misma que la de la petición limpia del mismo "
                "principal. No se comprueba que exista una comprobación."
            ),
        },
        command=f"pytest {SUITE}",
    )

    if spoofs:
        print(f"check_role_spoof: FALLO · {spoofs} caso(s) · G-ROLE-SPOOF es un AXIOMA\n")
        print(out[-2500:])
        return 1
    print(
        f"check_role_spoof: ok · 0 suplantaciones · {passed} casos adversariales, "
        "`_meta` y `arguments` son dato y no autoridad"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
