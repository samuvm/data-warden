-- ===========================================================================
-- CIERZO · exploration deck
--
-- Run one block at a time. Each is a question somebody actually asks of a
-- payments warehouse, and each is here because the data has a real answer for
-- it -- not because the SQL is pretty.
--
--   docker compose -f datagen/docker/compose.yaml run --rm sql
--   cierzo> .read sql/01-explora.sql
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 1. THE GRAIN TRAP. Three ways to add up "revenue", two of them wrong.
--    The first counts every retry as a sale. The third multiplies each basket
--    by the number of times the card was tried. Only the middle one is money.
-- ---------------------------------------------------------------------------
SELECT
  (SELECT count(*)          FROM fact_payment_attempt WHERE auth_status='approved')       AS approved_rows,
  (SELECT count(DISTINCT payment_intent_id) FROM fact_payment_attempt)                    AS real_payments,
  (SELECT round(sum(amount_eur_minor)/100.0)  FROM fact_payment_attempt)                  AS naive_all_rows_eur,
  (SELECT round(sum(captured_eur_minor)/100.0) FROM v_payment_intent WHERE eventually_approved) AS correct_eur,
  (SELECT round(sum(l.line_amount_minor)/100.0)
     FROM fact_order_line l JOIN fact_payment_attempt f USING (payment_intent_id))        AS lines_times_attempts_eur;

-- ---------------------------------------------------------------------------
-- 2. WHO BUYS WHAT, measured as LIFT rather than as a top-1 label.
--
--    The obvious version -- mode(category) per segment -- returns GROCERY for all
--    twenty-four segments and looks like the data has no signal. It does: GROCERY
--    is simply the biggest category everywhere, so the mode is a fact about the
--    catalogue and not about the customer. Lift divides each segment's mix by the
--    overall mix, which is the question anyone actually meant to ask.
-- ---------------------------------------------------------------------------
WITH purchases AS (
    SELECT c.age_band, p.subcategory
    FROM fact_order_line l
    JOIN dim_product p      ON p.product_sk = l.product_sk
    JOIN v_payment_intent i ON i.payment_intent_id = l.payment_intent_id
    JOIN dim_customer c     ON c.customer_sk = i.customer_sk
), overall AS (
    SELECT subcategory, count(*) * 1.0 / sum(count(*)) OVER () AS base_share
    FROM purchases GROUP BY 1
), by_band AS (
    SELECT age_band, subcategory, count(*) AS n,
           count(*) * 1.0 / sum(count(*)) OVER (PARTITION BY age_band) AS band_share
    FROM purchases GROUP BY 1, 2
)
SELECT b.age_band, b.subcategory, b.n AS purchases,
       round(b.band_share / o.base_share, 2) AS lift
FROM by_band b JOIN overall o USING (subcategory)
WHERE b.n > 300
QUALIFY row_number() OVER (PARTITION BY b.age_band ORDER BY b.band_share / o.base_share DESC) <= 3
ORDER BY b.age_band, lift DESC;

-- ---------------------------------------------------------------------------
-- 3. FOLLOW THE MONEY. A merchant trades in one country and its owner banks in
--    another. Answering this needs a recursive walk up the ownership graph --
--    and an unbounded WITH RECURSIVE is a denial of service written in SQL,
--    which is precisely why the guard has to bound it.
-- ---------------------------------------------------------------------------
SELECT ug.group_name                       AS ultimate_parent,
       ug.incorporation_country            AS banks_in,
       count(DISTINCT mf.merchant_id)      AS merchants,
       count(DISTINCT mf.merchant_country) AS trades_in_countries,
       max(mf.hops_to_ultimate)            AS ownership_hops,
       round(sum(mf.amount_eur_minor)/100.0)  AS collected_eur,
       round(sum(mf.fee_minor)/100.0)         AS cierzo_revenue_eur
FROM v_money_flow mf
JOIN dim_corporate_group ug ON ug.group_sk = mf.ultimate_group_sk
GROUP BY 1, 2 ORDER BY collected_eur DESC LIMIT 15;

-- ---------------------------------------------------------------------------
-- 4. ONE DEVICE, SEVERAL PEOPLE: a household, or a fraud ring?
--
--    COUNTING THE PEOPLE DOES NOT ANSWER IT, and that is deliberate. Ring devices
--    are on average SMALLER than household ones, so a threshold on the head count
--    is not just useless -- it points the wrong way. An earlier build capped
--    households at five owners while rings ran to eleven, and `people >= 6` became
--    a perfect detector; the exercise was over before it started.
--
--    What separates them is the SECOND thing they share. Run this, then run 4b.
-- ---------------------------------------------------------------------------
WITH shared AS (
    SELECT b.device_sk, count(DISTINCT b.customer_sk) AS people
    FROM bridge_customer_device b GROUP BY 1 HAVING count(DISTINCT b.customer_sk) >= 4
)
SELECT s.people,
       count(DISTINCT s.device_sk)                       AS devices,
       round(count(DISTINCT g.ip_block_sk) * 1.0
             / count(DISTINCT s.device_sk), 2)           AS networks_per_device,
       round(avg(f.risk_score), 0)                       AS avg_risk,
       round(100.0 * count(*) FILTER (WHERE m.category IN ('GAMBLING','CRYPTO','FINANCIAL'))
             / count(*), 1)                              AS pct_high_risk_category
