#!/usr/bin/env python
"""Congela el resultset de referencia de cada caso. **Sin congelar no hay medida.**

`G-EXEC-ACC` compara lo que devuelve el SQL del modelo con lo que devuelve el SQL de
referencia, y esa comparación tiene que ser **reproducible en otra máquina y en otro
mes**. Volver a ejecutar la referencia en cada medida no lo es: el día que alguien
regenere el dataset con otra semilla, o cambie el perfil, las dos mitades se moverían
a la vez y la comparación seguiría saliendo verde sobre datos distintos.

Congelado, un cambio del dataset **rompe** la comparación, que es lo correcto: obliga
a mirar qué cambió en vez de dejar que el número se deslice sin avisar.

**Se congela el resultset NORMALIZADO**, no el crudo. `docs/spec/resultset-equality.md`
define qué significa «el mismo resultado» —el orden de filas no cuenta salvo que la
referencia lleve `ORDER BY`, los nombres de columna solo si la pregunta los pedía— y
guardar el crudo obligaría a reimplementar esa decisión en cada comparación.

**No lo firma este script.** Congelar es mecánico; decidir que el número congelado es
la respuesta correcta es de Samuel, y `provenance` lo sigue diciendo hasta que lo haga.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
from typing import Any

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gatelib import ROOT

QUESTIONS = ROOT / "evals" / "golden" / "questions.yaml"
FROZEN = ROOT / "evals" / "golden" / "resultsets"

#: Tope de filas que se congelan por caso. Ninguna referencia del banco pasa de 100
#: —lo llevan en su `LIMIT`— y el tope existe para que un caso mal escrito no meta
#: medio almacén en el repositorio sin que nadie lo note al revisar el diff.
MAX_FILAS = 500


def normalizar(valor: Any) -> Any:
    """A JSON, sin perder la diferencia entre `null` y cero.

    Las fechas se serializan en ISO y los decimales como cadena: un `float` de Python
    redondea distinto según la plataforma, y un resultset congelado que cambia de
    máquina no es una referencia.
    """
    import datetime as dt
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
    raw = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))
    perfil = str(raw.get("perfil", "dev"))
    database = ROOT / "datagen" / "out" / f"cierzo-{perfil}.duckdb"
    if not database.exists():
        print(
            f"freeze_questions: FALLO · no existe {database.relative_to(ROOT)}.\n"
            f"  El banco declara `perfil: {perfil}`. Genéralo con "
            f"`make dataset PROFILE={perfil}`."
        )
        return 1

    import duckdb

    conexion = duckdb.connect(str(database), read_only=True)
    FROZEN.mkdir(parents=True, exist_ok=True)
    congelados = 0
    sin_sql: list[str] = []
    problemas: list[str] = []

    for caso in raw.get("ejecucion") or []:
        sql = caso.get("sql_referencia")
        if not sql:
            sin_sql.append(str(caso["id"]))
            continue
        cursor = conexion.execute(str(sql))
        columnas = [d[0] for d in (cursor.description or [])]
        filas = cursor.fetchall()
        if len(filas) > MAX_FILAS:
            problemas.append(
                f"{caso['id']}: la referencia devuelve {len(filas)} filas y el tope es "
                f"{MAX_FILAS}. Acota la pregunta: un banco no es un volcado"
            )
            continue
        payload = {
            "id": caso["id"],
            "pregunta": caso["pregunta"],
            "rol": caso.get("rol", "analyst"),
            "perfil": perfil,
            "sql_referencia": " ".join(str(sql).split()),
            "glosario": caso.get("glosario"),
            # El sha del SQL: si alguien lo edita sin recongelar, se nota.
            "sql_sha256": "sha256:"
            + hashlib.sha256(" ".join(str(sql).split()).encode("utf-8")).hexdigest(),
            "columns": columnas,
            "rows": [[normalizar(v) for v in fila] for fila in filas],
            "row_count": len(filas),
        }
        destino = FROZEN / f"{caso['id']}.json"
        destino.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        congelados += 1

    conexion.close()
    print(f"freeze_questions: {congelados} resultsets congelados · perfil {perfil}")
    if sin_sql:
        print(f"  sin SQL de referencia (arbitrio de Samuel): {sin_sql}")
    if problemas:
        for p in problemas:
            print(f"  · {p}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
