#!/usr/bin/env python
"""Assemble the generated Parquet into a queryable DuckDB catalogue.

Tables stay in Parquet and the database holds VIEWS over them. That is the
lakehouse shape the project targets, and it has a property that matters here:
the database file is a few hundred kilobytes, so it can be rebuilt in a second
and thrown away, while the data it points at is the artefact under the hash.

The derived views are where columns that would be wasteful to materialise get
their readable form -- `ip_address` as dotted quad, `full_name` from its parts --
and where the generalised columns the access policy points at are guaranteed to
sit next to the raw ones they generalise.
"""

from __future__ import annotations

import argparse
import pathlib

import duckdb

VIEWS = {
    # One row per (intent, attempt): the at-least-once duplicates collapsed.
    #
    # This view exists because the duplicates are a DECLARED trap, and a trap has to
    # be survivable. Anything counting attempts, computing retry rates or ordering
    # by time should start here; anything studying the ingestion pipeline itself
    # should use the raw table and find them.
    "v_attempt_dedup": """
        SELECT * EXCLUDE (rn) FROM (
            SELECT f.*, row_number() OVER (
                PARTITION BY f.payment_intent_id, f.attempt_seq
                ORDER BY f.ingestion_id) AS rn
            FROM fact_payment_attempt f)
        WHERE rn = 1
    """,
    # A person's displayable name, assembled from the parts actually stored.
    # `last_name_2` is NULL for naming systems that have no second surname, so
    # concat_ws is the only correct join here: `||` would blank the whole name.
    # Columns listed explicitly ON PURPOSE. A `SELECT *` view is a hole straight
    # through any policy that works by column name: a new sensitive column appears
    # in the view the moment it is added to the table, and in the policy only when
    # somebody remembers.
    "v_customer": """
        SELECT c.customer_sk, c.customer_id, c.first_name, c.last_name_1, c.last_name_2,
               c.email, c.email_domain, c.phone_e164, c.national_id, c.birth_date,
               c.age_band, c.street_address, c.postal_code, c.region_code, c.city_id,
               c.country_code, c.income_tier, c.value_tier, c.primary_category,
               c.segment_code, c.signed_up_on, c.marketing_opt_in, c.is_business_account,
               c.support_note, c.kyc_status, c.kyc_verified_on, c.erasure_requested_on,
               c.retention_expires_on,
               concat_ws(' ', c.first_name, c.last_name_1, c.last_name_2) AS full_name,
               date_diff('year', c.birth_date, DATE '2026-08-31')         AS age_years,
               (c.birth_date < DATE '1920-01-01'
                OR c.birth_date > DATE '2008-12-31')                      AS birth_date_is_implausible
        FROM dim_customer c
    """,
    # Dotted-quad rendering of the integer address, plus the geo attributes that
    # only a RANGE join can supply.
    "v_payment_geo": """
        SELECT f.ingestion_id, f.payment_intent_id, f.event_date,
               f.ip_address_int,
               concat_ws('.', (f.ip_address_int >> 24) & 255,
                              (f.ip_address_int >> 16) & 255,
                              (f.ip_address_int >>  8) & 255,
                               f.ip_address_int        & 255) AS ip_address,
               b.ip_block_sk, b.country_code AS ip_country, b.city_id AS ip_city_id,
               b.isp_name, b.asn, b.connection_kind, b.is_anonymizer,
               b.latitude, b.longitude
        FROM fact_payment_attempt f
        JOIN dim_ip_block b
          ON f.ip_address_int BETWEEN b.ip_start AND b.ip_end
    """,
    # The merchant version in force on the day of the payment. Joining facts to
    # `dim_merchant` on merchant_sk already gives this; the view exists so that a
    # query written against the NATURAL key has a correct thing to use instead.
    "v_merchant_current": """
        SELECT * FROM dim_merchant WHERE is_current
    """,
    # Ultimate parent of every group, by walking the ownership graph upward.
    # This is the recursive query the catalogue is built around: `depth` bounds it,
    # but nothing in SQL forces a caller to respect that bound.
    "v_group_ultimate_parent": """
        WITH RECURSIVE up(group_sk, cursor_sk, hops) AS (
            SELECT group_sk, group_sk, 0 FROM dim_corporate_group
            UNION ALL
            SELECT u.group_sk, g.parent_group_sk, u.hops + 1
            FROM up u
            JOIN dim_corporate_group g ON g.group_sk = u.cursor_sk
            WHERE g.parent_group_sk <> -1 AND u.hops < 12
        )
        SELECT u.group_sk,
               argMax(u.cursor_sk, u.hops) AS ultimate_group_sk,
               max(u.hops)                 AS hops_to_ultimate
        FROM up u GROUP BY u.group_sk
    """,
    # Where the money ends up: every approved payment attributed to the ultimate
    # parent of the merchant that took it.
    "v_money_flow": """
        SELECT f.event_date,
               m.merchant_id, m.trade_name, m.country_code AS merchant_country,
               g.group_sk, g.group_name, g.incorporation_country AS operating_country,
               ug.group_sk    AS ultimate_group_sk,
               ug.group_name  AS ultimate_group_name,
               ug.incorporation_country AS ultimate_country,
               p.hops_to_ultimate,
               f.amount_eur_minor, f.fee_minor, f.interchange_minor, f.scheme_fee_minor
        -- Deduplicated, like every other money path. Reading the raw table here
        -- overstated collections by 3,117,222 EUR: the ingestion duplicates are a
        -- trap in the pipeline, not extra revenue.
        FROM v_attempt_dedup f
        JOIN dim_merchant m            ON m.merchant_sk = f.merchant_sk
        JOIN dim_corporate_group g     ON g.group_sk = m.group_sk
        JOIN v_group_ultimate_parent p ON p.group_sk = g.group_sk
        JOIN dim_corporate_group ug    ON ug.group_sk = p.ultimate_group_sk
        WHERE f.auth_status = 'approved' AND NOT f.is_test
    """,
    # One row per payment INTENT rather than per attempt. Anything that counts
    # money should start here; anything that studies retries should not.
    "v_payment_intent": """
        SELECT payment_intent_id,
               min(event_ts)                                    AS first_attempt_ts,
               max(event_ts)                                    AS last_attempt_ts,
               count(*)                                         AS attempts,
               max(attempt_seq)                                 AS max_attempt_seq,
               bool_or(auth_status = 'approved')                AS eventually_approved,
               any_value(merchant_sk)                           AS merchant_sk,
               any_value(customer_sk)                           AS customer_sk,
               any_value(currency_code)                         AS currency_code,
               any_value(amount_minor)                          AS amount_minor,
               max(amount_eur_minor) FILTER (WHERE auth_status = 'approved')
                                                                AS captured_eur_minor,
               any_value(product_category)                      AS product_category
        FROM v_attempt_dedup
        GROUP BY payment_intent_id
    """,
    # Devices seen with more than one customer. Households and fraud rings look
    # identical here on purpose -- telling them apart is the exercise.
    "v_shared_device": """
        SELECT d.device_sk, d.device_fingerprint, d.device_class, d.os_family,
               count(DISTINCT b.customer_sk) AS distinct_customers,
               sum(b.n_payments)             AS total_payments
        FROM bridge_customer_device b
        JOIN dim_device d ON d.device_sk = b.device_sk
        GROUP BY 1, 2, 3, 4
        HAVING count(DISTINCT b.customer_sk) > 1
    """,
}

