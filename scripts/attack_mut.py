#!/usr/bin/env python
"""`G-WRITE-BLOCK` · mutación de AST. **>= 2.000 mutantes, cero evasiones.**

Es la mitad del número publicable, junto al holdout. Y es la mitad que no depende de
que a nadie se le ocurra un ataque: **una mutación de un ataque sigue siendo un
ataque**, así que si `DELETE FROM t` se rechaza, también tiene que rechazarse
envuelto en una CTE, con un comentario entre tokens, con las mayúsculas cambiadas y
unido por `UNION` a una consulta legítima.

Dos invariantes, y hacen falta los dos:

1. **Un mutante de un ataque se rechaza.** Si alguno pasa, es una evasión REAL: se
   convierte en `rule_id` nuevo o en caso permanente del corpus, nunca en un `xfail`.
2. **Un mutante de una consulta legítima que se ACEPTA sigue cumpliendo los
   invariantes**: ni nodo de escritura, ni estrella sin expandir. Sin esta segunda
   mitad, la meta la satisfaría un guard que rechazara todo.
"""

from __future__ import annotations

import json
import pathlib
import sys

import yaml
from sqlglot import expressions as exp

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
from datawarden.catalog import SCHEMA_PATH, load_generated
from datawarden.domain.types import (
    Principal,
    RejectionReason,
    Role,
    RoleSource,
    ValidatedQuery,
)
from datawarden.evalsupport.mutate import mutants
from datawarden.guard.rules.r010_no_write_node import WRITE_NODES
from datawarden.guard.validator import validate
from datawarden.principal import BUDGETS_PATH, POLICY_PATH
from datawarden.principal.budgets import load_budgets
from datawarden.principal.policy import load_policy
from gatelib import ROOT, record

#: Cuatro semillas fijas y dos rondas. `G-WRITE-BLOCK` exige >= 2.000 mutantes, y
#: con una sola semilla salían 612: el número no se alcanza mutando más veces la
#: misma consulta —eso da variaciones parecidas— sino con semillas distintas, que
#: es lo que cambia QUÉ mutación se aplica dónde. Fijas, no aleatorias: un
#: contraejemplo que no se puede reproducir es una anécdota.
SEEDS = (20260902, 20260903, 20260904, 20260905)
ROUNDS = 2

#: Semillas legítimas. La otra dirección de la prueba: lo que se acepta tiene que
#: seguir cumpliendo los invariantes después de mutarlo.
LEGITIMATE = (
    ("analyst", "SELECT country_code, count(*) AS n FROM dim_customer GROUP BY country_code"),
    ("analyst", "SELECT age_band, count(*) AS n FROM dim_customer GROUP BY age_band"),
    ("finance", "SELECT sum(gross_eur_minor) AS v FROM fact_settlement_batch"),
    ("ops", "SELECT customer_id, kyc_status FROM dim_customer LIMIT 10"),
    ("admin", "SELECT currency_code, minor_units FROM ref_currency"),
)


def _guard(sql: str, role: Role, schema, policy, budgets):
    return validate(
        sql,
        principal=Principal(id="mut", role=role, source=RoleSource.CLI_FLAG),
        schema=schema,
        policy=policy,
        max_rows=budgets.max_rows(role),
    )


def _violates_invariants(query: ValidatedQuery) -> str | None:
    if query.ast.find(*WRITE_NODES) is not None:
        return "el árbol aceptado contiene un nodo de escritura"
    if [s for s in query.ast.find_all(exp.Star) if not isinstance(s.parent, exp.Func)]:
        return "el árbol aceptado conserva una estrella sin expandir"
    return None


def main() -> int:
    schema = load_generated(SCHEMA_PATH)
    policy = load_policy(POLICY_PATH)
    budgets = load_budgets(BUDGETS_PATH)
    notebook = yaml.safe_load((ROOT / "attacks" / "dev-notebook.yaml").read_text("utf-8"))

    escapes: list[dict[str, object]] = []
    violations: list[dict[str, object]] = []
    total = 0
    from_attacks = 0
    from_legit = 0

    for attack in notebook["ataques"]:
        if "sql_generado" in attack:
            # Las bombas de AST no se mutan: mutarlas solo las hace más grandes y
            # el resultado es el mismo rechazo por tamaño, sin información nueva.
            continue
        role = Role(attack["rol"])
        for seed in SEEDS:
            for name, mutated in mutants(attack["sql"], rounds=ROUNDS, seed=seed):
                total += 1
                from_attacks += 1
                verdict = _guard(mutated, role, schema, policy, budgets)
                if not isinstance(verdict, RejectionReason):
                    escapes.append(
                        {
                            "attack": attack["id"],
                            "mutation": name,
                            "seed": seed,
                            "sql": mutated[:400],
                        }
                    )

    for role_name, sql in LEGITIMATE:
        role = Role(role_name)
        for seed in SEEDS:
            for name, mutated in mutants(sql, rounds=ROUNDS, seed=seed):
                total += 1
                from_legit += 1
                verdict = _guard(mutated, role, schema, policy, budgets)
                if isinstance(verdict, ValidatedQuery):
                    problem = _violates_invariants(verdict)
                    if problem is not None:
                        violations.append(
                            {
                                "seed": sql,
                                "mutation": name,
                                "problem": problem,
                                "sql": mutated[:400],
                            }
                        )

    record(
        "attack-mut.json",
        "G-WRITE-BLOCK",
        value=len(escapes) + len(violations),
        adicionales={
            "casos de holdout superados": _holdout_score(),
            "mutantes de AST evaluados": total,
        },
        detail={
            "mutants_total": total,
            "from_attacks": from_attacks,
            "from_legitimate": from_legit,
            "escapes": escapes[:20],
            "invariant_violations": violations[:20],
            "seeds": list(SEEDS),
            "rounds": ROUNDS,
        },
        command="make attack-mut",
    )

    print(
        f"attack_mut: {total} mutantes evaluados ({from_attacks} de ataques, "
        f"{from_legit} de consultas legítimas)"
    )
    if escapes:
        print(
            f"\n  {len(escapes)} EVASIONES. Cada una es un rule_id nuevo o un caso permanente:"
        )
        for e in escapes[:10]:
            print(f"    · {e['attack']} + {e['mutation']}: {e['sql'][:120]}")
    if violations:
        print(f"\n  {len(violations)} violaciones de invariante sobre mutantes ACEPTADOS:")
        for v in violations[:10]:
            print(f"    · {v['mutation']}: {v['problem']}")
    if escapes or violations:
        return 1
    print("  cero evasiones y cero violaciones de invariante")
    return 0


def _holdout_score() -> int:
    """Cuántos casos del holdout pasan, leído del artefacto que deja `make attack-holdout`.

    **NO se lee el holdout.** Se lee su INFORME, que es un número. El agente tiene
    `tests/holdout/**` denegado y esa prohibición incluye no pedirlo por
    conversación; leer el resultado agregado es justo lo contrario de leer los casos.
    """
    path = ROOT / "evals" / "reports" / "attack-holdout.json"
    if not path.exists():
        return 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    passed = payload.get("metas", {}).get("G-WRITE-BLOCK", {}).get("adicionales", {})
    return int(passed.get("casos de holdout superados", 0))


if __name__ == "__main__":
    sys.exit(main())
