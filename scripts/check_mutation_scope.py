#!/usr/bin/env python
"""Vigila lo que se saca de la medida de mutación. **Es un check anti-gaming.**

P-005 saca de la mutación el TEXTO de los mensajes de rechazo. Es legítimo y está
aprobado, pero abre una puerta que hay que cerrar el mismo día que se abre: **un
fichero excluido no se mide, así que meter lógica ahí la saca del alcance sin que
ninguna meta se entere.** Sería exactamente el mecanismo 2 de anti-gaming —cumplir la
métrica empeorando lo medido— y encima sin dejar rastro.

Se comprueban tres cosas, y cada una cierra una forma distinta de abusarlo:

1. **La lista de exclusión es la que se aprobó.** Solo `guard/rules/messages.py`.
   Añadir un fichero de regla a `do_not_mutate` sacaría la regla entera de
   `G-MUT-GUARD` con una línea de configuración.
2. **Lo excluido no razona sobre el árbol.** `messages.py` no puede importar
   `sqlglot`: si lo hiciera, estaría decidiendo y no redactando.
3. **Lo excluido no decide el veredicto.** Ni `retryable`, ni niveles de política, ni
   comparaciones sobre el contexto del guard. La composición se queda dentro de la
   medida, que es literalmente el recorte con el que Samuel aprobó P-005.
"""

from __future__ import annotations

import ast
import pathlib
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gatelib import ROOT, record

#: Lo único que P-005 autoriza a sacar de la medida. Cualquier otra cosa en
#: `do_not_mutate` es una propuesta nueva, no una línea de configuración.
ALLOWED_EXCLUSIONS = frozenset({"src/datawarden/guard/rules/messages.py"})

#: Nombres que, si aparecen en un fichero excluido, significan que ahí se está
#: decidiendo algo. No es una lista de palabras prohibidas por gusto: son los tres
#: ejes del veredicto —si se reintenta, qué nivel de política se violó, y qué dice el
#: árbol— y ninguno puede vivir fuera de la medida.
FORBIDDEN_NAMES = frozenset({"retryable", "Level", "GuardContext", "RuleResult"})

FORBIDDEN_IMPORTS = frozenset({"sqlglot"})


def main() -> int:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    excluded = list(config.get("tool", {}).get("mutmut", {}).get("do_not_mutate", []))

    problems: list[str] = []

    unexpected = sorted(set(excluded) - ALLOWED_EXCLUSIONS)
    if unexpected:
        problems.append(
            f"hay ficheros excluidos de la mutación que P-005 no autoriza: {unexpected}. "
            "Excluir un fichero lo saca del denominador sin medirlo: si de verdad hace "
            "falta, es una propuesta nueva en docs/PARA-SAMUEL.md, no una línea de "
            "configuración."
        )

    for relative in excluded:
        path = ROOT / relative
        if not path.exists():
            problems.append(f"{relative} está excluido de la mutación y no existe")
            continue
        problems.extend(_inspect(relative, path))

    record(
        "mutation-scope.json",
        "G-MUT-SCOPE",
        value=len(problems),
        detail={
            "excluidos": excluded,
            "autorizados": sorted(ALLOWED_EXCLUSIONS),
            "problemas": problems,
            "por_que": (
                "Un fichero excluido de la mutación no se mide. Meter lógica ahí la "
                "sacaría del alcance sin que ninguna meta se enterara, que es el "
                "mecanismo 2 de anti-gaming y encima silencioso."
            ),
        },
        command="make mutation",
    )

    if problems:
        print(f"check_mutation_scope: FALLO · {len(problems)} problemas\n")
        for problem in problems:
            print(f"  · {problem}")
        return 1
    print(
        f"check_mutation_scope: ok · {len(excluded)} fichero(s) fuera de la medida, "
        "todos autorizados y sin lógica de decisión"
    )
    return 0


def _inspect(relative: str, path: pathlib.Path) -> list[str]:
    """Lo excluido redacta; no decide. Se comprueba sobre el AST, no con `grep`."""
    problems: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FORBIDDEN_IMPORTS:
                    problems.append(
                        f"{relative} importa {alias.name}: un fichero fuera de la "
                        "medida no puede razonar sobre el árbol, solo redactar"
                    )
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in FORBIDDEN_IMPORTS:
                problems.append(
                    f"{relative} importa de {node.module}: un fichero fuera de la "
                    "medida no puede razonar sobre el árbol, solo redactar"
                )
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            problems.append(
                f"{relative} nombra {node.id!r}: eso es parte del VEREDICTO y tiene "
                "que quedarse dentro de la medida (P-005, recortada)"
            )
        elif isinstance(node, ast.arg) and node.arg in FORBIDDEN_NAMES:
            problems.append(
                f"{relative} recibe {node.arg!r} como argumento: el veredicto se "
                "decide en la regla, no aquí"
            )
    return problems


if __name__ == "__main__":
    sys.exit(main())