CHECKS = [
    (
        "facts point at a real merchant version",
        "SELECT count(*) FROM fact_payment_attempt f "
        "LEFT JOIN dim_merchant m ON m.merchant_sk=f.merchant_sk WHERE m.merchant_sk IS NULL",
    ),
    (
        "facts point at a real card",
        "SELECT count(*) FROM fact_payment_attempt f "
        "LEFT JOIN dim_card c ON c.card_sk=f.card_sk WHERE c.card_sk IS NULL",
    ),
    (
        "facts point at a real device",
        "SELECT count(*) FROM fact_payment_attempt f "
        "LEFT JOIN dim_device d ON d.device_sk=f.device_sk WHERE d.device_sk IS NULL",
    ),
    (
        "order lines point at a real product",
        "SELECT count(*) FROM fact_order_line l "
        "LEFT JOIN dim_product p ON p.product_sk=l.product_sk WHERE p.product_sk IS NULL",
    ),
    (
        "every customer_sk is real or the declared guest sentinel",
        "SELECT count(*) FROM fact_payment_attempt f LEFT JOIN dim_customer c "
        "ON c.customer_sk=f.customer_sk WHERE c.customer_sk IS NULL AND f.customer_sk <> -1",
    ),
    (
        "every ip lands in exactly one block",
        "SELECT count(*) FROM (SELECT f.ingestion_id, count(*) n FROM fact_payment_attempt f "
        "JOIN dim_ip_block b ON f.ip_address_int BETWEEN b.ip_start AND b.ip_end "
        "GROUP BY 1 HAVING count(*) <> 1)",
    ),
    (
        "exactly one current version per merchant",
        "SELECT count(*) FROM (SELECT merchant_id FROM dim_merchant WHERE is_current "
        "GROUP BY 1 HAVING count(*) <> 1)",
    ),
    (
        "account manager allocations close at 100%",
        "SELECT count(*) FROM (SELECT merchant_natural_id, sum(allocation_pct) s "
        "FROM bridge_merchant_account_manager GROUP BY 1 HAVING abs(s-1.0) > 0.0005)",
    ),
    (
        "the ownership graph is acyclic (no group is its own ancestor)",
        "SELECT count(*) FROM v_group_ultimate_parent WHERE hops_to_ultimate >= 12",
    ),
    (
        "order lines sum to the payment amount",
        "SELECT count(*) FROM (SELECT l.payment_intent_id, sum(l.line_amount_minor) s "
        "FROM fact_order_line l GROUP BY 1) t JOIN v_payment_intent i "
        "USING (payment_intent_id) WHERE t.s <> i.amount_minor",
    ),
    (
        "an approved attempt always has an auth code",
        "SELECT count(*) FROM fact_payment_attempt "
        "WHERE auth_status='approved' AND auth_code IS NULL",
    ),
    (
        "a declined attempt never has an auth code",
        "SELECT count(*) FROM fact_payment_attempt "
        "WHERE auth_status<>'approved' AND auth_code IS NOT NULL",
    ),
    (
        "a decline always carries a reason",
        "SELECT count(*) FROM fact_payment_attempt "
        "WHERE auth_status='declined' AND decline_reason_code IS NULL",
    ),
    (
        "attempt_seq starts at 1 for every intent",
        "SELECT count(*) FROM (SELECT payment_intent_id FROM fact_payment_attempt "
        "GROUP BY 1 HAVING min(attempt_seq) <> 1)",
    ),
    (
        "attempt_seq has no gaps",
        "SELECT count(*) FROM (SELECT payment_intent_id FROM v_attempt_dedup "
        "GROUP BY 1 HAVING max(attempt_seq) <> count(*))",
    ),
    (
        "retries are strictly later than the attempt before them",
        "SELECT count(*) FROM (SELECT payment_intent_id, event_ts, attempt_seq, "
        "lag(event_ts) OVER (PARTITION BY payment_intent_id ORDER BY attempt_seq) prev "
        "FROM v_attempt_dedup) WHERE prev IS NOT NULL AND event_ts <= prev",
    ),
    (
        "nothing settles before it happened",
        "SELECT count(*) FROM fact_payment_attempt "
        "WHERE settlement_date IS NOT NULL AND settlement_date < event_date",
    ),
    (
        "only approved attempts carry fees",
        "SELECT count(*) FROM fact_payment_attempt "
        "WHERE auth_status <> 'approved' AND fee_minor <> 0",
    ),
    (
        "ingestion_id is unique",
        "SELECT count(*)-count(DISTINCT ingestion_id) FROM fact_payment_attempt",
    ),
    (
        "ingested_at is never before event_ts",
        "SELECT count(*) FROM fact_payment_attempt WHERE ingested_at < event_ts",
    ),
    # The declared traps have to BE there. A trap that only exists in the README is
    # worse than no trap, because it is a claim -- and this one was exactly that
    # until a reviewer went looking for it and found zero duplicate rows.
    (
        "the declared at-least-once duplicate rate is within tolerance",
        "SELECT CASE WHEN abs((count(*) - count(DISTINCT (payment_intent_id, attempt_seq)))"
        " * 1.0 / count(*) - 0.0035) < 0.0012 THEN 0 ELSE 1 END FROM fact_payment_attempt",
    ),
    (
        "a duplicate carries a different ingestion_id and a later ingested_at",
        "SELECT count(*) FROM (SELECT payment_intent_id, attempt_seq, "
        "count(DISTINCT ingestion_id) i, count(*) n FROM fact_payment_attempt "
        "GROUP BY 1,2 HAVING n > 1 AND i <> n)",
    ),
    (
        "the unknown member exists as a row",
        "SELECT CASE WHEN count(*) = 1 THEN 0 ELSE 1 END FROM dim_customer "
        "WHERE customer_sk = -1",
    ),
    (
        "no customer pays before signing up",
        "SELECT count(*) FROM (SELECT customer_sk, min(event_date) p "
        "FROM fact_payment_attempt WHERE customer_sk <> -1 GROUP BY 1) f "
        "JOIN dim_customer c USING (customer_sk) WHERE f.p < c.signed_up_on",
    ),
    (
        "the rate applied to a payment is the rate the reference table publishes",
        "SELECT count(*) FROM fact_payment_attempt f JOIN ref_fx_rate_daily r "
        "ON r.currency_code = f.currency_code AND r.rate_date = f.event_date "
        "WHERE abs(f.fx_rate - r.eur_rate) > 1e-9",
    ),
    (
        "fx_rate is NULL for euro payments and set for every other currency",
        "SELECT count(*) FROM fact_payment_attempt WHERE "
        "(currency_code = 'EUR') <> (fx_rate IS NULL)",
    ),
    (
        "risk_decision agrees with the decline reason it produced",
        "SELECT count(*) FROM fact_payment_attempt WHERE "
        "decline_reason_code = 'blocked_by_risk_engine' AND risk_decision <> 'block'",
    ),
    (
        "a card is never used before it was added",
        "SELECT count(*) FROM fact_payment_attempt f JOIN dim_card c "
        "USING (card_sk) WHERE f.event_date < c.added_on",
    ),
]

