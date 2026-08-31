#!/usr/bin/env python
"""`Engine.execute()` acepta un `ValidatedQuery`, jamás un `str`.

ES EL INVARIANTE I-01 Y EL PRIMERO DE `CLAUDE.md`: lo que se ejecuta es
`ast.sql(dialect=...)` del árbol YA VALIDADO, nunca la cadena que entró.

La diferencia no es estilística. Si el motor acepta texto, existe un camino por
el que una consulta llega a la base de datos sin haber pasado por el guard, y
entonces los cinco anillos son decorativos: basta con que alguien, alguna vez,
llame a `execute(sql)` en vez de a `execute(validated)`.

Se comprueba sobre el ÁRBOL SINTÁCTICO, no con `grep`. Un `grep` se salta con un
salto de línea, y este proyecto entero trata sobre por qué buscar cadenas de
texto no sirve para decidir si algo es seguro.
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINES = ROOT / "src" / "datawarden" / "engines"
ALLOWED_PARAM_TYPES = {"ValidatedQuery"}


def annotation_name(node: ast.expr | None) -> str | None:
    """Nombre legible de una anotación de tipo, sin evaluarla."""
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value  # anotación diferida: "ValidatedQuery"
    if isinstance(node, ast.Subscript):
        return annotation_name(node.value)
    return ast.unparse(node)


def check_file(path: pathlib.Path) -> list[str]:
    problems: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name != "execute":
            continue
        args = [a for a in node.args.args if a.arg not in {"self", "cls"}]
        if not args:
            problems.append(f"{path}:{node.lineno} `execute` sin parámetro de consulta")
            continue
        first = args[0]
        name = annotation_name(first.annotation)
        if name is None:
            problems.append(
                f"{path}:{node.lineno} `execute({first.arg})` sin anotación de tipo. "
                "El invariante no se puede verificar sobre lo que no se declara."
            )
        elif name not in ALLOWED_PARAM_TYPES:
            problems.append(
                f"{path}:{node.lineno} `execute({first.arg}: {name})`. "
                f"Solo se admite {sorted(ALLOWED_PARAM_TYPES)}: si el motor acepta "
                "texto, existe un camino a la base de datos que no pasa por el guard."
            )
    return problems


def main() -> int:
    if not ENGINES.exists():
        print(
            f"check_no_raw_sql: {ENGINES.relative_to(ROOT)} no existe todavía "
            "(fase 0). El invariante se comprobará en cuanto haya un motor."
        )
        return 0
    files = sorted(ENGINES.rglob("*.py"))
    problems: list[str] = []
    for f in files:
        problems.extend(check_file(f))
    if problems:
        print("check_no_raw_sql: FALLO · I-01\n")
        for p in problems:
            print(f"  · {p}")
        return 1
    print(f"check_no_raw_sql: ok · {len(files)} ficheros de engines/ verificados")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
