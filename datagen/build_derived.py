#!/usr/bin/env python
"""Derive the money-movement facts from the authorization facts.

These four tables are built in SQL, over the Parquet that already exists, rather
than in the Python day loop. The reason is reconciliation: a settlement batch that
does not sum to the payments inside it is not a trap, it is a bug, and deriving it
by aggregation makes that impossible by construction. Finance is the role whose
whole job is making two numbers agree, so the two numbers have to actually agree.

Randomness comes from `hash(key)`, not from a random number generator: the same
input Parquet always produces the same disputes and the same refunds, with no seed
to thread through and no ordering dependence.

Every table reads `v_attempt_dedup`, never the raw fact table. The at-least-once
duplicates are a deliberate trap in the ingestion layer, and a real pipeline
collapses them before a settlement batch is cut.

Three rules this file learned the hard way, from an audit of the version before it:

* MONEY MOVES ON BUSINESS DAYS. 30.9 % of payouts had a value date on a Saturday,
  a Sunday or Christmas Day -- including 653 dated 2024-12-25 -- while `dim_date`
  sat right there with `is_business_day` on every row and `refs.py` documented the
  roll-forward rule in prose that existed nowhere in the code.
* A COLUMN NAMED `*_eur_minor` HOLDS EUROS. Two thirds of payouts declared a
  currency of HUF or SEK over an amount that had never been converted.
* ONE DRAW PER DECISION. A `CASE` whose branches each hash a different salt is not
  a cumulative distribution: the branches are independent coin flips and every
  declared mix comes out wrong. All of them did.
"""

from __future__ import annotations

import argparse
import pathlib

import duckdb


def _u(expr: str, salt: int) -> str:
    """Deterministic uniform in [0,1) from a key and a salt."""
    return f"((hash({expr} + {salt}) % 1000000) / 1000000.0)"


# Roll any date forward to the next day the banks are open. `dim_date` already
# knows weekends and Spanish national holidays, so this is a lookup and not a
# calculation -- which is the point of having a calendar dimension at all.
NEXT_BUSINESS_DAY = """
CREATE OR REPLACE TABLE _next_business_day AS
SELECT d.full_date                                          AS raw_date,
       min(b.full_date)                                     AS business_date
FROM dim_date d
JOIN dim_date b ON b.full_date >= d.full_date AND b.is_business_day
GROUP BY 1
"""

SETTLEMENT = f"""
CREATE OR REPLACE TABLE fact_settlement_batch AS
WITH cut AS (
    SELECT
        f.settlement_batch_id,
        f.settlement_date                                   AS scheduled_date,
        m.merchant_id,
        any_value(m.group_sk)                               AS group_sk,
        any_value(m.settlement_currency)                    AS merchant_currency,
        count(*)                                            AS payment_count,
        sum(f.amount_eur_minor)                             AS gross_eur_minor,
        sum(f.fee_minor)                                    AS fee_eur_minor,
        sum(f.interchange_minor)                            AS interchange_eur_minor,
        sum(f.scheme_fee_minor)                             AS scheme_fee_eur_minor,
        any_value(mc.reserve_pct)                           AS reserve_pct
    FROM v_attempt_dedup f
    JOIN dim_merchant m ON m.merchant_sk = f.merchant_sk
    JOIN ref_mcc mc     ON mc.mcc = m.mcc
    WHERE f.settlement_batch_id IS NOT NULL
      AND f.auth_status = 'approved'
      -- Test traffic never reaches the production ledger. Letting it through put
      -- 11.0 M EUR of money that does not exist into the batches, the payouts and
      -- every revenue figure derived from them.
      AND NOT f.is_test
    GROUP BY 1, 2, 3
)
SELECT
    c.settlement_batch_id,
    b.business_date                                         AS settlement_date,
    c.scheduled_date,
    c.merchant_id, c.group_sk,
    c.merchant_currency                                     AS merchant_preferred_currency,
    c.payment_count,
    c.gross_eur_minor, c.fee_eur_minor,
    c.interchange_eur_minor, c.scheme_fee_eur_minor,
    c.gross_eur_minor - c.fee_eur_minor                     AS net_eur_minor,
    -- A high-risk merchant has a rolling reserve withheld from every batch, released
    -- 180 days later. `reserve_released_on` is NULL when that date falls outside the
    -- warehouse: "not yet released" is a different fact from "released on a date we
    -- do not have data for".
    CAST(round(c.gross_eur_minor * c.reserve_pct / 100.0) AS BIGINT)
                                                            AS reserve_eur_minor,
    CASE WHEN c.reserve_pct > 0
              AND b.business_date + INTERVAL 180 DAY <= DATE '{{end_date}}'
         THEN b.business_date + INTERVAL 180 DAY END        AS reserve_released_on,
    -- One batch in six hundred fails to close on time. Those are exactly the rows a
    -- controller spends their week on, so they have to exist. One draw, three
    -- outcomes, cumulative thresholds -- not three independent coin flips.
    CASE WHEN {_u('c.settlement_batch_id', 17)} < 0.0017 THEN 'exception'
         WHEN {_u('c.settlement_batch_id', 17)} < 0.0127 THEN 'pending'
         ELSE 'settled' END                                 AS batch_status
FROM cut c
JOIN _next_business_day b ON b.raw_date = c.scheduled_date
"""

