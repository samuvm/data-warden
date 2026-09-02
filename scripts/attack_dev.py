#!/usr/bin/env python
"""`G-WRITE-BLOCK-DEV` · el cuaderno de ataque de desarrollo. **Higiene, no evidencia.**

La meta lo dice con esas palabras y el script lo repite al terminar, porque es la
clase de número que alguien copia al README sin leer la letra pequeña: **las reglas
se escribieron PARA parar estos veinticinco casos**, así que pasar el 100 % es medir
sobre el conjunto de entrenamiento. `PROJECT.md` pedía «100 % sobre una suite de 40
intentos» y su propio hito 1 explicaba por qué eso no mide nada.

El número publicable es el del HOLDOUT (`G-WRITE-BLOCK`), que escribe un subagente
que no ha visto ni el guard ni este fichero, más la mutación de AST.

Y cada ataque declara **qué regla debe pararlo**. Que lo pare otra es un fallo, no un
aprobado: un rechazo por la regla equivocada es un acierto por casualidad, y el día
que esa otra regla cambie, la familia queda sin cubrir y nadie se entera.
"""

from __future__ import annotations

import json
import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from datawarden.catalog import SCHEMA_PATH, load_generated
from datawarden.domain.types import (
    Principal,
    RejectionReason,
    Role,
    RoleSource,
)
from datawarden.guard.validator import validate
from datawarden.principal import BUDGETS_PATH, POLICY_PATH
from datawarden.principal.budgets import load_budgets
from datawarden.principal.policy import load_policy
from gatelib import ROOT, record

NOTEBOOK = ROOT / "attacks" / "dev-notebook.yaml"


def generated_sql(kind: str, n: int) -> str:
    """Las mismas bombas que el corpus, construidas y no pegadas."""
    if kind == "estrella_de_or":
        return "SELECT customer_sk FROM dim_customer WHERE " + " OR ".join(
            f"customer_sk = {i}" for i in range(n)
        )
    if kind == "subconsultas_anidadas":
        return (
            "SELECT country_code FROM (" * n
            + "SELECT country_code FROM dim_customer"
            + "".join(f") AS s{i}" for i in range(n))
        )
    if kind == "suma_de_literales":
        # MUCHOS NODOS EN POCOS CARACTERES, y eso es lo que hace falta. Los otros
        # generadores producen SQL tan largo que el corte por LONGITUD DE ENTRADA lo
        # rechaza antes de parsear, así que el cuerpo de R013 no llegaba a
        # ejecutarse nunca: la regla estaba «probada» por un caso que paraba otro
        # mecanismo. Lo destapó la mutación, que dejaba vivos casi todos sus
        # mutantes. Aquí son dos caracteres por nodo.
        return "SELECT " + "+".join(["1"] * n) + " AS n FROM dim_customer"
    if kind == "cadena_de_concat":
        return "SELECT " + " || ".join(["country_code"] * n) + " AS c FROM dim_customer"
    message = f"generador desconocido en el cuaderno: {kind!r}"
    raise ValueError(message)


def main() -> int:
    schema = load_generated(SCHEMA_PATH)
    policy = load_policy(POLICY_PATH)
    budgets = load_budgets(BUDGETS_PATH)
    notebook = yaml.safe_load(NOTEBOOK.read_text(encoding="utf-8"))

    rows: list[dict[str, object]] = []
    blocked = 0
    wrong_rule = 0
    for attack in notebook["ataques"]:
        role = Role(attack["rol"])
        sql = (
            generated_sql(attack["sql_generado"], int(attack["parametro"]))
            if "sql_generado" in attack
            else attack["sql"]
        )
        verdict = validate(
            sql,
            principal=Principal(id="attack", role=role, source=RoleSource.CLI_FLAG),
            schema=schema,
            policy=policy,
            max_rows=budgets.max_rows(role),
        )
        rejected = isinstance(verdict, RejectionReason)
        actual = verdict.rule_id if rejected else None
        expected = attack["espera"]
        ok = rejected and actual == expected
        blocked += int(bool(rejected))
        wrong_rule += int(bool(rejected and actual != expected))
        rows.append(
            {
                "id": attack["id"],
                "family": attack["familia"],
                "role": role.value,
                "expected_rule": expected,
                "actual_rule": actual,
                "blocked": rejected,
                "right_rule": ok,
            }
        )

    total = len(rows)
    passed = sum(1 for r in rows if r["right_rule"])
    record(
        "attack-dev.json",
        "G-WRITE-BLOCK-DEV",
        value=passed,
        detail={
            "total": total,
            "blocked": blocked,
            "wrong_rule": wrong_rule,
            "attacks": rows,
            "aviso": (
                "HIGIENE, NO EVIDENCIA. Las reglas se escribieron para parar estos "
                "casos: pasar el 100 % es medir sobre el conjunto de entrenamiento. "
                "El número publicable es G-WRITE-BLOCK (holdout + mutación)."
            ),
        },
        command="make attack-dev",
    )

    for row in rows:
        if row["right_rule"]:
            continue
        estado = f"lo paró {row['actual_rule']}" if row["blocked"] else "PASÓ EL GUARD"
        print(
            f"  FALLO {row['id']} ({row['family']}): esperaba {row['expected_rule']}, {estado}"
        )

    print(
        f"\nattack_dev: {passed}/{total} parados por la regla correcta "
        f"({blocked}/{total} parados por alguna)"
    )
    print(
        "  AVISO: higiene, no evidencia. Este número NO se publica como métrica de\n"
        "  seguridad. El publicable es G-WRITE-BLOCK: holdout + mutación de AST."
    )
    if passed != total:
        return 1
    (ROOT / "evals" / "reports" / "attack-dev-detail.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
