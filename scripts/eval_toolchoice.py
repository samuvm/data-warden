#!/usr/bin/env python
"""`G-TOOL-CHOICE` · las descripciones de las tools bastan para elegir bien.

**La prueba que casi nadie hace, y es puro diseño de MCP.** Al modelo se le dan
**solo las cuatro descripciones** —sin catálogo, sin glosario, sin ejemplos y sin el
esquema— y se comprueba que elige la herramienta correcta en >= 18 de 20 escenarios.
Si hiciera falta más contexto para acertar, el defecto está en la descripción y no
en el modelo.

**LA REGLA DE REACCIÓN, y está en `GOALS.yaml`: si sale por debajo de 18 se
reescribe la DESCRIPCIÓN, no el umbral ni el escenario.** La meta es «negociable
hacia arriba solo». Bajarla sería tapar exactamente lo que la prueba mide.

**Y SE MIDE LA LÍNEA BASE, que es lo que impide que el número sea decorativo.**
Medido el 2026-09-03: unas descripciones de una línea que no dicen cuándo NO usar
cada herramienta sacan **18 de 20**, o sea, exactamente el umbral. La meta se cumple
sin haber hecho ningún diseño. Las descripciones de verdad sacan 20/20 y las pobres
fallan justo donde importa, así que la prueba SÍ discrimina — pero el número solo
significa algo junto a su línea base, y por eso el informe publica los dos y el
margen. Propuesta de subir el umbral: P-009 en `docs/PARA-SAMUEL.md`.

**La procedencia se publica y no se adorna.** Samuel aceptó el borrador al responder
Q-008 y su trabajo es corregir la columna «correcta»; mientras no lo haya hecho, el
informe dice `agente_propuesto`. Un número medido contra una respuesta correcta que
nadie ha revisado es el agente puntuándose a sí mismo, y decirlo es la diferencia
entre una evaluación y una demostración.

Determinista y gratis desde casetes, igual que `G-RECOVERY`: `--refresh` es lo único
que llama al modelo, y el juez NUNCA es el modelo que genera.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import urllib.request

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from datawarden.nl2sql.providers import LocalProvider
from gatelib import ROOT, record

SUITE = ROOT / "evals" / "suites" / "tool-choice.yaml"
TOOLS = ROOT / "docs" / "spec" / "tools.yaml"
CASSETTES = ROOT / "evals" / "cassettes-toolchoice"


def prompt_for(descriptions: str, statement: str) -> str:
    """Lo ÚNICO que ve el modelo. Cada línea de más aquí falsearía la prueba.

    No hay catálogo, no hay glosario, no hay ejemplos resueltos y no hay ni una
    pista sobre qué tablas existen: si con eso no basta para elegir, la descripción
    de la herramienta no está terminada, que es justo lo que se quiere saber.
    """
    return (
        "Tienes estas cuatro herramientas y nada más:\n\n"
        f"{descriptions}\n\n"
        "Un usuario dice:\n\n"
        f"  «{statement}»\n\n"
        "Responde SOLO con el nombre de la herramienta que hay que usar. "
        "Una palabra, sin explicación, sin comillas y sin punto final."
    )


def descriptions_block(tools: dict[str, object]) -> str:
    """Las descripciones REALES, tal como las publica el contrato."""
    lines = []
    for tool in tools["herramientas"]:  # type: ignore[index]
        texto = " ".join(str(tool["descripcion"]).split())
        lines.append(f"- **{tool['nombre']}** — {tool['titulo']}. {texto}")
    return "\n".join(lines)


def baseline_block(suite: dict[str, object]) -> str:
    """El control negativo, declarado en la suite y no escondido aquí."""
    base = suite["linea_base"]["descripciones"]  # type: ignore[index]
    return "\n".join(f"- **{name}** — {text}" for name, text in base.items())


def cache_key(statement: str, descriptions: str, model: str) -> str:
    """Incluye las DESCRIPCIONES a propósito.

    Reescribir una descripción es exactamente la reacción que la meta prescribe
    cuando el número sale bajo, así que la caché tiene que invalidarse con ella: si
    no, se reescribiría la descripción y se seguiría publicando el número viejo.
    """
    payload = json.dumps(
        {"statement": statement, "descriptions": descriptions, "model": model},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def pick(answer: str, names: list[str]) -> str | None:
    """La herramienta que el modelo eligió, o `None` si no eligió una sola.

    Se exige que aparezca UNA: una respuesta que nombra dos no es una elección, y
    contarla como acierto porque la correcta está entre ellas sería regalar el punto.
    """
    lowered = answer.lower()
    found = [name for name in names if name in lowered]
    return found[0] if len(found) == 1 else None


def ask(model: str, prompt: str, endpoint: str, timeout_s: float) -> str:
    body = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "options": {"temperature": 0.0, "seed": LocalProvider.seed},
        }
    ).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310  — endpoint fijo y local
        endpoint, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310
        return str(json.loads(response.read().decode("utf-8")).get("response", ""))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="llama al modelo y regraba")
    parser.add_argument("--model-role", default="juez")
    args = parser.parse_args()

    from eval_recovery import models_lock

    suite = yaml.safe_load(SUITE.read_text(encoding="utf-8"))
    tools = yaml.safe_load(TOOLS.read_text(encoding="utf-8"))
    names = [str(t["nombre"]) for t in tools["herramientas"]]
    blocks = {"real": descriptions_block(tools), "base": baseline_block(suite)}
    tag, digest = models_lock(args.model_role)
    local = LocalProvider(model=tag, think=False)
    CASSETTES.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, object]] = []
    problems: list[str] = []
    hits = {"real": 0, "base": 0}

    for case in suite["escenarios"]:
        statement = str(case["enunciado"])
        chosen_by: dict[str, str | None] = {}
        for kind, block in blocks.items():
            path = CASSETTES / f"{cache_key(statement, block, tag)}.json"
            if args.refresh:
                answer = ask(tag, prompt_for(block, statement), local.endpoint, local.timeout_s)
                path.write_text(
                    json.dumps(
                        {"statement": statement, "kind": kind, "model": tag, "answer": answer},
                        indent=2,
                        ensure_ascii=False,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                print(f"    · {case['id']} [{kind}] -> {answer.strip()[:36]}", flush=True)
            elif not path.exists():
                problems.append(
                    f"{case['id']} [{kind}]: no hay grabación. `make eval-toolchoice` NO "
                    "llama al modelo. Si las DESCRIPCIONES han cambiado —que es la "
                    "reacción que la meta prescribe— regraba con "
                    "`make eval-toolchoice-refresh`."
                )
                continue
            else:
                answer = str(json.loads(path.read_text(encoding="utf-8"))["answer"])
            chosen = pick(answer, names)
            chosen_by[kind] = chosen
            hits[kind] += int(chosen == str(case["correcta"]))

        tolerated = case.get("aceptable_con_penalizacion")
        chosen = chosen_by.get("real")
        results.append(
            {
                "id": case["id"],
                "statement": statement,
                "expected": str(case["correcta"]),
                "chosen": chosen,
                "chosen_baseline": chosen_by.get("base"),
                "hit": chosen == str(case["correcta"]),
                "ambiguous": bool(case.get("ambiguo")),
                "tolerated": chosen == tolerated if tolerated else False,
            }
        )

    total = len(suite["escenarios"])
    record(
        "tool-choice.json",
        "G-TOOL-CHOICE",
        value=float(hits["real"]),
        adicionales={"escenarios del conjunto": float(total)},
        detail={
            "provenance": suite["provenance"],
            "reviewed_by": suite.get("revisado_por"),
            "model": {"role": args.model_role, "tag": tag, "digest": digest},
            "tools_contract_sha256": hashlib.sha256(TOOLS.read_bytes()).hexdigest(),
            # LA LÍNEA BASE VA EN EL INFORME. Sin ella, 20/20 se lee como un triunfo
            # cuando la señal real es el margen sobre no haber diseñado nada.
            "baseline_hits": hits["base"],
            "margin_over_baseline": hits["real"] - hits["base"],
            "baseline_reason": " ".join(str(suite["linea_base"]["motivo"]).split()),
            "misses": [r for r in results if not r["hit"]],
            "results": results,
            "problems": problems,
        },
        command="make eval-toolchoice",
    )

    print(f"\neval_toolchoice: {hits['real']}/{total} · modelo {tag} ({digest})")
    print(
        f"  LÍNEA BASE con descripciones pobres: {hits['base']}/{total} · "
        f"margen {hits['real'] - hits['base']:+d}"
    )
    print(f"  procedencia: {suite['provenance']}")
    if suite["provenance"] != "agente_propuesto_revisado_humano":
        print(
            "  AVISO · la columna «correcta» NO la ha revisado Samuel todavía (Q-008, "
            "45 min). Hasta entonces esto es el agente puntuándose a sí mismo."
        )
    for row in results:
        if not row["hit"]:
            print(f"  fallo {row['id']}: esperaba {row['expected']}, eligió {row['chosen']}")
    if problems:
        for problem in problems:
            print(f"  - {problem}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