PAYOUT = """
CREATE OR REPLACE TABLE fact_payout AS
WITH grouped AS (
    SELECT
        b.settlement_date, b.group_sk,
        count(DISTINCT b.merchant_id)                       AS merchant_count,
        count(*)                                            AS batch_count,
        sum(b.net_eur_minor) - sum(b.reserve_eur_minor)     AS paid_eur_minor,
        sum(b.reserve_eur_minor)                            AS withheld_eur_minor
    FROM fact_settlement_batch b
    WHERE b.batch_status = 'settled'
    GROUP BY 1, 2
)
SELECT
    -- Deterministic: the window is ordered by the FULL grouping key. Ordering by a
    -- prefix of it left 96.2 % of rows inside a tie, so the surrogate key and the
    -- human-facing reference changed between two runs of the same input.
    row_number() OVER (ORDER BY g.settlement_date, g.group_sk)      AS payout_sk,
    'PO-' || lpad(CAST(row_number() OVER (ORDER BY g.settlement_date, g.group_sk)
                       AS VARCHAR), 10, '0')                        AS payout_reference,
    n.business_date                                                 AS value_date,
    g.settlement_date,
    g.group_sk                                                      AS beneficiary_group_sk,
    -- The ledger is denominated in EUR and says so. The merchant's preferred
    -- currency is carried alongside as information, not as a label on an amount
    -- that was never converted.
    'EUR'                                                           AS currency_code,
    g.merchant_count, g.batch_count,
    g.paid_eur_minor, g.withheld_eur_minor,
    -- The destination account belongs to the GROUP, in the GROUP's own country --
    -- which is the whole point of the ownership graph: the shop that took the money
    -- and the account that receives it are different legal entities, often in
    -- different jurisdictions. Every payout landing in a Spanish IBAN regardless of
    -- whether the parent was British, Swiss or American contradicted the one story
    -- this table exists to tell.
    cg.payout_iban                                                  AS beneficiary_iban,
    cg.incorporation_country                                        AS beneficiary_country
FROM grouped g
JOIN dim_corporate_group cg ON cg.group_sk = g.group_sk
JOIN _next_business_day n   ON n.raw_date = g.settlement_date + INTERVAL 1 DAY
"""

