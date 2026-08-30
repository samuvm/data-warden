#!/usr/bin/env python
"""Emit the measured figures of a built profile as Markdown.

This file exists because of a specific failure. The README carried a table headed
"lo que sale del perfil completo, medido" whose merchant-concentration row was the
SOLVER'S TARGET (45 % / 80 %) while the traffic actually generated was 30.9 % /
71.6 %. It was the exact sin the generator declares itself built to prevent, and a
reviewer found it in the documentation rather than in the data.

So the numbers are no longer typed. `run.sh` regenerates this block from the
manifest and the database on every build, and the README includes what comes out.
A figure that cannot be produced by this script does not belong in the README.
"""

from __future__ import annotations

import argparse
import json
import pathlib

import duckdb

SCALARS = """
SELECT
    (SELECT count(*) FROM fact_payment_attempt)                              AS attempts,
    (SELECT count(*) FROM v_attempt_dedup)                                   AS attempts_dedup,
    (SELECT count(DISTINCT payment_intent_id) FROM v_attempt_dedup)          AS intents,
    (SELECT count(*) FROM fact_order_line)                                   AS lines,
    (SELECT round(100.0*avg((auth_status='approved')::INT), 2)
       FROM v_attempt_dedup WHERE NOT is_test)                               AS approval_pct,
    (SELECT round(median(amount_eur_minor)/100.0, 2)
       FROM v_attempt_dedup WHERE NOT is_test)                               AS median_eur,
    (SELECT round(avg(amount_eur_minor)/100.0, 2)
       FROM v_attempt_dedup WHERE NOT is_test)                               AS mean_eur,
    (SELECT round(100.0*avg(is_cross_border::INT), 1) FROM v_attempt_dedup)  AS cross_border_pct,
    (SELECT round(sum(gross_eur_minor)/100000000.0, 1)
       FROM fact_settlement_batch)                                           AS settled_meur,
    (SELECT round(sum(fee_eur_minor)/100000.0, 1) FROM fact_settlement_batch) AS fees_keur,
    (SELECT round(100.0*count(*) FILTER (WHERE batch_status <> 'settled')/count(*), 2)
       FROM fact_settlement_batch)                                           AS unsettled_pct,
    (SELECT count(*) FROM fact_payout)                                       AS payouts,
    (SELECT round(100.0*count(*) FILTER (WHERE support_note IS NOT NULL)/count(*), 2)
       FROM dim_customer)                                                    AS note_pct,
    (SELECT count(*) FROM dim_customer WHERE erasure_requested_on IS NOT NULL) AS erasures
"""

TRAPS = """
SELECT
    -- The naive total sums EVERY attempt row, declines and retries included, which
    -- is what `SELECT sum(amount) FROM fact_payment_attempt` does. The correct one
    -- sums what was actually captured, once per payment. Comparing approved rows
    -- against captured intents -- the first version of this query -- compared two
    -- numbers that are almost the same by construction and reported a 0.4 % trap.
    (SELECT round(100.0*(sum(amount_eur_minor)
        / (SELECT sum(captured_eur_minor) FROM v_payment_intent WHERE eventually_approved)
        - 1), 1) FROM fact_payment_attempt)                                 AS naive_row_count_pct,
    (SELECT round(100.0*((SELECT count(*) FROM fact_payment_attempt f
                          JOIN dim_merchant m ON m.merchant_sk = f.merchant_sk
                          JOIN dim_merchant m2 ON m2.merchant_id = m.merchant_id)
                         / (SELECT count(*)::DOUBLE FROM fact_payment_attempt) - 1), 1))
                                                                            AS scd2_natural_key_pct,
    (SELECT round(100.0*(count(*) - count(fx.eur_rate))/count(*), 1)
       FROM fact_payment_attempt f LEFT JOIN ref_fx_rate_daily fx
         ON fx.currency_code = f.currency_code AND fx.rate_date = f.event_date
       WHERE f.currency_code <> 'EUR')                                      AS fx_equality_join_lost_pct,
    (SELECT round(100.0*(count(*) - count(DISTINCT (payment_intent_id, attempt_seq)))
        / count(*), 3) FROM fact_payment_attempt)                           AS ingestion_dup_pct,
    (SELECT round(100.0*(sum(disputed_eur_minor)
        / sum(disputed_eur_minor) FILTER (WHERE is_final_stage) - 1), 1)
       FROM fact_dispute)                                                   AS dispute_stage_pct
"""

INTEGRITY = """
SELECT
    (SELECT count(*) FROM fact_payout p JOIN dim_date d ON d.full_date = p.value_date
      WHERE NOT d.is_business_day)                                          AS payouts_off_calendar,
    (SELECT count(*) FROM v_attempt_dedup f JOIN dim_card c USING (card_sk)
      WHERE f.auth_status='approved' AND NOT f.is_cross_border
        AND f.interchange_minor > f.amount_eur_minor
            * (CASE c.funding_type WHEN 'credit' THEN 30.0 ELSE 20.0 END)/10000.0 + 1)
                                                                            AS interchange_over_cap,
    (SELECT count(*) FROM dim_customer WHERE support_note LIKE '%on +%'
        AND support_note NOT LIKE '%' || phone_e164 || '%')                 AS notes_with_wrong_phone
"""