# Reconciliation. These only exist once the derived money tables are built, so
# they are kept apart and skipped rather than failed when those tables are absent.
# A controller's entire job is making two of these numbers agree, so if they do
# not agree in the data there is nothing to practise on.
RECONCILIATION = [
    (
        "a settlement batch equals the payments inside it",
        "SELECT count(*) FROM (SELECT f.settlement_batch_id, sum(f.amount_eur_minor) s "
        "FROM v_attempt_dedup f WHERE f.settlement_batch_id IS NOT NULL "
        "AND f.auth_status='approved' AND NOT f.is_test GROUP BY 1) t "
        "JOIN fact_settlement_batch b "
        "USING (settlement_batch_id) WHERE t.s <> b.gross_eur_minor",
    ),
    (
        "net equals gross minus the merchant discount",
        "SELECT count(*) FROM fact_settlement_batch "
        "WHERE net_eur_minor <> gross_eur_minor - fee_eur_minor",
    ),
    (
        "a payout equals the batches it pays",
        "SELECT count(*) FROM (SELECT b.settlement_date, b.group_sk, "
        "sum(b.net_eur_minor)-sum(b.reserve_eur_minor) s FROM fact_settlement_batch b "
        "WHERE b.batch_status='settled' GROUP BY 1,2) t JOIN fact_payout p "
        "ON p.settlement_date = t.settlement_date AND p.beneficiary_group_sk = t.group_sk "
        "WHERE p.paid_eur_minor <> t.s",
    ),
    # --- invariants added after the SECOND audit -----------------------------
    (
        "money only moves on a business day",
        "SELECT (SELECT count(*) FROM fact_settlement_batch b JOIN dim_date d "
        "ON d.full_date = b.settlement_date WHERE NOT d.is_business_day) + "
        "(SELECT count(*) FROM fact_payout p JOIN dim_date d ON d.full_date = p.value_date "
        "WHERE NOT d.is_business_day)",
    ),
    (
        "the ledger is denominated in euros and says so",
        "SELECT count(*) FROM fact_payout WHERE currency_code <> 'EUR'",
    ),
    (
        "a refund carries its euro amount",
        "SELECT count(*) FROM fact_refund WHERE refund_amount_eur_minor IS NULL "
        "OR refund_amount_eur_minor <= 0",
    ),
    (
        "the beneficiary account is in the beneficiary's own country",
        "SELECT count(*) FROM fact_payout p JOIN dim_corporate_group g "
        "ON g.group_sk = p.beneficiary_group_sk "
        "WHERE left(p.beneficiary_iban, 2) <> g.incorporation_country",
    ),
    # Mod-97 reduced DIGIT BY DIGIT. The first version chunked the numeric string
    # into groups of up to nine and folded them with `a*10^9 + b`, which is only
    # correct when every chunk is exactly nine digits long -- the last one never is.
    # It reported sixteen valid IBANs that a Python reimplementation could not find:
    # the check was broken, not the data. A failing invariant is a claim about the
    # data, so it has to be right before the data is blamed.
    (
        "no synthetic IBAN passes the mod-97 check",
        "SELECT count(*) FROM (SELECT list_reduce([CAST(d AS BIGINT) FOR d IN "
        "  string_split(list_reduce([CASE WHEN c BETWEEN 'A' AND 'Z' "
        "    THEN CAST(ascii(c) - 55 AS VARCHAR) ELSE c END FOR c IN "
        "    string_split(substr(payout_iban, 5) || substr(payout_iban, 1, 4), '')], "
        "    (a, b) -> a || b), '')], (a, b) -> (a * 10 + b) % 97) AS m "
        "FROM dim_corporate_group) WHERE m = 1",
    ),
    (
        "nothing is dated after the last day of the warehouse",
        "SELECT (SELECT count(*) FROM fact_refund WHERE refund_date > DATE '2026-08-31') + "
        "(SELECT count(*) FROM fact_dispute WHERE opened_on > DATE '2026-08-31')",
    ),
    (
        "test traffic never reaches the ledger",
        "SELECT count(*) FROM fact_settlement_batch b JOIN (SELECT DISTINCT settlement_batch_id "
        "FROM fact_payment_attempt WHERE is_test) t USING (settlement_batch_id)",
    ),
    (
        "a payment is a test in all its attempts or in none",
        "SELECT count(*) FROM (SELECT payment_intent_id FROM fact_payment_attempt "
        "GROUP BY 1 HAVING count(DISTINCT is_test) > 1)",
    ),
    (
        "age-restricted goods are never sold to a minor",
        "SELECT count(*) FROM fact_order_line l JOIN dim_product p USING (product_sk) "
        "JOIN v_payment_intent i USING (payment_intent_id) JOIN dim_customer c "
        "ON c.customer_sk = i.customer_sk WHERE p.is_age_restricted "
        "AND date_diff('year', c.birth_date, l.event_date) < 18 "
        # The declared impossible-birth-date cohort is excluded: a checkout cannot
        # validate an age it was given as 1900-01-01 or as a date in 2031, and pretending
        # otherwise would turn a documented trap into a failing invariant.
        "AND c.birth_date BETWEEN DATE '1920-01-01' AND DATE '2008-12-31'",
    ),
    (
        "there is exactly one chief executive and exactly one root",
        "SELECT (SELECT count(*) FROM dim_employee WHERE org_level = 0) - 1 + "
        "(SELECT count(*) FROM dim_employee WHERE manager_employee_sk = -1) - 1",
    ),
    (
        "an account manager is never assigned before being hired",
        "SELECT count(*) FROM bridge_merchant_account_manager b JOIN dim_employee e "
        "USING (employee_sk) WHERE b.valid_from < e.hired_on",
    ),
    (
        "the declared expired-card cohort is delivered",
        "SELECT CASE WHEN abs(count(*) FILTER (WHERE expiry_year <= 2026) * 1.0 / count(*) "
        "- 0.022) < 0.004 THEN 0 ELSE 1 END FROM dim_card",
    ),
    (
        "the support note quotes the customer's own phone number",
        "SELECT count(*) FROM dim_customer WHERE support_note LIKE '%on +%' "
        "AND support_note NOT LIKE '%' || phone_e164 || '%'",
    ),
    (
        "product_sk carries no information about popularity",
        "SELECT CASE WHEN abs(corr(product_sk, popularity_weight)) < 0.05 THEN 0 ELSE 1 END "
        "FROM dim_product",
    ),
    (
        "a reserve is only withheld from a high-risk merchant",
        "SELECT count(*) FROM fact_settlement_batch b JOIN dim_merchant m "
        "ON m.merchant_id=b.merchant_id AND m.is_current JOIN ref_mcc c ON c.mcc=m.mcc "
        "WHERE b.reserve_eur_minor > 0 AND NOT c.is_high_risk",
    ),
    (
        "a refund never exceeds what was charged",
        "SELECT count(*) FROM fact_refund r JOIN v_payment_intent i "
        "USING (payment_intent_id) WHERE r.refund_amount_minor > i.amount_minor",
    ),
    (
        "a refund is never dated before the payment",
        "SELECT count(*) FROM fact_refund r JOIN v_payment_intent i "
        "USING (payment_intent_id) WHERE r.refund_date < CAST(i.first_attempt_ts AS DATE)",
    ),
    (
        "dispute stages run 1..n with no gaps",
        "SELECT count(*) FROM (SELECT payment_intent_id FROM fact_dispute GROUP BY 1 "
        "HAVING min(stage_no) <> 1 OR max(stage_no) <> count(*))",
    ),
    (
        "a dispute is never opened before the payment",
        "SELECT count(*) FROM fact_dispute d JOIN v_payment_intent i "
        "USING (payment_intent_id) WHERE d.opened_on < CAST(i.first_attempt_ts AS DATE)",
    ),
    (
        "every disputed payment was actually approved",
        "SELECT count(*) FROM fact_dispute d JOIN v_payment_intent i "
        "USING (payment_intent_id) WHERE NOT i.eventually_approved",
    ),
]


