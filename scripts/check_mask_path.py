#!/usr/bin/env python
"""El ÚNICO camino al motor pasa por el anillo 4. Hecho ejecutable.

**Esto nace de una fuga real, encontrada el 2026-09-03 construyendo la fase 7.**
`AuditedExecutor` es el único camino sancionado a `Engine.execute()` (I-06) y
llamaba a `screen()` —anillos 2 y 3, guard y presupuesto— saltándose el 4. El
sistema entero devolvía **nombres y correos reales** a `analyst`, un rol para el que
la política dice `mask`. Su propio registro lo decía con un `columns_masked: []` que
nadie leía.

**Y `G-PII-LEAK` —que es un AXIOMA— pasaba con 0 fugas en 177 comprobaciones.**
Porque `pii_suite.py` medía `screen_and_mask()`, que es correcto y no es el camino
por el que se ejecuta. La suite ya se cambió para medir por el ejecutor; esto es lo
otro que hacía falta: que el fallo no pueda volver por descuido.

El contrato de import-linter no puede expresarlo —`audit` PUEDE importar `cost`, y
debe— así que hace falta mirar qué llama de verdad quien llega al motor. Es el mismo
razonamiento que llevó I-06 de la prosa a un contrato de imports.

**Qué comprueba, sobre el AST y no sobre el texto:**
1. Quien llame a `Engine.execute()` tiene que llamar también a `screen_and_mask`.
2. Y NO puede llamar a `screen` directamente: si lo hiciera, tendría a mano el
   camino que se salta el anillo 4, que es exactamente el que se acaba cogiendo.
3. `AuditedExecutor.__init__` exige `mask` SIN VALOR POR DEFECTO. Con un parámetro
   opcional el fallo vuelve el día que alguien se olvide de pasarlo, y vuelve en
   silencio.
"""

from __future__ import annotations

import ast
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gatelib import ROOT, record

SRC = ROOT / "src" / "datawarden"

#: Quien llega al motor. Lo dice I-06 y lo impone `[[tool.importlinter.contracts]]`
#: «Al motor solo se llega por la auditoría»: si esta lista crece, el contrato de
#: imports habrá fallado antes que este check.
ENGINE_CALLERS = ("audit/executor.py",)


def called_names(tree: ast.AST) -> set[str]:
    """Los nombres de función llamados en el módulo, sin resolver atributos."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


def mask_is_required(tree: ast.AST) -> bool:
    """`AuditedExecutor.__init__` pide `mask` y no le pone valor por defecto."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "__init__":
            continue
        args = node.args
        kwonly = [a.arg for a in args.kwonlyargs]
        if "mask" not in kwonly:
            continue
        index = kwonly.index("mask")
        default = args.kw_defaults[index]
        return default is None
    return False


def main() -> int:
    problems: list[str] = []
    checked: list[str] = []

    for relative in ENGINE_CALLERS:
        path = SRC / relative
        if not path.exists():
            problems.append(f"{relative} no existe y la lista de ENGINE_CALLERS lo nombra")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = called_names(tree)
        checked.append(relative)

        if "screen_and_mask" not in names:
            problems.append(
                f"{relative} llega al motor y NO llama a `screen_and_mask`. El anillo 4 "
                "es el enmascarado: sin él, el único camino sancionado a "
                "`Engine.execute()` devuelve valores reales de columnas `mask`"
            )
        if "screen" in names:
            problems.append(
                f"{relative} llama a `screen` directamente. `screen` para en el anillo "
                "3 y deja el 4 fuera; tener ese camino a mano es exactamente cómo se "
                "acabó cogiendo el 2026-09-03"
            )
        if not mask_is_required(tree):
            problems.append(
                f"{relative}: `mask` tiene que ser un argumento OBLIGATORIO de "
                "`__init__`, sin valor por defecto. Con uno opcional, el fallo vuelve "
                "el día que alguien se olvide de pasarlo, y vuelve en silencio"
            )

    record(
        "arch-checks.json",
        "G-MASK-PATH",
        value=len(problems),
        detail={"checked": checked, "problems": problems},
        command="python scripts/check_mask_path.py",
    )

    if problems:
        print("check_mask_path: FALLO · el camino al motor se salta el anillo 4\n")
        for problem in problems:
            print(f"  · {problem}")
        return 1
    print(
        f"check_mask_path: ok · {len(checked)} camino(s) al motor, todos por "
        "`screen_and_mask` y con la máscara obligatoria"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
