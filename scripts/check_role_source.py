#!/usr/bin/env python
"""I-05 · **El rol nunca viene de datos no autenticados.**

`_meta` y los argumentos de tool son DATO, no autoridad. La spec MCP 2026-07-28
eliminó las sesiones, así que cualquier cosa que el cliente diga sobre sí mismo es
dato transportado; si el rol pudiera salir de ahí, el anillo 5 sería ficción y
cualquiera se autoconcedería `admin` con un campo en el JSON.

La primera mitad de la invariante ya vive en el TIPO: `RoleSource` no tiene ningún
valor que signifique «lo dijo el cliente», así que ni siquiera se puede escribir. Lo
que este script añade es la segunda mitad: **que ningún módulo fuera de `principal/`
saque un rol de un diccionario de petición.**

Se comprueba sobre el AST: se buscan accesos por clave —`x["role"]`, `x.get("role")`—
a nombres sospechosos de venir del transporte, fuera de `principal/`.
"""

from __future__ import annotations

import ast
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gatelib import ROOT, record

SRC = ROOT / "src" / "datawarden"

#: `principal/` es donde el rol SE RESUELVE, así que ahí sí puede leerse de algo.
ALLOWED_PACKAGES = {"principal"}

#: Claves cuya lectura desde un diccionario sería sacar autoridad de un dato.
SUSPICIOUS_KEYS = {"role", "rol", "principal", "principal_id", "roles", "scope", "scopes"}


def offenders(path: pathlib.Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        # x["role"]
        if isinstance(node, ast.Subscript):
            key = node.slice
            if (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and key.value.lower() in SUSPICIOUS_KEYS
            ):
                found.append((node.lineno, f"[{key.value!r}]"))
        # x.get("role")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value.lower() in SUSPICIOUS_KEYS
        ):
            found.append((node.lineno, f".get({node.args[0].value!r})"))
    return found


def main() -> int:
    problems: list[str] = []
    checked = 0
    for path in sorted(SRC.rglob("*.py")):
        package = path.relative_to(SRC).parts[0] if path.parent != SRC else "<raíz>"
        if package in ALLOWED_PACKAGES:
            continue
        checked += 1
        for lineno, how in offenders(path):
            problems.append(
                f"{path.relative_to(ROOT)}:{lineno} lee {how} de un diccionario fuera "
                "de principal/. El rol no puede salir de un dato transportado (I-05)"
            )

    record(
        "mcp-conformance.json",
        "G-ROLE-SOURCE",
        value=len(problems),
        detail={"files_checked": checked, "problems": problems},
        command="python scripts/check_role_source.py",
    )

    if problems:
        print("check_role_source: FALLO · I-05\n")
        for p in problems:
            print(f"  · {p}")
        return 1
    print(
        f"check_role_source: ok · {checked} ficheros fuera de principal/ y ninguno "
        "saca un rol de un diccionario de petición"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
