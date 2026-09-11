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
import math
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


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalo de Wilson al 95 %. **Se publica el intervalo, nunca el punto.**

    Vive aquí y no en cada script porque lo usan dos metas —`G-ATTACK-HOLDOUT` y
    `G-RECOVERY`— y las dos publican el intervalo en el informe. Dos copias de la
    fórmula del estadístico que se publica es una divergencia esperando a pasar: el
    día que una se corrija, la otra seguirá publicando el número viejo con la misma
    etiqueta.

    Con 15/15 sale aproximadamente [0,80 - 1,00]. Publicar «100 %» a secas con n=15
    es publicar un número sin significado.
    """
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denom = 1 + z**2 / total
    centre = (p + z**2 / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z**2 / (4 * total**2)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


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


#: El hardware de referencia, LEÍDO de `docs/GOALS.yaml` y no copiado.
#:
#: Estaba escrito a mano en `eval_exec.py`. Un número de latencia solo significa algo
#: junto al hardware declarado, así que dos sitios donde decirlo son dos sitios que
#: pueden discrepar — y el que estuviera mal seguiría publicándose sin que nada fallara.
def _hardware() -> str:
    import yaml  # type: ignore[import-untyped]

    datos = yaml.safe_load((ROOT / "docs" / "GOALS.yaml").read_text(encoding="utf-8"))
    return str(datos["hardware_referencia"])


#: El identificador del proyecto **en el vocabulario del contrato**, no el del directorio.
#:
#: El esquema lo tiene como enumerado —`citebound`, `evalgate`, `datawarden`,
#: `indexkeeper`— y aquí se publicaba `"data-warden-03"`, que es el nombre de la carpeta.
#: El informe se escribía igual y el panel del 02 lo habría descartado sin decir nada.
PROYECTO: str = "datawarden"

#: Los roles de prompt del contrato están en INGLÉS y son un enumerado cerrado. Dentro
#: de este repo los roles se llaman en español (`models.lock`, `--model-role`), así que
#: la traducción va aquí, en el borde, y no se reparte por los scripts.
ROLES_CONTRATO: dict[str, str] = {
    "generador": "generator",
    "generador_dev": "generator",
    "juez": "judge",
    "reescritor": "rewriter",
}


def rol_contrato(rol: str) -> str:
    """El rol de prompt, en el vocabulario del contrato. Lo que no encaja es `other`."""
    return ROLES_CONTRATO.get(rol, "other")


def eval_report(
    *,
    suite: str,
    metric_id: str,
    value: float,
    n: int,
    unit: str,
    deterministic: bool,
    models: dict[str, str],
    dataset: dict[str, Any],
    raw_path: str,
    ci95: tuple[float, float] | None = None,
    per_stratum: dict[str, float] | None = None,
    prompts: list[dict[str, Any]] | None = None,
    seed: int | None = None,
    notes: str = "",
) -> pathlib.Path:
    """Escribe un informe conforme a `docs/CONTRACTS/eval-report.schema.json`.

    **Está aquí y no en cada script por lo que costó descubrirlo.** El primer informe
    del proyecto —el de `G-EXEC-ACC`— declaraba `contract_version: "1.0.0"` cuando el
    contrato dice `const: 1`, y metía objetos en `per_stratum` cuando el esquema pide
    números. Los dos fallos son invisibles desde este lado: el fichero se escribe, el
    gate pasa, y lo que se rompe es un panel del proyecto 02 semanas después.

    Con tres evaluaciones escribiendo su propio JSON a mano, esos dos errores tenían
    tres sitios donde volver a aparecer. Con una función, uno — y `check_eval_reports.py`
    lo valida contra el contrato de verdad, no contra lo que aquí se crea recordar.

    `deterministic` no tiene valor por defecto A PROPÓSITO: es la diferencia entre un
    número que se repite y uno que llamó al modelo, y dejar que se olvide sería dejar
    que los dos se comparen como si fueran lo mismo.
    """
    import datetime as dt
    import platform

    ahora = dt.datetime.now(tz=dt.UTC)
    metrica: dict[str, Any] = {
        "id": metric_id,
        "value": value,
        "n": n,
        "unit": unit,
        "raw_path": raw_path,
    }
    if ci95 is not None:
        metrica["ci95"] = [round(ci95[0], 4), round(ci95[1], 4)]
    if per_stratum is not None:
        metrica["per_stratum"] = per_stratum

    informe: dict[str, Any] = {
        "contract_version": 1,
        "run_id": f"{suite}-{ahora.strftime('%Y%m%dT%H%M%SZ')}",
        "project": PROYECTO,
        "suite": suite,
        "created_at": ahora.isoformat().replace("+00:00", "Z"),
        "environment": {
            "hardware": _hardware(),
            "python": platform.python_version(),
            "deterministic": deterministic,
            "models": models,
        },
        "dataset": dataset,
        "metrics": [metrica],
    }
    if seed is not None:
        informe["environment"]["seed"] = seed
    if prompts:
        # **Se normaliza AQUÍ y no en cada llamador.** El contrato pide `role` de un
        # enumerado inglés y `version` entera; los scripts traen el rol en español y la
        # versión tal como venga del frontmatter del prompt, que es texto. Dejar que
        # cada uno lo convierta es dejar que uno se olvide.
        informe["prompts"] = [
            {
                **prompt,
                "role": rol_contrato(str(prompt.get("role", ""))),
                "version": int(prompt["version"]),
            }
            for prompt in prompts
        ]
    if notes:
        informe["notes"] = notes

    destino = REPORTS / f"{suite}-report.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        json.dumps(informe, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return destino
