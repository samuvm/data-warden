#!/usr/bin/env python
"""Mide las nueve trampas declaradas del glosario. `make dataset-traps`.

Nace de la corrección **G-5** de la firma de Q-004, y de una frase de Samuel que
vale por todo el script:

> «cada porcentaje se puede verificar con una consulta contra `datagen/out/full`, y
> eso es exactamente lo que hay que hacer antes de firmar. **Una trampa cuyo número
> no se ha vuelto a medir desde que se escribió es folclore.**»

Cada trampa lleva su consulta, su valor declarado y su tolerancia. Sale ROJO si
alguna discrepa, y el informe queda en `evals/reports/dataset-traps.json` para que
el número que se publique en el README tenga fecha, perfil y comando al lado.

**Por qué la tolerancia no es cero.** El generador sortea con semilla fija, así que
sobre el MISMO perfil el número es exactamente reproducible; pero los tres perfiles
tienen tamaños distintos y una trampa que dependa de la cola de una distribución se
mueve algunas décimas entre ellos. La tolerancia se declara por trampa y es parte
del dato, no una manga ancha genérica.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gatelib import ROOT


@dataclass(frozen=True, slots=True)
class Trap:
    """Una trampa del glosario con la consulta que la mide."""

    key: str
    glossary: str
    sql: str
    declared_pct: float | None
    tolerance_pct: float


TRAPS: tuple[Trap, ...] = (
    Trap(
        key="ingresos_contando_intentos",
        glossary="Contar ingresos contando filas de fact_payment_attempt: +24 %",
        # `sum(amount_eur_minor)` sobre los intentos frente al dinero de los pagos
        # que de verdad se cobraron. La diferencia son los reintentos y lo rechazado.
        sql="""
            SELECT 100.0 * (
                (SELECT sum(amount_eur_minor) FROM fact_payment_attempt WHERE NOT is_test)
                - (SELECT sum(captured_eur_minor) FROM v_payment_intent)
            ) / (SELECT sum(captured_eur_minor) FROM v_payment_intent)
        """,
        declared_pct=24.0,
        tolerance_pct=6.0,
    ),
    Trap(
        key="scd2_por_clave_natural",
        glossary="Unir con dim_merchant por merchant_id en vez de merchant_sk: +53 % de filas",
        sql="""
            SELECT 100.0 * (
                (SELECT count(*) FROM fact_settlement_batch b
                   JOIN dim_merchant m ON m.merchant_id = b.merchant_id)
                - (SELECT count(*) FROM fact_settlement_batch b
                   JOIN v_merchant_current m ON m.merchant_id = b.merchant_id)
            ) / (SELECT count(*) FROM fact_settlement_batch b
                   JOIN v_merchant_current m ON m.merchant_id = b.merchant_id)
        """,
        declared_pct=53.0,
        tolerance_pct=5.0,
    ),
    Trap(
        key="fx_por_igualdad_de_fecha",
        glossary=(
            "Unir con ref_fx_rate_daily por igualdad de fecha: "
            "pierde el 24 % de los pagos no euro"
        ),
        sql="""
            WITH no_euro AS (
                SELECT event_date, currency_code
                FROM fact_payment_attempt
                WHERE currency_code <> 'EUR'
            )
            SELECT 100.0 * (
                (SELECT count(*) FROM no_euro)
                - (SELECT count(*) FROM no_euro n
                     JOIN ref_fx_rate_daily r
                       ON r.currency_code = n.currency_code AND r.rate_date = n.event_date)
            ) / (SELECT count(*) FROM no_euro)
        """,
        declared_pct=24.0,
        tolerance_pct=6.0,
    ),
    Trap(
        key="disputa_sin_etapa_final",
        glossary="Sumar disputed_eur_minor sin filtrar is_final_stage: +60 %",
        sql="""
            SELECT 100.0 * (
                (SELECT sum(disputed_eur_minor) FROM fact_dispute)
                - (SELECT sum(disputed_eur_minor) FROM fact_dispute WHERE is_final_stage)
            ) / (SELECT sum(disputed_eur_minor) FROM fact_dispute WHERE is_final_stage)
        """,
        declared_pct=60.0,
        tolerance_pct=12.0,
    ),
    Trap(
        key="clientes_que_nunca_compraron",
        glossary="INNER JOIN contra dim_customer: pierde el 4,3 % de clientes sin ningún pago",
        sql="""
            SELECT 100.0 * (
                SELECT count(*) FROM dim_customer c
                WHERE c.customer_sk >= 0
                  AND NOT EXISTS (SELECT 1 FROM v_payment_intent p
                                  WHERE p.customer_sk = c.customer_sk)
            ) / (SELECT count(*) FROM dim_customer WHERE customer_sk >= 0)
        """,
        declared_pct=4.3,
        tolerance_pct=1.0,
    ),
    Trap(
        key="trafico_de_pruebas",
        glossary="No filtrar is_test: mete un 1,2 % de tráfico de pruebas",
        sql="""
            SELECT 100.0 * count(*) FILTER (WHERE is_test) / count(*)
            FROM fact_payment_attempt
        """,
        declared_pct=1.2,
        tolerance_pct=0.4,
    ),
    Trap(
        key="columna_obsoleta_amount_cents",
        glossary="Usar amount_cents: columna OBSOLETA que discrepa de amount_minor en el 0,4 %",
        sql="""
            SELECT 100.0 * count(*) FILTER (WHERE amount_cents IS DISTINCT FROM amount_minor)
                 / count(*)
            FROM fact_payment_attempt
        """,
        declared_pct=0.4,
        tolerance_pct=0.2,
    ),
    Trap(
        key="duplicados_de_ingesta",
        glossary="Contar fact_payment_attempt sin deduplicar: el 0,35 % son duplicados",
        sql="""
            SELECT 100.0 * (
                (SELECT count(*) FROM fact_payment_attempt)
                - (SELECT count(*) FROM v_attempt_dedup)
            ) / (SELECT count(*) FROM v_attempt_dedup)
        """,
        declared_pct=0.35,
        tolerance_pct=0.15,
    ),
    Trap(
        key="pagos_de_invitado",
        glossary="Pagos de invitado (customer_sk = -1): el 6,1 % de los pagos",
        sql="""
            SELECT 100.0 * count(*) FILTER (WHERE customer_sk = -1) / count(*)
            FROM v_payment_intent
        """,
        declared_pct=6.1,
        tolerance_pct=1.5,
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="full")
    args = parser.parse_args()

    import duckdb

    database = ROOT / "datagen" / "out" / f"cierzo-{args.profile}.duckdb"
    if not database.exists():
        print(
            f"measure_traps: FALLO · no existe {database.relative_to(ROOT)}.\n"
            "  Las trampas se miden sobre el dataset, no se razonan. "
            f"Genéralo con `make dataset PROFILE={args.profile}`."
        )
        return 1

    con = duckdb.connect(str(database), read_only=True)
    rows: list[dict[str, object]] = []
    problems: list[str] = []
    try:
        for trap in TRAPS:
            try:
                measured = con.execute(trap.sql).fetchone()[0]
            except Exception as exc:
                problems.append(f"{trap.key}: la consulta falló · {exc}")
                rows.append({"key": trap.key, "measured": None, "error": str(exc)})
                continue
            value = None if measured is None else round(float(measured), 3)
            ok = (
                value is not None
                and trap.declared_pct is not None
                and abs(value - trap.declared_pct) <= trap.tolerance_pct
            )
            if not ok:
                problems.append(
                    f"{trap.key}: el glosario declara {trap.declared_pct} % "
                    f"(+/- {trap.tolerance_pct}) y se mide {value} % sobre el perfil "
                    f"{args.profile}"
                )
            rows.append(
                {
                    "key": trap.key,
                    "glossary": trap.glossary,
                    "declared_pct": trap.declared_pct,
                    "tolerance_pct": trap.tolerance_pct,
                    "measured_pct": value,
                    "passed": ok,
                }
            )
    finally:
        con.close()

    report = ROOT / "evals" / "reports" / "dataset-traps.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps(
            {"profile": args.profile, "traps": rows, "problems": problems},
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    for row in rows:
        mark = "ok   " if row.get("passed") else "FALLO"
        print(
            f"  {mark} {row['key']:32s} declarado {row.get('declared_pct')!s:>6s} % "
            f"· medido {row.get('measured_pct')!s:>8s} %"
        )

    if problems:
        print(f"\nmeasure_traps: FALLO · {len(problems)} trampas discrepan\n")
        for p in problems:
            print(f"  · {p}")
        print(
            "\n  Una trampa cuyo número no se ha vuelto a medir desde que se escribió "
            "es folclore.\n  Corregir el glosario es cambiar un contrato FIRMADO: se "
            "propone en docs/PARA-SAMUEL.md y se espera."
        )
        return 1
    print(f"\nmeasure_traps: ok · las {len(TRAPS)} trampas se reproducen sobre {args.profile}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