REFUND = f"""
CREATE OR REPLACE TABLE fact_refund AS
WITH approved AS (
    SELECT f.payment_intent_id, f.event_date, f.merchant_sk, f.customer_sk,
           f.amount_minor, f.amount_eur_minor, f.currency_code, f.fx_rate, r.category
    FROM v_attempt_dedup f
    JOIN dim_merchant m ON m.merchant_sk = f.merchant_sk
    JOIN ref_mcc r      ON r.mcc = m.mcc
    WHERE f.auth_status = 'approved' AND NOT f.is_test
), picked AS (
    SELECT a.*,
           -- Most refunds happen in the first fortnight and the tail runs long. A
           -- flat draw over 45 days gave 23,000 refunds in every five-day bucket
           -- and a hard wall at day 45, which is not how returns behave.
           CAST(1 + floor(pow({_u('a.payment_intent_id', 41)}, 2.1) * 88) AS INTEGER)
                                                              AS delay_days,
           {_u('a.payment_intent_id', 53)}                     AS u_partial,
           {_u('a.payment_intent_id', 61)}                     AS u_frac,
           {_u('a.payment_intent_id', 67)}                     AS u_reason
    FROM approved a
    WHERE {_u('a.payment_intent_id', 13)} <
          CASE a.category WHEN 'FASHION' THEN 0.128 WHEN 'ELECTRONICS' THEN 0.061
                          WHEN 'HOME' THEN 0.054   WHEN 'TRAVEL' THEN 0.038
                          WHEN 'BEAUTY' THEN 0.046 WHEN 'FOOD' THEN 0.011
                          WHEN 'GROCERY' THEN 0.008 ELSE 0.024 END
)
SELECT
    row_number() OVER (ORDER BY p.event_date, p.payment_intent_id) AS refund_sk,
    p.payment_intent_id, p.merchant_sk, p.customer_sk, p.currency_code,
    p.event_date + p.delay_days                                    AS refund_date,
    -- Partial refunds are the common case in fashion (one item of three goes back)
    -- and rare in travel. A refund that always equals the original amount would make
    -- `amount_minor - refunded_minor` a useless column.
    CASE WHEN p.u_partial < 0.42
         THEN CAST(round(p.amount_minor * (0.2 + 0.6 * p.u_frac)) AS BIGINT)
         ELSE p.amount_minor END                                   AS refund_amount_minor,
    -- AND THE SAME FIGURE IN EUROS. Without it this is a money table denominated in
    -- fourteen currencies that cannot be summed: the naive total came to 55.7 M EUR
    -- of which only 22.1 M were actually euros, the rest being ore, haleru and
    -- forints added together.
    CAST(round(CASE WHEN p.u_partial < 0.42
                    THEN p.amount_eur_minor * (0.2 + 0.6 * p.u_frac)
                    ELSE p.amount_eur_minor END) AS BIGINT)        AS refund_amount_eur_minor,
    CASE WHEN p.u_reason < 0.51 THEN 'customer_returned_goods'
         WHEN p.u_reason < 0.62 THEN 'item_out_of_stock'
         WHEN p.u_reason < 0.78 THEN 'duplicate_charge'
         WHEN p.u_reason < 0.91 THEN 'goodwill'
         ELSE 'order_cancelled' END                                AS refund_reason,
    p.category                                                     AS product_category
FROM picked p
-- Right-censored at the edge of the warehouse. 6,657 refunds were dated after the
-- last payment in the dataset, up to 2026-10-15, which makes any "refund rate this
-- month" query wrong at exactly the month people look at first.
WHERE p.event_date + p.delay_days <= DATE '{{end_date}}'
"""

