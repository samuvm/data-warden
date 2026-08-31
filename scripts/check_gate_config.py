#!/usr/bin/env python
"""`[tool.gate]` de pyproject.toml dice los mismos números que docs/GOALS.yaml.

POR QUÉ ESTE SCRIPT EXISTE, y no es paranoia.

`docs/GOALS.yaml` está sellado por `thresholds.lock`: cambiar un umbral allí pone
`make done` en rojo sin excepción. `pyproject.toml` NO está sellado. Sería, por
tanto, el atajo más barato de todo el repositorio: bajar el 90 de cobertura a 70
en `[tool.gate]`, que la suite pase, y que nadie mire los dos ficheros a la vez.

Manda `GOALS.yaml`. Esto es una copia operativa que se verifica en cada
`make coverage`, y el invariante I-16 de `docs/RULES.md`.
"""

from __future__ import annotations

import pathlib
import tomllib

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent

# meta de GOALS.yaml  ->  (clave en [tool.gate], de dónde sale el valor)
CHECKS = [
    ("G-COV-LINE", "cobertura_linea_min", "umbral"),
    ("G-COV-LINE", "cobertura_linea_min_critica", "adicional"),
    ("G-COV-FUNC", "funciones_sin_cubrir_max", "umbral"),
    ("G-MUTATION", "mutantes_muertos_min", "umbral"),
    ("G-MUT-GUARD", "mutantes_muertos_min_guard", "umbral"),
    ("G-SECRETS", "secretos_nuevos_max", "umbral"),
]


def main() -> int:
    goals = yaml.safe_load((ROOT / "docs" / "GOALS.yaml").read_text(encoding="utf-8"))
    gate = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["gate"]
    by_id = {m["id"]: m for m in goals["metas"]}

    problems: list[str] = []
    for meta_id, key, source in CHECKS:
        meta = by_id.get(meta_id)
        if meta is None:
            problems.append(f"{meta_id}: no está en GOALS.yaml")
            continue
        if source == "umbral":
            expected = meta["umbral"]["valor"]
        else:
            extra = meta["umbral"].get("adicionales") or []
            if not extra:
                problems.append(f"{meta_id}: se esperaba un umbral adicional y no hay")
                continue
            expected = extra[0]["valor"]
        actual = gate.get(key)
        if actual != expected:
            problems.append(
                f"{meta_id} vale {expected} en GOALS.yaml y "
                f"[tool.gate].{key} vale {actual}. Manda GOALS.yaml."
            )

    # `G-COV-FUNC` exige un número exacto de paquetes auditados: si alguien añade
    # un paquete testable y no lo declara, deja de medirse sin que nada avise.
    cov_func = by_id.get("G-COV-FUNC", {})
    extra = (cov_func.get("umbral", {}).get("adicionales") or [{}])[0]
    n_expected = extra.get("valor")
    n_actual = len(gate.get("testable", []))
    if n_expected is not None and n_actual != n_expected:
        problems.append(
            f"G-COV-FUNC exige {n_expected} paquetes auditados y "
            f"[tool.gate].testable declara {n_actual}: {gate.get('testable')}"
        )

    if problems:
        print("check_gate_config: FALLO\n")
        for p in problems:
            print(f"  · {p}")
        return 1
    print(
        f"check_gate_config: ok · {len(CHECKS)} umbrales y "
        f"{n_actual} paquetes coinciden con GOALS.yaml"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
