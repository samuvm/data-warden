"""Lo que comparten los `check_*.py`: dónde queda cada número y cómo se compara.

**Un número sin artefacto no es un número.** `docs/GOALS.yaml` le exige a cada meta
un campo `artefacto`, y esto es lo que hace que ese campo signifique algo: todos los
checks escriben en el mismo formato, y `goals_check.py` lee ese formato en vez de
volver a ejecutar nada. La consecuencia práctica es que el gate se puede auditar
después: los ficheros de `evals/reports/` dicen qué se midió, cuándo y con qué
comando, y no hay que fiarse de que el proceso salió en verde.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPORTS = ROOT / "evals" / "reports"


def record(
    artifact: str,
    meta_id: str,
    *,
    value: float,
    adicionales: dict[str, float] | None = None,
    detail: dict[str, Any] | None = None,
    command: str = "",
) -> None:
    """Deja el número de una meta en su artefacto, sin borrar los de las otras.

    Fusiona en vez de sobrescribir porque varias metas comparten artefacto
    —`arch-checks.json` recoge cuatro— y un check que reescribiera el fichero
    entero borraría la medida del anterior. El gate saldría verde con la mitad de
    los números ausentes, que es la peor forma de pasar un gate.
    """
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / artifact
    payload: dict[str, Any] = {}
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
    payload.setdefault("metas", {})
    payload["metas"][meta_id] = {
        "value": value,
        "adicionales": adicionales or {},
        "command": command,
        "detail": detail or {},
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def read_meta(artifact: str, meta_id: str) -> dict[str, Any] | None:
    """El número medido de una meta, o `None` si nadie lo midió todavía."""
    path = REPORTS / artifact
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, Any] | None = payload.get("metas", {}).get(meta_id)
    return result


def compare(operator: str, measured: float, threshold: float) -> bool:
    """Los cinco operadores que `docs/CONTRACTS/goals.schema.json` admite."""
    if operator == ">=":
        return measured >= threshold
    if operator == "<=":
        return measured <= threshold
    if operator == "==":
        return measured == threshold
    if operator == ">":
        return measured > threshold
    if operator == "<":
        return measured < threshold
    message = f"operador desconocido en GOALS.yaml: {operator!r}"
    raise ValueError(message)


def run(command: list[str], *, cwd: pathlib.Path | None = None) -> tuple[int, str]:
    """Ejecuta y devuelve `(código, salida)`. La salida se conserva para el informe."""
    result = subprocess.run(
        command,
        cwd=str(cwd or ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, (result.stdout + result.stderr)
