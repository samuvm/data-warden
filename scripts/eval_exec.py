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
from datawarden.nl2sql.providers import LocalProvider, RecordedProvider, cassette_dir_for
from gatelib import PROYECTO, ROOT, _hardware, record, rol_contrato, wilson

QUESTIONS = ROOT / "evals" / "golden" / "questions.yaml"
FROZEN = ROOT / "evals" / "golden" / "resultsets"
#: La versión del CONTRATO, no la del proyecto — y es un entero por `const: 1`.
#: Decía "1.0.0", que parecía más informativo y era sencillamente inválido:
#: `check_eval_reports.py` lo cazó la primera vez que se ejecutó. El 02 lee este campo
#: para saber qué forma esperar, así que aquí no cabe una versión propia.
CONTRACT_VERSION = 1
GATE_PEPPER = "pimienta-del-gate-solo-para-medir-g-pii-leak"


def sha_del_banco() -> str:
    """El sha del golden set. **Comparar dos informes sobre bancos distintos engaña.**"""
    return "sha256:" + hashlib.sha256(QUESTIONS.read_bytes()).hexdigest()


def como_tabla(columnas: list[str], filas: list[Any]) -> Table:
    return Table(columns=tuple(columnas), rows=[tuple(f) for f in filas])


def _es_superconjunto(obtenido: Table, referencia: Table) -> bool:
    """Si la respuesta CONTIENE la referencia y además trae columnas de más.

    Se comprueba por VALORES y no por nombres, igual que hace el contrato: para cada
    columna de la referencia tiene que existir una columna del resultado con
    exactamente los mismos valores en el mismo orden de filas.
    """
    if len(obtenido.rows) != len(referencia.rows) or not referencia.rows:
        return False
    if len(obtenido.columns) <= len(referencia.columns):
        return False
    columnas_obtenidas = [
        [fila[i] for fila in obtenido.rows] for i in range(len(obtenido.columns))
    ]
    for indice in range(len(referencia.columns)):
        buscada = [fila[indice] for fila in referencia.rows]
        if buscada not in columnas_obtenidas:
            return False
    return True


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
        else RecordedProvider(directory=ROOT / cassette_dir_for(tag))
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
        # Las reglas que saltaron en CUALQUIER intento del ciclo, el último incluido.
        reglas = [a.rejection.rule_id for a in salida.attempts if a.rejection is not None]
        # **P-014, aprobada por Samuel el 2026-09-11.** Un caso de rechazo acierta si la
        # regla DECLARADA saltó en algún punto del ciclo, no solo si el ciclo termina en
        # rechazo.
        #
        # Antes se puntuaba el desenlace final, y eso ponía a dos metas a tirar en
        # sentidos opuestos: el ciclo existe para RECUPERARSE de un rechazo, así que un
        # modelo que se recupera bien —la columna prohibida se bloquea, el mensaje dice
        # la alternativa, el modelo la usa— terminaba en una aceptación y contaba como
        # fallo. Un modelo que mejoraba en `G-RECOVERY` empeoraba en `G-EXEC-ACC`. Medido
        # con el 26B: en 5 de los 8 casos de rechazo fallados la regla declarada SÍ
        # había saltado (Q-R-01, 02 y 03 con R008; Q-R-04 y 05 con R012). Es lo que
        # Samuel vio a mano en Claude Desktop con Q-R-01 y lo que el sistema está hecho
        # para hacer.
        #
        # **Tiene que ser la regla declarada, no una cualquiera.** Q-R-09 declara R006 y
        # le salta R008: sigue fallando, que es lo correcto. Y un caso donde no salta
        # ninguna regla —el modelo nunca llegó a pedir lo prohibido— también.
        #
        # Se decidió DESPUÉS de ver el número (sube de 0,4386 a 0,5263) y por eso fue
        # una propuesta con el número escrito por delante, no un cambio del agente.
        acierto = caso["regla"] in reglas
        resultados.append(
            {
                "id": caso["id"],
                "clase": "rechazo",
                "estrato": caso["estrato"],
                "esperado": caso["regla"],
                "obtenido": None if rechazo is None else rechazo.rule_id,
                # La EVIDENCIA de `acierto`, publicada. Nació como diagnóstico para
                # distinguir «el guard nunca vio nada prohibido» de «el guard lo paró y
                # el modelo corrigió», que son cosas opuestas y antes salían igual
                # (`obtenido: null`). Desde P-014 es la base del acierto, y se publica
                # para que cualquiera pueda ver POR QUÉ acertó cada caso: una condición
                # relajada sin su evidencia al lado sería un número que hay que creerse.
                "reglas_en_el_ciclo": reglas,
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

    # **El contrato exige NÚMEROS en `per_stratum`, no objetos.** Aquí se publicaban
    # `{"n": 20, "aciertos": 5}` y el esquema pide `additionalProperties: number`: el
    # panel del 02 esperaba una serie por estrato y recibía un diccionario. Se publica
    # el ratio, que es lo comparable entre estratos de tamaño distinto, y los conteos
    # crudos siguen enteros en `raw_path` — que es exactamente para lo que existe.
    ratio_por_estrato = {
        nombre: round(fila["aciertos"] / fila["n"], 4) if fila["n"] else 0.0
        for nombre, fila in sorted(por_estrato.items())
    }

    informe = {
        "contract_version": CONTRACT_VERSION,
        "run_id": f"exec-{dt.datetime.now(tz=dt.UTC).strftime('%Y%m%dT%H%M%SZ')}",
        "project": PROYECTO,
        "suite": "exec-accuracy",
        "created_at": dt.datetime.now(tz=dt.UTC).isoformat().replace("+00:00", "Z"),
        "environment": {
            "hardware": _hardware(),
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
                # ENTERA: el contrato pide `integer` y el frontmatter la trae como
                # texto. Se escribía tal cual y el informe no validaba.
                "version": int(prompt.version),
                "sha256": f"sha256:{prompt.sha256}",
                # En INGLÉS y del enumerado del contrato. Dentro del repo el rol se
                # llama `generador`; el 02 no sabe español.
                "role": rol_contrato("generador"),
            }
        ],
        "metrics": [
            {
                "id": "G-EXEC-ACC",
                "value": ratio,
                "n": total,
                "unit": "ratio",
                "ci95": [round(low, 4), round(high, 4)],
                "per_stratum": ratio_por_estrato,
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
            "superconjuntos": [
                r["id"]
                for r in resultados
                if not r["acierto"] and "SUPERCONJUNTO" in str(r.get("motivo", ""))
            ],
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
    supersets = [
        r
        for r in resultados
        if not r["acierto"] and "SUPERCONJUNTO" in str(r.get("motivo", ""))
    ]
    if supersets:
        print(
            f"  de los fallos, {len(supersets)} son SUPERCONJUNTOS: contienen la "
            "referencia y traen columnas de más. Cuentan como fallo (decisión 3 del "
            "contrato firmado) y se publican aparte."
        )
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
        self._cassettes = RecordedProvider(directory=ROOT / cassette_dir_for(tag))
        self.name = "local"

    def generate(self, request: Any) -> str:
        from datawarden.nl2sql.providers import extract_sql

        sql = extract_sql(self._inner.generate(request))
        self._cassettes.record(request, sql, model=self._tag, thinking=False)
        print(f"    · {request.question[:44]:44} intento {request.attempt}", flush=True)
        return sql


if __name__ == "__main__":
    raise SystemExit(main())
