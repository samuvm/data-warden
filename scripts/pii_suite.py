#!/usr/bin/env python
"""`G-PII-LEAK` · **cero fugas en las tres superficies.** Es un AXIOMA.

`propuesta_admisible: false`: este número no se negocia ni con dos intentos medidos.

**Cómo se mide, y por qué así.** No se comprueba que el código diga que enmascara: se
ejecuta la consulta **dos veces contra el dataset real** —una como `admin`, que lo ve
todo, y otra como el rol restringido— y se exige que **ningún valor real aparezca en la
salida del rol restringido**. Comprobar el árbol probaría el reescritor; comprobar los
valores prueba el sistema. Es la misma diferencia que hay entre mirar el veredicto y
mirar el contador del motor en `G-BUDGET-ESCAPE`.

**Las tres superficies son tres ataques distintos**, y la meta las nombra porque una
sola no basta:

1. **Proyección** — la columna sale en el `SELECT`, directa o por alias. Se exige que
   salga ENMASCARADA, no que se rechace: un guard que rechaza todo no deja trabajar.
2. **Predicado** — `WHERE iban LIKE 'ES91%'` no devuelve el IBAN y **filtra por él**.
   Es exfiltración bit a bit y se exige RECHAZO: no hay máscara que arregle un
   predicado, porque el atacante no quiere ver el valor, quiere adivinarlo.
3. **Agregación de grupo único** — `GROUP BY national_id` expone la columna por
   cardinalidad aunque nunca se proyecte. También rechazo.

Una fuga es cualquiera de estas tres: un valor real en la salida, un predicado sobre
columna protegida que se acepta, o una agregación de grupo único que pasa.
"""

from __future__ import annotations

import pathlib
import sys
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gatelib import ROOT, record

sys.path.insert(0, str(ROOT / "src"))

#: Copiada letra a letra de `docs/GOALS.yaml`: `goals_check.py` compara la etiqueta por
#: igualdad exacta de cadena, y una tilde de más produce «falta el umbral adicional».
LABEL_SUPERFICIES = "superficies cubiertas (proyección, predicado, agregación)"

#: Sin pimienta no hay enmascarado, y sin enmascarado esta suite mediría otra cosa.
#: Es de pruebas y se declara como tal: la de producción sale de la variable de
#: entorno y jamás del código.
GATE_PEPPER = "pimienta-del-gate-solo-para-medir-g-pii-leak"


@dataclass(frozen=True, slots=True)
class Leak:
    """Una fuga, con lo que hace falta para reproducirla."""

    surface: str
    role: str
    column: str
    sql: str
    why: str