def build(data_dir: pathlib.Path, db_path: pathlib.Path) -> str:
    m = json.loads((data_dir / "MANIFEST.json").read_text())
    con = duckdb.connect(str(db_path), read_only=True)
    s = con.execute(SCALARS).fetchone()
    t = con.execute(TRAPS).fetchone()
    g = con.execute(INTEGRITY).fetchone()

    risk = con.execute("""
        SELECT CASE WHEN f.risk_score < 200 THEN '000-199'
                    WHEN f.risk_score < 400 THEN '200-399' ELSE '400+' END AS band,
               round(100.0*count(DISTINCT d.payment_intent_id)
                     / count(DISTINCT f.payment_intent_id), 3) AS pct
        FROM v_attempt_dedup f
        LEFT JOIN fact_dispute d ON d.payment_intent_id = f.payment_intent_id
                                AND d.stage_no = 1
        WHERE f.auth_status = 'approved' GROUP BY 1 ORDER BY 1""").fetchall()
    con.close()

    mc = m["shape"]["merchant_concentration"]
    cp = m["shape"]["customer_payments"]
    # Counted from the CATALOGUE, not from the manifest: the manifest only knows the
    # tables `generate.py` wrote, and the four money tables are derived afterwards.
    con2 = duckdb.connect(str(db_path), read_only=True)
    tables = [r[0] for r in con2.execute(
        "SELECT view_name FROM duckdb_views() WHERE NOT internal "
        "AND view_name NOT LIKE 'v!_%' ESCAPE '!'").fetchall()]
    rows = sum(con2.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables)
    n_tables = len(tables)
    con2.close()

    out = [
        f"<!-- generado por datagen/report.py desde el perfil `{m['profile']}`; no editar a mano -->",
        "",
        "| | |", "|---|---:|",
        f"| Filas totales · {n_tables} tablas | **{rows:,}** |",
        f"| Intentos de autorización · líneas de cesta | {s[0]:,} · {s[3]:,} |",
        f"| Parquet en disco | **{m['totals']['bytes']/1e9:.2f} GB** |",
        f"| Aprobación · ticket mediano · ticket medio | {s[4]} % · {s[5]} € · {s[6]} € |",
        f"| Transfronterizo | {s[7]} % |",
        f"| Liquidado · comisión · lotes sin cerrar | {s[8]} M€ · {s[9]} k€ · {s[11]:,} pagos, {s[10]} % |",
        f"| Top 1 % / top 10 % de comercios, **medido sobre el tráfico** "
        f"| **{mc['top_1pct_measured_on_traffic']:.2%} / {mc['top_10pct_measured_on_traffic']:.2%}** |",
        f"| (el mismo objetivo sobre el vector de pesos, que **no** es lo publicado) "
        f"| {mc['top_1pct_share']:.0%} / {mc['top_10pct_share']:.0%} |",
        f"| Clientes que nunca pagan (objetivo {cp['never_paid_target']:.1%}) "
        f"| {cp['never_paid_share']:.3%} |",
        f"| Pagos por cliente pagador: media · mediana · p99 · máximo "
        f"| {cp['mean_per_paying_customer']:.2f} · {cp['median_per_paying_customer']:.0f} "
        f"· {cp['p99']} · {cp['max']} |",
        f"| Anillos de fraude · miembros | {m['shape']['fraud_rings']['rings']:,} "
        f"· {m['shape']['fraud_rings']['members']:,} |",
        f"| Factor de reintento | {m['shape']['retry_expansion_factor']:.4f} |",
        f"| Clientes con nota de soporte · con supresión pedida | {s[12]} % · {s[13]:,} |",
        "",
        "**Las trampas, medidas sobre estos datos:**",
        "",
        "| Trampa | Cuánto engaña |", "|---|---:|",
        f"| Contar ingresos contando filas | **+{t[0]} %** |",
        f"| Unir por la clave natural del comercio en vez de la subrogada | **+{t[1]} %** |",
        f"| `JOIN` de divisa por igualdad de fecha | **−{t[2]} %** de los pagos no-euro |",
        f"| Duplicados de ingesta | {t[3]} % de las filas |",
        f"| Sumar el contracargo en todas sus etapas | **+{t[4]} %** |",
        "",
        "**Y lo que tiene que dar cero:**",
        "",
        "| Invariante | Filas que lo violan |", "|---|---:|",
        f"| Dinero movido en día no hábil | {g[0]} |",
        f"| Interchange sobre el tope del Reglamento (UE) 2015/751 | {g[1]} |",
        f"| Notas de soporte con un teléfono que no es el del cliente | {g[2]} |",
        "",
        "**Contracargo por tramo de riesgo** (el score tiene que predecirlo):",
        "",
        "| Tramo | % de contracargo |", "|---|---:|",
    ]
    out += [f"| {b} | {p} % |" for b, p in risk]
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=pathlib.Path, default="datagen/out/dev")
    ap.add_argument("--db", type=pathlib.Path, default="datagen/out/cierzo-dev.duckdb")
    ap.add_argument("--out", type=pathlib.Path, default=None)
    a = ap.parse_args()
    md = build(a.data, a.db)
    if a.out:
        a.out.write_text(md)
        print(f"wrote {a.out}")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
