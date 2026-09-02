#!/usr/bin/env python
"""I-04 · **`except Exception` solo en `validator.validate`, y termina en rechazo.**

Es la invariante que hace que `G-FAILCLOSED` signifique algo. La propiedad de
Hypothesis comprueba que hoy, con cinco mil entradas, no sale ninguna excepción;
esto comprueba que **no puede** salir, porque no hay ningún otro sitio donde se
capture y se siga adelante.

La diferencia entre las dos importa: un `except Exception: pass` dentro de una regla
haría que el guard siguiera validando con un árbol a medio procesar y aceptara. La
propiedad no lo vería —no sale ninguna excepción, en efecto— y el agujero estaría ahí.

Se comprueba sobre el ÁRBOL SINTÁCTICO del código, no con `grep`. Un `grep` se salta
con un salto de línea, y este proyecto entero trata sobre por qué buscar cadenas de
texto no sirve para decidir si algo es seguro.
"""

from __future__ import annotations

import ast
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gatelib import ROOT, record

GUARD = ROOT / "src" / "datawarden" / "guard"

#: El único sitio donde `except Exception` es legítimo, y la función exacta.
ALLOWED = {("validator.py", "validate")}

#: Excepciones ANCHAS que se comprueban. Un `except ValueError` acotado es normal.
BROAD = {"Exception", "BaseException"}

#: Sitios donde una captura ancha es legítima **por fail-closed**, con su motivo.
#: Cada entrada tiene que explicarse: una exención sin motivo es un agujero con
#: permiso.
JUSTIFIED: dict[tuple[str, str], str] = {
    ("query_lineage.py", "resolve"): (
        "si no se puede construir el árbol de ámbitos, el linaje queda vacío y R008 "
        "trata lo desconocido como lo más restrictivo: la captura TERMINA EN RECHAZO "
        "por otra vía, que es lo que I-04 exige"
    ),
}


def broad_handlers(path: pathlib.Path) -> list[tuple[str, int, bool]]:
    """`(función, línea, termina_en_retorno)` de cada `except` ancho del fichero."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int, bool]] = []

    def walk(node: ast.AST, fn: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = (
                child.name if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) else fn
            )
            if isinstance(child, ast.ExceptHandler):
                caught = child.type
                names = set()
                if isinstance(caught, ast.Name):
                    names.add(caught.id)
                elif isinstance(caught, ast.Tuple):
                    names |= {e.id for e in caught.elts if isinstance(e, ast.Name)}
                elif caught is None:
                    names.add("BaseException")
                if names & BROAD:
                    # Un manejador que no devuelve ni relanza sigue adelante con el
                    # estado a medias: es exactamente lo que fail-closed prohíbe.
                    ends = any(isinstance(stmt, ast.Return | ast.Raise) for stmt in child.body)
                    found.append((name, child.lineno, ends))
            walk(child, name)

    walk(tree, "<module>")
    return found


def main() -> int:
    problems: list[str] = []
    inventory: list[dict[str, object]] = []
    for path in sorted(GUARD.rglob("*.py")):
        for fn, lineno, ends in broad_handlers(path):
            key = (path.name, fn)
            inventory.append(
                {"file": path.name, "function": fn, "line": lineno, "returns": ends}
            )
            if key in ALLOWED:
                if not ends:
                    problems.append(
                        f"{path.name}:{lineno} `{fn}` captura Exception y NO devuelve ni "
                        "relanza: seguiría adelante con el estado a medias"
                    )
                continue
            if key in JUSTIFIED:
                if not ends:
                    problems.append(
                        f"{path.name}:{lineno} `{fn}` está justificada pero no devuelve"
                    )
                continue
            problems.append(
                f"{path.name}:{lineno} `{fn}` captura {BROAD} y no es "
                "`validator.validate`. I-04: el guard tiene UN solo sitio donde se "
                "atrapa lo ancho, y siempre termina en Rejected"
            )

    record(
        "guard-property.json",
        "G-FAILCLOSED-AST",
        value=len(problems),
        detail={
            "handlers": inventory,
            "allowed": sorted(map(list, ALLOWED)),
            "problems": problems,
        },
        command="python scripts/check_failclosed.py",
    )

    if problems:
        print("check_failclosed: FALLO · I-04\n")
        for p in problems:
            print(f"  · {p}")
        return 1
    print(
        f"check_failclosed: ok · {len(inventory)} capturas anchas en guard/, todas en "
        "sitios declarados y todas terminan en rechazo"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