def main() -> int:
    from datawarden.catalog import SCHEMA_PATH, load_generated
    from datawarden.catalog.statistics import load as load_stats
    from datawarden.cost import STATISTICS_PATH
    from datawarden.domain.types import Principal, Role, RoleSource
    from datawarden.engines.duckdb_engine import DuckDBEngine
    from datawarden.mask.config import MaskConfig
    from datawarden.mask.macro import macro_ddl
    from datawarden.mask.pipeline import screen_and_mask
    from datawarden.principal import BUDGETS_PATH, POLICY_PATH
    from datawarden.principal.budgets import load_budgets
    from datawarden.principal.policy import Level, load_policy

    database = ROOT / "datagen" / "out" / "cierzo-dev.duckdb"
    if not database.exists():
        print(
            f"pii_suite: FALLO · no existe {database.relative_to(ROOT)}.\n"
            "  La fuga se mide EJECUTANDO contra datos, no razonando sobre el árbol.\n"
            "  Genera el dataset con `make dataset PROFILE=dev`."
        )
        return 1

    schema = load_generated(SCHEMA_PATH)
    policy = load_policy(POLICY_PATH)
    budgets = load_budgets(BUDGETS_PATH)
    stats = load_stats(STATISTICS_PATH)
    config = MaskConfig(pepper=GATE_PEPPER)
    engine = DuckDBEngine(database, setup_sql=(macro_ddl(config),))

    def run(sql: str, role: Role) -> tuple[object, str]:
        who = Principal(id=f"pii-{role.value}", role=role, source=RoleSource.CLI_FLAG)
        out = screen_and_mask(
            sql,
            principal=who,
            schema=schema,
            policy=policy,
            budgets=budgets,
            stats=stats,
            config=config,
        )
        if out.query is None:
            return None, "rechazada"
        return engine.execute(out.query), "ejecutada"

    leaks: list[Leak] = []
    checked = {"proyeccion": 0, "predicado": 0, "agregacion": 0}

    # Toda fila de la política que NO sea `allow` para algún rol es una fila a probar.
    # Se recorre la política COMPILADA y no una lista escrita a mano: el día que
    # negocio añada una columna protegida, esta suite la prueba sola.
    for name in sorted(policy.columns):
        table, _, column = name.partition(".")
        for role in Role:
            level = policy.level_for(name, role)
            if level is Level.ALLOW:
                continue

            # ---------------------------------------------------- 1 · proyección ---
            checked["proyeccion"] += 1
            sql = f"SELECT {column} FROM {table} LIMIT 50"
            rows, status = run(sql, role)
            if status == "ejecutada":
                if level is Level.DENY:
                    leaks.append(Leak("proyeccion", role.value, name, sql, "deny se ejecutó"))
                else:
                    reales, _ = run(sql, Role.ADMIN)
                    fuga = _shared_values(rows, reales)
                    if fuga:
                        leaks.append(
                            Leak(
                                "proyeccion",
                                role.value,
                                name,
                                sql,
                                f"valor real en la salida: {fuga!r}",
                            )
                        )

            # ----------------------------------------------------- 2 · predicado ---
            checked["predicado"] += 1
            pred = f"SELECT count(*) AS n FROM {table} WHERE CAST({column} AS TEXT) LIKE 'a%'"
            _, status = run(pred, role)
            if status == "ejecutada":
                leaks.append(Leak("predicado", role.value, name, pred, "predicado aceptado"))

            # ---------------------------------------------------- 3 · agregación ---
            checked["agregacion"] += 1
            agg = (
                f"SELECT {column}, count(*) AS n FROM {table} "
                f"GROUP BY {column} HAVING count(*) = 1 LIMIT 50"
            )
            _, status = run(agg, role)
            if status == "ejecutada":
                leaks.append(Leak("agregacion", role.value, name, agg, "grupo único aceptado"))

    engine.close()

    surfaces = sum(1 for n in checked.values() if n > 0)
    record(
        "pii-leak.json",
        "G-PII-LEAK",
        value=len(leaks),
        adicionales={LABEL_SUPERFICIES: surfaces},
        detail={
            "comprobaciones_por_superficie": checked,
            "filas_de_politica_probadas": len(policy.columns),
            "fugas": [vars(leak) for leak in leaks],
            "como_se_mide": (
                "Se EJECUTA contra el dataset dos veces —como admin y como el rol "
                "restringido— y se exige que ningún valor real aparezca en la salida "
                "del segundo. Comprobar el árbol probaría el reescritor; comparar los "
                "valores prueba el sistema."
            ),
            "perfil": "dev",
        },
        command="make pii-suite",
    )

    if leaks:
        print(f"pii_suite: FALLO · {len(leaks)} fugas\n")
        for leak in leaks[:20]:
            print(f"  · [{leak.surface}] {leak.role} / {leak.column}: {leak.why}")
            print(f"      {leak.sql}")
        return 1
    total = sum(checked.values())
    print(
        f"pii_suite: ok · 0 fugas en {total} comprobaciones sobre {surfaces} "
        f"superficies (proyección {checked['proyeccion']}, predicado "
        f"{checked['predicado']}, agregación {checked['agregacion']})"
    )
    return 0


def _shared_values(masked: object, real: object) -> object | None:
    """El primer valor real que se coló en la salida enmascarada, si lo hay.

    Se comparan los VALORES y no las cadenas de SQL: dos consultas idénticas salvo la
    máscara producen resultsets que solo se parecen en lo que no se protegió. Un valor
    compartido en la columna enmascarada es, por definición, la fuga.
    """
    if masked is None or real is None:
        return None
    izquierda = {v for row in getattr(masked, "rows", ()) for v in row if v is not None}
    derecha = {v for row in getattr(real, "rows", ()) for v in row if v is not None}
    comunes = izquierda & derecha
    return next(iter(sorted(comunes, key=str)), None) if comunes else None


if __name__ == "__main__":
    sys.exit(main())
