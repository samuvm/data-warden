#!/usr/bin/env python
"""`G-EXEC-ACC` · la métrica insignia. El sistema entero contra el banco de referencia.

**Qué se mide.** Se le da la pregunta EN LENGUAJE NATURAL al ciclo completo —generar,
validar, corregir, ejecutar— y se compara lo que sale con la referencia congelada. No
se compara el SQL: se compara **el resultset normalizado**, según
`docs/spec/resultset-equality.md`. Dos consultas distintas que devuelven lo mismo son
la misma respuesta, y una que se parece a la de referencia y devuelve otra cosa no lo es.

**Los 10 de rechazo se puntúan por su `rule_id`.** Su respuesta correcta no es un
resultset: es que el sistema NO conteste, y que diga por qué con la regla exacta.
Acertar el rechazo por otra regla no es acertar — el mensaje que lee quien pregunta
sería otro—, y por eso se exige la declarada. Son 10 de los 60 y `PLAN.md` los pide
precisamente porque casi ningún banco los tiene: **medir solo lo que el sistema
contesta mide la mitad del sistema.**

**Determinista y gratis desde casetes**, igual que `G-RECOVERY`. `--refresh` es lo
único que llama al modelo.

**LA PROCEDENCIA MANDA.** Mientras `questions.yaml` no diga `revisado_humano`, el
informe lo publica y el número sale etiquetado. Un banco que el agente se escribió y
se corrigió es el agente puntuándose a sí mismo, por muy bien transcrito que esté.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import platform
import sys
from typing import Any

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from datawarden.domain.types import Principal, Role, RoleSource, ValidatedQuery
from datawarden.evalsupport.resultset_equality import Table, compare
from datawarden.guard.validator import validate
from datawarden.nl2sql.loop import run_loop
from datawarden.nl2sql.providers import CASSETTE_DIR, LocalProvider, RecordedProvider
from gatelib import ROOT, record, wilson

QUESTIONS = ROOT / "evals" / "golden" / "questions.yaml"
FROZEN = ROOT / "evals" / "golden" / "resultsets"
CONTRACT_VERSION = "1.0.0"
GATE_PEPPER = "pimienta-del-gate-solo-para-medir-g-pii-leak"


def sha_del_banco() -> str:
    """El sha del golden set. **Comparar dos informes sobre bancos distintos engaña.**"""
    return "sha256:" + hashlib.sha256(QUESTIONS.read_bytes()).hexdigest()


def como_tabla(columnas: list[str], filas: list[Any]) -> Table:
    return Table(columns=tuple(columnas), rows=[tuple(f) for f in filas])


def normalizar(valor: Any) -> Any:
    """La misma normalización que `freeze_questions.py`. **Tiene que ser la misma.**

    Si el congelador y el comparador normalizaran distinto, un caso correcto fallaría
    por la forma de un decimal y el número culparía al modelo de una diferencia de
    serialización.
    """
    import decimal

    if valor is None or isinstance(valor, (bool, int, str)):
        return valor
    if isinstance(valor, float):
        return repr(valor)
    if isinstance(valor, decimal.Decimal):
        return str(valor)
    if isinstance(valor, (dt.date, dt.datetime)):
        return valor.isoformat()
    return str(valor)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="llama al modelo y regraba")
    parser.add_argument("--model-role", default="generador")
    args = parser.parse_args()

    from eval_recovery import model_ref, models_lock

    raw = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))
    perfil = str(raw.get("perfil", "dev"))
    database = ROOT / "datagen" / "out" / f"cierzo-{perfil}.duckdb"
    if not database.exists():
        print(f"eval_exec: FALLO · no existe {database.relative_to(ROOT)}")
        return 1

    from datawarden.audit.factory import build_executor
    from datawarden.catalog import SCHEMA_PATH, load_generated
    from datawarden.mask.config import MaskConfig
    from datawarden.nl2sql.prompt import load as load_prompt
    from datawarden.principal import POLICY_PATH
    from datawarden.principal.policy import load_policy

    schema = load_generated(SCHEMA_PATH)
    policy = load_policy(POLICY_PATH)
    mask = MaskConfig(pepper=GATE_PEPPER)
    executor = build_executor(
        database=database, audit_db=ROOT / "var" / "eval-exec.sqlite3", mask=mask
    )
    tag, digest = models_lock(args.model_role)
    prompt = load_prompt("nl2sql")
    provider: Any = (
        _Grabando(LocalProvider(model=tag, think=False), tag)
        if args.refresh
        else RecordedProvider(directory=ROOT / CASSETTE_DIR)
    )

    resultados: list[dict[str, Any]] = []
    problemas: list[str] = []

    def valida_para(rol: str) -> Any:
        who = Principal(id=f"eval-{rol}", role=Role(rol), source=RoleSource.CLI_FLAG)

        def check(sql: str) -> Any:
            return validate(sql, principal=who, schema=schema, policy=policy, max_rows=50_000)

        return check

    def resolver(caso: dict[str, Any]) -> Any:
        """Resuelve un caso, y distingue **fallo de medida** de fallo del modelo.

        El bucle es fail-closed: si el proveedor revienta —y un fallo de caché lo
        hace— lo convierte en un rechazo `INTERNAL` en vez de propagarlo. Correcto
        para producción y venenoso aquí: sin esta comprobación, «no hay grabaciones»
        se contaba como «el modelo falló las 57» y la métrica publicaba un 0,0 sobre
        un modelo al que nadie llegó a preguntar. Es el mismo error que ya apareció
        en `G-RECOVERY`, y se cierra igual.
        """
        try:
            salida = run_loop(
                str(caso["pregunta"]),
                provider=provider,
                validate=valida_para(str(caso.get("rol", "analyst"))),
                prompt_id=prompt.prompt_id,
                prompt_version=prompt.version,
            )
        except KeyError as falta:
            problemas.append(f"{caso['id']}: {falta.args[0]}")
            return None
        if salida.rejection is not None and salida.rejection.rule_id == "INTERNAL":
            problemas.append(
                f"{caso['id']}: el ciclo acabó en INTERNAL ({salida.rejection.message}). "
                "Es un fallo de MEDIDA, no del modelo: no puede contar como error"
            )
            return None
        return salida

    # ------------------------------------------------ los 10 de rechazo ---
    for caso in raw.get("rechazos") or []:
        salida = resolver(caso)
        if salida is None:
            continue
        rechazo = salida.rejection
        acierto = (
            not salida.accepted and rechazo is not None and rechazo.rule_id == caso["regla"]
        )
        resultados.append(
            {
                "id": caso["id"],
                "clase": "rechazo",
                "estrato": caso["estrato"],
                "esperado": caso["regla"],
                "obtenido": None if rechazo is None else rechazo.rule_id,
                "acierto": acierto,
            }
        )

    # -------------------------------------------- los de ejecución ---
    for caso in raw.get("ejecucion") or []:
        congelado = FROZEN / f"{caso['id']}.json"
        if not congelado.exists():
            continue  # arbitrio de Samuel: sin referencia no hay nada que comparar
        salida = resolver(caso)
        if salida is None:
            continue
        esperado = json.loads(congelado.read_text(encoding="utf-8"))
        acierto = False
        motivo = "el sistema no llegó a ejecutar"
        if salida.accepted and isinstance(salida.query, ValidatedQuery):
            try:
                filas = executor.engine.execute(salida.query)
                obtenido = como_tabla(
                    list(filas.columns),
                    [[normalizar(v) for v in fila] for fila in filas.rows],
                )
                veredicto = compare(obtenido, como_tabla(esperado["columns"], esperado["rows"]))
                acierto, motivo = veredicto.equal, veredicto.reason
            except Exception as fallo:
                motivo = f"la consulta generada no corrió: {type(fallo).__name__}"
        elif salida.rejection is not None:
            motivo = f"rechazada por {salida.rejection.rule_id}"
        resultados.append(
            {
                "id": caso["id"],
                "clase": "ejecucion",
                "estrato": caso["estrato"],
                "acierto": acierto,
                "motivo": motivo,
                "intentos": len(salida.attempts),
            }
        )

    aciertos = sum(1 for r in resultados if r["acierto"])
    total = len(resultados)
    ratio = round(aciertos / total, 4) if total else 0.0
    low, high = wilson(aciertos, total)

    por_estrato: dict[str, dict[str, int]] = {}
    for r in resultados:
        fila = por_estrato.setdefault(str(r["estrato"]), {"n": 0, "aciertos": 0})
        fila["n"] += 1
        fila["aciertos"] += int(bool(r["acierto"]))

    informe = {
        "contract_version": CONTRACT_VERSION,
        "run_id": f"exec-{dt.datetime.now(tz=dt.UTC).strftime('%Y%m%dT%H%M%SZ')}",
        "project": "data-warden-03",
        "suite": "exec-accuracy",
        "created_at": dt.datetime.now(tz=dt.UTC).isoformat().replace("+00:00", "Z"),
        "environment": {
            "hardware": "MacBook Pro M4 Max, 36 GB unificada, macOS 26.5",
            "python": platform.python_version(),
            # `deterministic` es TRUE solo si todo salió de casetes. En un refresco es
            # false, y decirlo importa: un número que llamó al modelo no se repite.
            "deterministic": not args.refresh,
            "models": {"generador": f"{tag} ({model_ref(digest)})"},
            "seed": 20260903,
        },
        "dataset": {
            "name": f"cierzo-{perfil}",
            "version": int(raw.get("version", 1)),
            "n_cases": total,
            "n_negative_cases": sum(1 for r in resultados if r["clase"] == "rechazo"),
            "sha256": sha_del_banco(),
        },
        "prompts": [
            {
                "id": prompt.prompt_id,
                "version": prompt.version,
                "sha256": f"sha256:{prompt.sha256}",
                "role": "generador",
            }
        ],
        "metrics": [
            {
                "id": "G-EXEC-ACC",
                "value": ratio,
                "n": total,
                "unit": "ratio",
                "ci95": [round(low, 4), round(high, 4)],
                "per_stratum": por_estrato,
                "raw_path": "evals/reports/exec-accuracy.json",
            }
        ],
        "notes": (
            f"provenance del banco: {raw.get('provenance')}. "
            "Mientras no diga `revisado_humano`, el banco lo escribió el agente "
            "transcribiendo el glosario firmado y NADIE lo ha revisado: el número es "
            "el agente puntuándose contra su propia respuesta correcta."
        ),
    }
    (ROOT / "evals" / "reports" / "exec-accuracy-report.json").write_text(
        json.dumps(informe, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    record(
        "exec-accuracy.json",
        "G-EXEC-ACC",
        value=ratio,
        adicionales={
            "límite inferior del intervalo de Wilson 95 %": round(low, 4),
            "tamaño del conjunto de referencia": float(total),
        },
        detail={
            "provenance": raw.get("provenance"),
            "perfil": perfil,
            "por_estrato": por_estrato,
            "fallos": [r for r in resultados if not r["acierto"]],
            "problemas": problemas,
        },
        command="make eval",
    )

    print(
        f"\neval_exec: {aciertos}/{total} · ratio {ratio} · "
        f"Wilson 95 % [{low:.2f} - {high:.2f}]"
    )
    for estrato, fila in sorted(por_estrato.items()):
        print(f"  {estrato:22} {fila['aciertos']:>3}/{fila['n']:<3}")
    print(f"  procedencia del banco: {raw.get('provenance')}")
    if raw.get("provenance") != "revisado_humano":
        print(
            "  AVISO · el banco NO lo ha revisado Samuel. Este número es el agente "
            "puntuándose contra su propia respuesta correcta."
        )
    if problemas:
        for p in problemas[:5]:
            print(f"  - {p}")
        return 1
    return 0


class _Grabando:
    """Llama al modelo y graba. Solo lo usa `make eval-refresh-exec`."""

    def __init__(self, inner: LocalProvider, tag: str) -> None:
        self._inner = inner
        self._tag = tag
        self._cassettes = RecordedProvider(directory=ROOT / CASSETTE_DIR)
        self.name = "local"

    def generate(self, request: Any) -> str:
        from datawarden.nl2sql.providers import extract_sql

        sql = extract_sql(self._inner.generate(request))
        self._cassettes.record(request, sql, model=self._tag, thinking=False)
        print(f"    · {request.question[:44]:44} intento {request.attempt}", flush=True)
        return sql


if __name__ == "__main__":
    raise SystemExit(main())