FROM shared s
JOIN fact_payment_attempt f ON f.device_sk = s.device_sk
JOIN v_payment_geo g        ON g.ingestion_id = f.ingestion_id
JOIN dim_merchant dm        ON dm.merchant_sk = f.merchant_sk
JOIN ref_mcc m              ON m.mcc = dm.mcc
GROUP BY 1 ORDER BY 1;

-- ---------------------------------------------------------------------------
-- 4b. NAME THE RING. Hold the head count constant and split on the NETWORK.
--     Same sizes, same everything -- except that one group also connects from the
--     same couple of /24s. That group buys three times as much gambling and
--     crypto. This is the query the previous one sets up, and the one whose
--     answer is a list of customer ids.
-- ---------------------------------------------------------------------------
WITH cohort AS (
    SELECT b.device_sk,
           count(DISTINCT b.customer_sk)               AS people,
           list(DISTINCT b.customer_sk)[1:6]           AS sample_customers
    FROM bridge_customer_device b
    GROUP BY 1 HAVING count(DISTINCT b.customer_sk) >= 8
)
SELECT c.device_sk, c.people, c.sample_customers,
       count(DISTINCT g.ip_block_sk)                   AS networks,
       count(*)                                        AS payments,
       round(avg(f.amount_eur_minor)/100.0, 2)         AS avg_ticket_eur,
       round(avg(f.risk_score), 0)                     AS avg_risk,
       mode(m.category)                                AS top_category
FROM cohort c
JOIN fact_payment_attempt f ON f.device_sk = c.device_sk
JOIN v_payment_geo g        ON g.ingestion_id = f.ingestion_id
JOIN dim_merchant dm        ON dm.merchant_sk = f.merchant_sk
JOIN ref_mcc m              ON m.mcc = dm.mcc
GROUP BY 1, 2, 3
HAVING count(DISTINCT g.ip_block_sk) <= 3
ORDER BY avg_risk DESC LIMIT 10;

-- ---------------------------------------------------------------------------
-- 5. WHERE THE WORLD PAYS FROM. The geo join is a RANGE join, and it is the
--    most expensive thing in the catalogue -- which makes it the honest test of
--    any estimate of "bytes scanned" made before running.
-- ---------------------------------------------------------------------------
SELECT g.ip_country, g.connection_kind,
       count(*)                                                       AS payments,
       round(avg(f.risk_score), 0)                                    AS avg_risk,
       round(100.0*count(*) FILTER (WHERE f.auth_status='approved')/count(*), 1) AS approval_pct
FROM v_payment_geo g
JOIN fact_payment_attempt f USING (ingestion_id)
GROUP BY 1, 2 HAVING count(*) > 200 ORDER BY payments DESC LIMIT 20;

-- ---------------------------------------------------------------------------
-- 6. THE SLOWLY CHANGING DIMENSION TRAP. The same question asked two ways.
--    Joining on the natural key instead of the surrogate multiplies a merchant
--    by its version count and rewrites its history -- and the total still looks
--    plausible, which is why this one survives review.
-- ---------------------------------------------------------------------------
SELECT
  (SELECT count(*) FROM fact_payment_attempt f
     JOIN dim_merchant m ON m.merchant_sk = f.merchant_sk)                AS correct_join,
  (SELECT count(*) FROM fact_payment_attempt f
     JOIN dim_merchant m ON m.merchant_sk = f.merchant_sk
     JOIN dim_merchant m2 ON m2.merchant_id = m.merchant_id)              AS natural_key_join;

-- ---------------------------------------------------------------------------
-- 7. RETRIES. Which declines are worth trying again, measured rather than
--    assumed. A hard decline that gets retried is a scheme violation; the data
--    reflects that, so this table has a hole in it exactly where it should.
-- ---------------------------------------------------------------------------
SELECT r.decline_reason_code, r.category, r.is_soft_decline,
       count(*)                                                   AS first_declines,
       count(*) FILTER (WHERE nxt.attempt_seq IS NOT NULL)        AS retried,
       round(100.0*count(*) FILTER (WHERE nxt.auth_status='approved')
             / nullif(count(*) FILTER (WHERE nxt.attempt_seq IS NOT NULL), 0), 1)
                                                                  AS retry_success_pct
FROM fact_payment_attempt f
JOIN ref_decline_reason r ON r.decline_reason_code = f.decline_reason_code
LEFT JOIN fact_payment_attempt nxt
       ON nxt.payment_intent_id = f.payment_intent_id
      AND nxt.attempt_seq = f.attempt_seq + 1
WHERE f.attempt_seq = 1 AND f.auth_status = 'declined'
GROUP BY 1, 2, 3 ORDER BY first_declines DESC;

