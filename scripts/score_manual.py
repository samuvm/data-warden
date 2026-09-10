#!/usr/bin/env python
"""Puntúa SQL escrito por un cliente EXTERNO contra los resultsets congelados.

**Esto NO es `G-EXEC-ACC` y no escribe su artefacto.** Es para la prueba manual de
Q-009: pegar en un cliente MCP real —Claude Desktop— las preguntas del banco, recoger
el SQL que generó y compararlo con la referencia. Sirve para saber **si un modelo
frontera pasaría de 0,80**, que es lo que decide entre las tres salidas de P-011.

**Por qué no puede ser la métrica.** No es automatizable, no es reproducible por otro y
no está anclado a un modelo de `models.lock`. Publicarlo como `G-EXEC-ACC` sería
publicar un número que nadie puede volver a obtener. Es un INDICIO, y sale etiquetado.

Distingue tres desenlaces, y el tercero es el que este script existe para no esconder:

- **acierto**: el resultset coincide según `docs/spec/resultset-equality.md`.
- **superconjunto**: contiene la referencia entera **y trae columnas de más**. Cuenta
  como fallo —la decisión 3 está firmada y empareja por posición— pero se separa,
  porque una respuesta MEJOR que la referencia publicada como error hace que el número
  se lea como fallo del modelo cuando es estrechez de la medida.
- **fallo**: lo demás.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from datawarden.domain.types import Principal, Role, RoleSource, ValidatedQuery
from datawarden.evalsupport.resultset_equality import Table, compare
from datawarden.guard.validator import validate
from eval_exec import _es_superconjunto, normalizar
from gatelib import ROOT

FROZEN = ROOT / "evals" / "golden" / "resultsets"


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "uso: score_manual.py <fichero.json>\n"
            '  con {"Q-E-01": "SELECT ...", "Q-R-01": "SELECT ..."}\n'
            "  Para los casos de rechazo, el SQL es el que el cliente intentó; si no\n"
            "  intentó ninguno porque el sistema le paró antes, pon null."
        )
        return 2

    import duckdb
    import yaml

    respuestas = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
    raw = yaml.safe_load((ROOT / "evals" / "golden" / "questions.yaml").read_text("utf-8"))
    rechazos = {c["id"]: c for c in raw["rechazos"]}
    ejecucion = {c["id"]: c for c in raw["ejecucion"]}

    from datawarden.catalog import SCHEMA_PATH, load_generated
    from datawarden.principal import POLICY_PATH
    from datawarden.principal.policy import load_policy

    schema = load_generated(SCHEMA_PATH)
    policy = load_policy(POLICY_PATH)
    perfil = str(raw.get("perfil", "full"))
    conexion = duckdb.connect(str(ROOT / "datagen" / "out" / f"cierzo-{perfil}.duckdb"), True)

    aciertos = supersets = 0
    filas_informe: list[tuple[str, str, str]] = []

    for cid, sql in respuestas.items():
        caso = rechazos.get(cid) or ejecucion.get(cid)
        if caso is None:
            filas_informe.append((cid, "DESCONOCIDO", "no está en el banco"))
            continue
        who = Principal(
            id="manual", role=Role(caso.get("rol", "analyst")), source=RoleSource.CLI_FLAG
        )

        if cid in rechazos:
            if sql is None:
                filas_informe.append((cid, "?", "el cliente no llegó a intentar SQL"))
                continue
            veredicto = validate(
                str(sql), principal=who, schema=schema, policy=policy, max_rows=50_000
            )
            if isinstance(veredicto, ValidatedQuery):
                filas_informe.append((cid, "FALLO", "el sistema lo ACEPTÓ"))
            elif veredicto.rule_id == caso["regla"]:
                aciertos += 1
                filas_informe.append((cid, "acierto", f"rechazado por {veredicto.rule_id}"))
            else:
                filas_informe.append(
                    (cid, "FALLO", f"esperaba {caso['regla']}, disparó {veredicto.rule_id}")
                )
            continue

        congelado = FROZEN / f"{cid}.json"
        if not congelado.exists():
            filas_informe.append((cid, "-", "sin referencia congelada (arbitrio)"))
            continue
        veredicto = validate(
            str(sql), principal=who, schema=schema, policy=policy, max_rows=50_000
        )
        if not isinstance(veredicto, ValidatedQuery):
            filas_informe.append((cid, "FALLO", f"el guard lo rechazó · {veredicto.rule_id}"))
            continue
        try:
            cursor = conexion.execute(str(sql))
            columnas = [d[0] for d in (cursor.description or [])]
            filas = cursor.fetchall()
        except Exception as fallo:
            filas_informe.append((cid, "FALLO", f"no corre · {type(fallo).__name__}"))
            continue
        obtenido = Table(
            columns=tuple(columnas), rows=[tuple(normalizar(v) for v in f) for f in filas]
        )
        esperado = json.loads(congelado.read_text(encoding="utf-8"))
        referencia = Table(
            columns=tuple(esperado["columns"]), rows=[tuple(r) for r in esperado["rows"]]
        )
        resultado = compare(obtenido, referencia)
        if resultado.equal:
            aciertos += 1
            filas_informe.append((cid, "acierto", ""))
        elif _es_superconjunto(obtenido, referencia):
            supersets += 1
            filas_informe.append(
                (cid, "SUPERCONJ.", f"contiene la referencia + {len(columnas)} columnas")
            )
        else:
            filas_informe.append((cid, "FALLO", resultado.reason))

    conexion.close()
    total = len(filas_informe)
    print(f"score_manual · INDICIO, no es G-EXEC-ACC · perfil {perfil}\n")
    for cid, estado, detalle in filas_informe:
        print(f"  {cid:8} {estado:11} {detalle[:62]}")
    print(f"\n  aciertos estrictos : {aciertos}/{total}")
    print(f"  superconjuntos     : {supersets}  (fallo por la decisión 3, se anota aparte)")
    if total:
        print(
            f"  estricto {aciertos / total:.4f} · "
            f"con superconjuntos {(aciertos + supersets) / total:.4f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