def emit_sql(data_dir: pathlib.Path, glob_root: str) -> str:
    """Portable catalogue script: the same views, rooted at `glob_root`.

    A DuckDB view stores the literal path it was created with AND validates the
    glob at creation time, so a catalogue built on the host for a container mount
    cannot be built at all -- it fails on CREATE VIEW, not later. The way out is
    not to ship a database file to the container: ship the script, and let the
    container build its catalogue against whatever it actually has mounted. It
    then cannot go stale, which is worth more than the second of startup it costs.
    """
    parts = [
        f"-- generated from {data_dir.name}; do not edit by hand",
        "SET enable_progress_bar = false;",
    ]
    for t in sorted(d.name for d in data_dir.iterdir() if d.is_dir()):
        parts.append(
            f"CREATE OR REPLACE VIEW {t} AS SELECT * FROM read_parquet("
            f"'{glob_root}/{t}/**/*.parquet', hive_partitioning=true, "
            "union_by_name=true);"
        )
    for name, sql in VIEWS.items():
        parts.append(f"CREATE OR REPLACE VIEW {name} AS {' '.join(sql.split())};")
    return "\n".join(parts) + "\n"


def build(data_dir: pathlib.Path, db_path: pathlib.Path, glob_root: str | None = None) -> int:
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    tables = sorted(d.name for d in data_dir.iterdir() if d.is_dir())
    for t in tables:
        glob = f"{data_dir / t}/**/*.parquet"
        con.execute(
            f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{glob}', "
            f"hive_partitioning=true, union_by_name=true)"
        )
    for name, sql in VIEWS.items():
        con.execute(f"CREATE VIEW {name} AS {sql}")

    print(f"{len(tables)} tables + {len(VIEWS)} views -> {db_path}")
    print(f"{'table':34s} {'rows':>14s}")
    total = 0
    for t in tables:
        n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        total += n
        print(f"  {t:32s} {n:14,d}")
    print(f"  {'TOTAL':32s} {total:14,d}")

    print("\nintegrity")
    failures = 0
    have_derived = "fact_settlement_batch" in tables
    if not have_derived:
        print("  (reconciliation checks skipped: derived money tables not built yet)")
    for label, sql in CHECKS + (RECONCILIATION if have_derived else []):
        try:
            bad = con.execute(sql).fetchone()[0]
        except Exception as exc:
            print(f"  ERROR  {label}: {exc}")
            failures += 1
            continue
        mark = "ok    " if bad == 0 else "FAIL  "
        if bad:
            failures += 1
        print(f"  {mark} {label}" + (f"  ({bad:,} bad rows)" if bad else ""))
    con.close()
    print(f"\n{failures} failing check(s)")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=pathlib.Path, default="datagen/out/dev")
    ap.add_argument("--db", type=pathlib.Path, default="datagen/out/cierzo-dev.duckdb")
    ap.add_argument(
        "--emit-sql",
        type=pathlib.Path,
        default=None,
        help="write a portable catalogue script instead of a database",
    )
    ap.add_argument(
        "--glob-root",
        default="/warehouse/data",
        help="path the emitted script should reference",
    )
    a = ap.parse_args()
    if a.emit_sql:
        a.emit_sql.parent.mkdir(parents=True, exist_ok=True)
        a.emit_sql.write_text(emit_sql(a.data, a.glob_root))
        print(f"wrote {a.emit_sql}")
        return 0
    return 1 if build(a.data, a.db) else 0


if __name__ == "__main__":
    raise SystemExit(main())
