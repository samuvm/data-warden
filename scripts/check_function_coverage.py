#!/usr/bin/env python
"""`G-COV-FUNC`: toda función pública de un paquete testable la ejerce algún test.

Es el punto 4 de la Definition of Done, y la constitución §2.6 lo hace ejecutable
en vez de dejarlo en «un test por función»: se mide con `--cov-context=test` y con
el AST, **no por convención de nombres**, porque una convención de nombres se
falsea trivialmente escribiendo `test_foo` sin llamar a `foo`.

Cómo funciona: `coverage` registra QUÉ test cubrió cada línea. El script recorre el
AST de los ocho paquetes de `[tool.gate].testable`, localiza la primera línea
ejecutable de cada función pública y exige que tenga al menos un contexto de test.

**En `guard/` la unidad no es la función, es el caso** (docs/RULES.md §3): una regla
tiene una única función pública `check`, así que «un test por función» quedaría
satisfecho por un solo test y no probaría nada. Eso lo cubre
`scripts/check_rule_coverage.py`, y aquí se sigue exigiendo el mínimo.
"""

from __future__ import annotations

import ast
import json
import pathlib
import sys
import tomllib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gatelib import ROOT, record

COVERAGE_JSON = ROOT / "evals" / "reports" / "coverage-contexts.json"

#: Métodos especiales que no son «función pública» aunque no empiecen por `_`.
#: Un `__post_init__` se ejercita construyendo el objeto y exigirle test propio
#: sería exigir un test que no puede llamarlo directamente.
_DUNDER_EXEMPT = frozenset({"__post_init__", "__repr__", "__str__", "__eq__", "__hash__"})


def public_functions(path: pathlib.Path) -> list[tuple[str, int]]:
    """`(nombre_cualificado, primera_línea_ejecutable)` de cada función pública."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                name = child.name
                if name.startswith("_") and name not in _DUNDER_EXEMPT:
                    continue
                if name in _DUNDER_EXEMPT:
                    continue
                if any(
                    isinstance(d, ast.Name) and d.id == "overload" for d in child.decorator_list
                ):
                    continue
                body = [
                    stmt
                    for stmt in child.body
                    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
                ]
                if not body:
                    continue
                found.append((f"{prefix}{name}", body[0].lineno))
                walk(child, f"{prefix}{name}.")

    walk(tree, "")
    return found


def main() -> int:
    gate = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["gate"]
    packages = [ROOT / "src" / "datawarden" / p for p in gate["testable"]]

    if not COVERAGE_JSON.exists():
        print(
            "check_function_coverage: FALLO · no hay medida de contextos.\n"
            "  Falta `make coverage`, que es quien escribe "
            f"{COVERAGE_JSON.relative_to(ROOT)} con --cov-context=test.\n"
            "  Un check que aprueba porque no encuentra su medida es una señal "
            "verde falsa, así que esto es rojo."
        )
        return 1

    data = json.loads(COVERAGE_JSON.read_text(encoding="utf-8"))
    files = data.get("files", {})

    uncovered: list[str] = []
    total = 0
    for package in packages:
        if not package.exists():
            continue
        for source in sorted(package.rglob("*.py")):
            if source.name == "__init__.py" and not source.read_text(encoding="utf-8").strip():
                continue
            relative = str(source.relative_to(ROOT))
            contexts = files.get(relative, {}).get("contexts", {})
            for name, lineno in public_functions(source):
                total += 1
                hit = contexts.get(str(lineno), [])
                # coverage anota `""` como contexto cuando la línea se ejecutó fuera
                # de un test (por ejemplo, al importar el módulo). No cuenta.
                if not [c for c in hit if c]:
                    uncovered.append(f"{relative}::{name}:{lineno}")

    record(
        "coverage.json",
        "G-COV-FUNC",
        value=len(uncovered),
        adicionales={"paquetes de [tool.gate].testable auditados": len(gate["testable"])},
        detail={"public_functions": total, "uncovered": uncovered},
        command="make coverage && python scripts/check_function_coverage.py",
    )

    if uncovered:
        print(f"check_function_coverage: FALLO · {len(uncovered)} de {total} sin test\n")
        for item in uncovered[:30]:
            print(f"  · {item}")
        if len(uncovered) > 30:
            print(f"  … y {len(uncovered) - 30} más")
        return 1
    print(
        f"check_function_coverage: ok · {total} funciones públicas, "
        f"todas con al menos un contexto de test, en {len(gate['testable'])} paquetes"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