-- ---------------------------------------------------------------------------
-- 8. THE FX HOLE. Weekends and holidays have no quote, so "the rate on the day"
--    does not exist for a fifth of the calendar. An equality join here silently
--    drops those payments; the correct answer is the last published rate.
-- ---------------------------------------------------------------------------
SELECT f.currency_code,
       count(*)                                              AS payments,
       count(fx.eur_rate)                                    AS matched_by_equality,
       count(*) - count(fx.eur_rate)                         AS silently_dropped,
       round(100.0*(count(*)-count(fx.eur_rate))/count(*), 1) AS pct_lost
FROM fact_payment_attempt f
LEFT JOIN ref_fx_rate_daily fx
       ON fx.currency_code = f.currency_code AND fx.rate_date = f.event_date
WHERE f.currency_code <> 'EUR'
GROUP BY 1 ORDER BY payments DESC;

-- ---------------------------------------------------------------------------
-- 9. THE COLUMN NO POLICY PROTECTS. `phone_e164` can be masked by name.
--    `support_note` contains the same phone number inside a sentence, and a
--    column-level rule never sees it. This is the single most important query
--    in the deck for what Data Warden is trying to prove.
-- ---------------------------------------------------------------------------
SELECT customer_id, age_band, country_code, support_note
FROM dim_customer
WHERE support_note IS NOT NULL
  AND (support_note ILIKE '%phone%' OR support_note ILIKE '%NIF%')
LIMIT 10;

-- ---------------------------------------------------------------------------
-- 10. THE GDPR CONTRADICTION, stated as data. A customer has asked to be
--     erased; the audit trail is append-only and immutable by design. Both
--     things are true at once and the project has to say what it does about it.
-- ---------------------------------------------------------------------------
SELECT
    count(*) FILTER (WHERE c.erasure_requested_on IS NOT NULL)        AS erasure_requested,
    count(*) FILTER (WHERE c.retention_expires_on < DATE '2026-08-31') AS retention_expired,
    count(DISTINCT f.payment_intent_id) FILTER (WHERE c.erasure_requested_on IS NOT NULL)
                                                                      AS payments_still_on_file
FROM dim_customer c
LEFT JOIN fact_payment_attempt f ON f.customer_sk = c.customer_sk;

-- ---------------------------------------------------------------------------
-- 11. DOES THE RISK SCORE EARN ITS KEEP? It should predict chargebacks without
--     being a restatement of them. Read the lift down the column: this is the
--     query a fraud analyst runs first, and the one that used to return a flat
--     line -- and inside betting, an inverted one.
-- ---------------------------------------------------------------------------
SELECT CASE WHEN f.risk_score < 200 THEN '000-199'
            WHEN f.risk_score < 400 THEN '200-399'
            WHEN f.risk_score < 600 THEN '400-599'
            ELSE '600+' END                                     AS risk_band,
       count(DISTINCT f.payment_intent_id)                      AS payments,
       round(100.0 * count(DISTINCT d.payment_intent_id)
             / count(DISTINCT f.payment_intent_id), 3)          AS chargeback_pct,
       round(avg(f.amount_eur_minor) / 100.0, 2)                AS avg_ticket_eur
FROM v_attempt_dedup f
LEFT JOIN fact_dispute d
       ON d.payment_intent_id = f.payment_intent_id AND d.stage_no = 1
WHERE f.auth_status = 'approved'
GROUP BY 1 ORDER BY 1;

-- ---------------------------------------------------------------------------
-- 12. THE INGESTION DUPLICATES. The same business tuple twice, under two
--     ingestion ids, minutes apart. Every money query must go through
--     `v_attempt_dedup`; anything auditing the pipeline itself comes here.
-- ---------------------------------------------------------------------------
SELECT payment_intent_id, attempt_seq,
       count(*)                                      AS copies,
       min(ingestion_id)                             AS first_ingestion,
       max(ingestion_id)                             AS second_ingestion,
       max(ingested_at) - min(ingested_at)           AS apart
FROM fact_payment_attempt
GROUP BY 1, 2 HAVING count(*) > 1
ORDER BY apart DESC LIMIT 10;

-- ---------------------------------------------------------------------------
-- 13. THE INTERCHANGE CAP. Regulation (EU) 2015/751 caps consumer interchange
--     at 20 bps on debit and 30 on credit for anything with both legs inside
--     the EEA. Zero rows should breach it; only inter-regional traffic pays
--     more. This is the query that would get an acquirer fined if it returned
--     anything -- and it used to return 55 % of the book.
-- ---------------------------------------------------------------------------
SELECT count(*)                                                        AS eea_payments,
       count(*) FILTER (WHERE f.interchange_minor > f.amount_eur_minor
                        * (CASE c.funding_type WHEN 'credit' THEN 30.0 ELSE 20.0 END)
                        / 10000.0 + 1)                                 AS over_the_cap,
       round(avg(10000.0 * f.interchange_minor / f.amount_eur_minor), 1) AS avg_bps
FROM v_attempt_dedup f
JOIN dim_card c USING (card_sk)
WHERE f.auth_status = 'approved' AND NOT f.is_cross_border AND f.amount_eur_minor > 0;