DISPUTE = f"""
CREATE OR REPLACE TABLE fact_dispute AS
WITH base AS (
    SELECT f.payment_intent_id, f.event_date, f.merchant_sk, f.customer_sk,
           f.amount_eur_minor, f.card_sk, r.dispute_rate_pct, f.risk_score
    FROM v_attempt_dedup f
    JOIN dim_merchant m ON m.merchant_sk = f.merchant_sk
    JOIN ref_mcc r      ON r.mcc = m.mcc
    WHERE f.auth_status = 'approved' AND NOT f.is_test
      -- The chargeback rate is the category's BASE rate scaled by what the risk
      -- engine thought at the time. Drawing it from the MCC alone left `risk_score`
      -- with no predictive power over disputes at all, and inside MCC 7995 the
      -- relationship even inverted. A risk score that does not predict the outcome it
      -- exists to predict is the first thing a fraud analyst checks.
      AND {_u('f.payment_intent_id', 97)} <
          (r.dispute_rate_pct / 100.0) * (0.35 + 2.6 * pow(f.risk_score / 999.0, 1.7))
), staged AS (
    SELECT b.*,
           -- How far it escalates. Most stop at the first chargeback; arbitration is
           -- rare and expensive, which is why the stage is worth having as a column
           -- instead of a boolean. One draw, four outcomes, cumulative thresholds.
           CASE WHEN {_u('b.payment_intent_id', 101)} < 0.58 THEN 1
                WHEN {_u('b.payment_intent_id', 101)} < 0.86 THEN 2
                WHEN {_u('b.payment_intent_id', 101)} < 0.96 THEN 3
                ELSE 4 END                                      AS max_stage,
           {_u('b.payment_intent_id', 113)}                     AS u_outcome,
           {_u('b.payment_intent_id', 131)}                     AS u_reason,
           CAST(3 + floor({_u('b.payment_intent_id', 109)} * 90) AS INTEGER) AS first_delay
    FROM base b
)
SELECT
    row_number() OVER (ORDER BY s.event_date, s.payment_intent_id, st.stage) AS dispute_sk,
    -- A stable id for the DISPUTE, distinct from the id of the stage row. Without it
    -- the only way to count disputes is `count(DISTINCT payment_intent_id)`, and the
    -- obvious `count(*)` overstates them by 47.8 %.
    dense_rank() OVER (ORDER BY s.payment_intent_id)                    AS dispute_case_id,
    s.payment_intent_id, s.merchant_sk, s.customer_sk, s.card_sk,
    st.stage                                                            AS stage_no,
    s.max_stage                                                         AS final_stage_no,
    st.stage = s.max_stage                                              AS is_final_stage,
    CASE st.stage WHEN 1 THEN 'retrieval_request' WHEN 2 THEN 'first_chargeback'
                  WHEN 3 THEN 'pre_arbitration'   ELSE 'arbitration' END AS stage_name,
    -- Escalation is not a metronome. Every consecutive pair used to sit EXACTLY 21
    -- days apart -- min = max = 21, a single distinct value across 14,613 pairs --
    -- which is the kind of detail that identifies a dataset as generated on sight.
    s.event_date + s.first_delay
        + CAST((st.stage - 1) * (11 + floor({_u('s.payment_intent_id', 149)} * 34))
               AS INTEGER)                                              AS opened_on,
    -- The disputed amount belongs to the CASE, not to each of its stages: it is
    -- repeated here for convenience and `is_final_stage` is how you avoid summing it
    -- four times. Summing it blind inflates chargebacks by 47.8 %.
    s.amount_eur_minor                                                  AS disputed_eur_minor,
    -- The outcome is only known once it is over. An open case has no verdict, and
    -- stamping the final one on every stage made the column useless for anything
    -- historical.
    CASE WHEN st.stage < s.max_stage THEN 'escalated'
         WHEN s.u_outcome < 0.34 THEN 'merchant_won'
         WHEN s.u_outcome < 0.79 THEN 'issuer_won'
         ELSE 'open' END                                                AS outcome,
    CASE WHEN s.u_reason < 0.31 THEN 'fraudulent'
         WHEN s.u_reason < 0.58 THEN 'product_not_received'
         WHEN s.u_reason < 0.74 THEN 'product_unacceptable'
         WHEN s.u_reason < 0.88 THEN 'subscription_cancelled'
         ELSE 'unrecognised' END                                        AS reason_category
FROM staged s
CROSS JOIN (SELECT unnest([1, 2, 3, 4]) AS stage) st
WHERE st.stage <= s.max_stage
  -- Right-censored with the ACTUAL gap, not a lower bound on it. Using the minimum
  -- 11-day step let 73 late stages past the edge of the warehouse.
  AND s.event_date + s.first_delay
      + CAST((st.stage - 1) * (11 + floor({_u('s.payment_intent_id', 149)} * 34))
             AS INTEGER) <= DATE '{{end_date}}'
"""


def build(data_dir: pathlib.Path, db_path: pathlib.Path, end_date: str) -> None:
    con = duckdb.connect(str(db_path))
    con.execute(NEXT_BUSINESS_DAY)
    for label, sql in [("fact_settlement_batch", SETTLEMENT), ("fact_payout", PAYOUT),
                       ("fact_refund", REFUND), ("fact_dispute", DISPUTE)]:
        con.execute(sql.replace("{end_date}", end_date))
        n = con.execute(f"SELECT count(*) FROM {label}").fetchone()[0]
        out = data_dir / label
        out.mkdir(parents=True, exist_ok=True)
        con.execute(f"COPY {label} TO '{out}/part-0000.parquet' "
                    "(FORMAT parquet, COMPRESSION zstd)")
        print(f"  {label:26s} {n:12,d} rows")
    con.execute("DROP TABLE _next_business_day")
    con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=pathlib.Path, default="datagen/out/dev")
    ap.add_argument("--db", type=pathlib.Path, default="datagen/out/cierzo-dev.duckdb")
    ap.add_argument("--end-date", default="2026-08-31")
    a = ap.parse_args()
    build(a.data, a.db, a.end_date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
