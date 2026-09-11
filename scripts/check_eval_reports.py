#!/usr/bin/env python
"""`G-EVAL-REPORT` · todo informe de evaluación cumple el contrato transversal.

**Por qué esto es una meta y no una convención.** `docs/CONTRACTS/eval-report.schema.json`
es una copia literal de `_comun/` y un contrato con el proyecto 02: sus paneles leen
estos ficheros. Un informe que se desvía no da un error aquí, da un panel vacío allí, y
la desviación se descubre semanas después mirando una gráfica plana.

Se comprueban cuatro cosas, y las cuatro nacen de una forma distinta de romperlo:

1. **El esquema.** Validación real contra el contrato, no una lectura de claves. Un
   campo que cambia de tipo —`n` como cadena, `ci95` con tres elementos— pasa cualquier
   comprobación escrita a mano y rompe el consumidor.

2. **`metrics[].id` sale de `docs/GOALS.yaml`.** Es la frase literal de la meta: «cero
   métricas con id fuera de este fichero». Un informe que publica `G-EXEC-ACCURACY` en
   vez de `G-EXEC-ACC` es peor que uno que no publica nada: el panel del 02 lo enseña
   como una métrica nueva que nadie definió, y la que falta parece que dejó de medirse.

3. **`environment.deterministic` distingue `make eval` de `make eval-refresh`.** Un
   número que salió de casetes se repite; uno que llamó al modelo, no. Comparar los dos
   como si fueran la misma clase de dato es un error, y el contrato existe para que la
   puerta pueda rechazarlo en vez de que alguien lo note en una tabla.

4. **Toda evaluación que llama a un modelo publica su informe.** Sin esto la meta sería
   trivialmente verde el día que un informe se borre: cero ficheros, cero fallos. Se
   exige por META, no por fichero.
"""

from __future__ import annotations

import json
import pathlib
import sys

import jsonschema
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gatelib import ROOT, record

CONTRATO = ROOT / "docs" / "CONTRACTS" / "eval-report.schema.json"
GOALS = ROOT / "docs" / "GOALS.yaml"
REPORTS = ROOT / "evals" / "reports"

#: Las metas que se miden LLAMANDO A UN MODELO. Cada una tiene que publicar su informe.
#:
#: No se deduce del directorio a propósito: si se dedujera, borrar un informe haría
#: desaparecer su obligación y la meta saldría verde por no tener nada que mirar. La
#: lista es corta y explícita, y ampliarla es un acto consciente.
CON_MODELO = ("G-EXEC-ACC", "G-RECOVERY", "G-TOOL-CHOICE")


def ids_de_goals() -> set[str]:
    datos = yaml.safe_load(GOALS.read_text(encoding="utf-8"))
    return {str(m["id"]) for m in datos["metas"]}


def informes() -> dict[pathlib.Path, dict]:
    """Todo `.json` de `evals/reports/` que DECLARA cumplir el contrato.

    La marca es `contract_version`: quien la pone se está comprometiendo. Los ficheros
    de medida del gate —los de `{"metas": …}`— no la ponen y no son informes de
    evaluación, así que no se les exige un contrato que no dicen cumplir.
    """
    encontrados: dict[pathlib.Path, dict] = {}
    for ruta in sorted(REPORTS.glob("*.json")):
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(datos, dict) and "contract_version" in datos:
            encontrados[ruta] = datos
    return encontrados


def main() -> int:
    esquema = json.loads(CONTRATO.read_text(encoding="utf-8"))
    conocidas = ids_de_goals()
    hallados = informes()

    problemas: list[str] = []
    validos = 0
    publicadas: set[str] = set()

    for ruta, datos in hallados.items():
        nombre = ruta.relative_to(ROOT)
        errores = sorted(
            jsonschema.Draft202012Validator(esquema).iter_errors(datos),
            key=lambda e: list(e.path),
        )
        if errores:
            for error in errores[:3]:
                donde = "/".join(str(p) for p in error.path) or "(raíz)"
                problemas.append(
                    f"{nombre}: no cumple el esquema en `{donde}` · {error.message}"
                )
            continue
        validos += 1

        for metrica in datos.get("metrics", []):
            ident = str(metrica.get("id"))
            publicadas.add(ident)
            if ident not in conocidas:
                problemas.append(
                    f"{nombre}: publica la métrica `{ident}`, que NO está en "
                    "docs/GOALS.yaml. El panel del 02 la enseñaría como una métrica "
                    "que nadie definió"
                )

        determinista = datos.get("environment", {}).get("deterministic")
        if not isinstance(determinista, bool):
            problemas.append(
                f"{nombre}: `environment.deterministic` es {determinista!r} y tiene que "
                "ser booleano. Sin él no se distingue un número reproducible de uno que "
                "llamó al modelo"
            )

    for meta in CON_MODELO:
        if meta not in publicadas:
            problemas.append(
                f"{meta} se mide llamando a un modelo y NO publica informe de "
                "evaluación. Una evaluación sin informe no es consumible por el 02"
            )

    porcentaje = round(100.0 * validos / len(hallados), 2) if hallados else 0.0
    record(
        "arch-checks.json",
        "G-EVAL-REPORT",
        value=float(len(problemas)),
        adicionales={"informes válidos contra el esquema (%)": porcentaje},
        detail={
            "informes encontrados": len(hallados),
            "métricas publicadas": sorted(publicadas),
            "problemas": problemas,
        },
        command="python scripts/check_eval_reports.py",
    )

    if problemas:
        print(f"check_eval_reports: FALLO · {len(problemas)} problema(s)\n")
        for p in problemas:
            print(f"  · {p}")
        return 1

    print(
        f"check_eval_reports: ok · {len(hallados)} informe(s), {porcentaje}% válidos · "
        f"métricas {sorted(publicadas)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
