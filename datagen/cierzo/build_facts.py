"""Day-by-day construction of the fact tables."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pyarrow as pa
import pyarrow.compute

from . import config, facts, shape
from .dims_people import CATEGORIES

_EPOCH = dt.date(1970, 1, 1)


def _fee_model(amount_minor, mdr_bps, funding, inter_regional, high_risk):
    """What CIERZO charges and what it pays away.

    Split three ways because that is how the money actually moves and because
    finance cannot reconcile a single blended number: the merchant discount rate
    is revenue, interchange goes to the issuer, the scheme fee goes to the card
    network. Consumer debit interchange is capped at 20 bps in the EEA and credit
    at 30 -- caps that a query over this data will find, which is the point.
    """
    fee = np.round(amount_minor * mdr_bps / 10_000.0).astype(np.int64)
    # Consumer prepaid is capped at the DEBIT rate by the same regulation; the 65 bps
    # band belongs to commercial cards, which this dataset does not issue. Leaving
    # prepaid at 65 kept 0.55 % of domestic payments above a cap they are subject to.
    ic_bps = np.where(funding == "credit", 30.0, 20.0)
    # Regulation (EU) 2015/751 caps consumer interchange at 20 bps on debit and
    # 30 bps on credit, and the cap applies to any transaction where BOTH sides are
    # in the EEA -- cross-border inside the EEA included. Only a non-EEA issuer
    # escapes it. Multiplying by 3.2 for every cross-border payment broke the cap on
    # 55.5 % of approved authorisations, which is not a modelling simplification: it
    # is a number that would get an acquirer fined.
    # The multiplier applies only INTER-REGIONALLY: one side inside the EEA and the
    # other outside. A UK card at a UK merchant is domestic under UK rules, which
    # mirror the EU caps -- treating every non-EEA issuer as inter-regional put 15.7 %
    # of DOMESTIC payments above the cap they are not even subject to.
    ic_bps = np.where(inter_regional, ic_bps * 3.2, ic_bps)
    interchange = np.round(amount_minor * ic_bps / 10_000.0).astype(np.int64)
    scheme = np.round(amount_minor * 1.1 / 10_000.0).astype(np.int64) + 2
    fee = fee + np.where(high_risk, 15, 0)
    return fee, interchange, scheme


def build_day(day_idx: int, day: dt.date, k: int, rng, ctx) -> tuple[pa.Table, pa.Table]:
    """Generate one day of authorization attempts and their order lines."""
    if k == 0:
        return None, None

    tr = ctx["traits"]
    cust = ctx["cust_pool"][ctx["cust_off"][day_idx] : ctx["cust_off"][day_idx] + k]
    ci = cust - 1

    # ---- what kind of shop, and which one -------------------------------------
    # With probability `affinity_strength` the customer buys inside their dominant
    # category; otherwise they wander, weighted by how big each category is
    # overall. This single line is what makes segment-level profiling find signal.
    dominant = rng.random(k) < tr["affinity_strength"][ci]
    cat_idx = np.where(
        dominant, tr["affinity_cat"][ci], rng.choice(ctx["mi"].present, k, p=ctx["mi"].cat_p)
    ).astype(np.int8)
    nat = ctx["mi"].sample(rng, cat_idx)

    # Merchant loyalty: a repeat customer goes back to the same shop.
    loyal = rng.random(k) < tr["merchant_loyalty"][ci]
    nat = np.where(loyal & dominant, ctx["pref_merchant"][ci], nat).astype(np.int32)

    # GEOGRAPHY. Most people buy at home. Choosing the merchant by category alone
    # -- the first version -- drew its country independently of the customer's and
    # produced 86.7 % cross-border traffic against a European reality of 15-25 %.
    # That single number then poisoned approval rates, interchange and the whole
    # risk layer, because `is_cross_border` feeds all three.
    # AGE-GATED SECTORS REFUSE THE SALE. Betting, alcohol and tobacco merchants sell
    # nothing that an under-18 may buy, so a minor is not routed to them at all --
    # re-drawing the PRODUCT was the first attempt and it could not work, because in
    # those three categories every SKU is restricted and the fallback landed back on
    # one. Ages are measured at the end of the window, so somebody who is 18 on the
    # last day was 16 on the first: 9,626 lines went to buyers under 18 on the date
    # of purchase. 6,588 days is 18 years plus a fortnight of margin, so the rule is
    # conservative against the calendar-aware check the gate applies.
    days_alive = (day - _EPOCH).days - tr["birth_days"][ci]
    minor = days_alive < 6588
    gated = np.isin(cat_idx, ctx["age_gated_categories"])
    refuse = minor & gated
    if refuse.any():
        cat_idx = cat_idx.copy()
        cat_idx[refuse] = rng.choice(
            ctx["open_categories"], int(refuse.sum()), p=ctx["open_category_p"]
        ).astype(np.int8)
        nat = np.where(refuse, ctx["mi"].sample(rng, cat_idx), nat).astype(np.int32)

    domestic = rng.random(k) < ctx["domestic_share"]
    nat = np.where(
        domestic,
        ctx["mi"].sample_domestic(
            rng,
            cat_idx,
            tr["country_idx"][ci],
            nat,
            ctx["region_of_country"][tr["country_idx"][ci]],
        ),
        nat,
    ).astype(np.int32)
    cat_idx = ctx["mi_cat_index"][nat]

    merchant_sk = ctx["scd2"][day_idx][nat]

    # Pick the merchant's site IN THE CUSTOMER'S COUNTRY when it has one; otherwise
    # any of its sites. This is what makes `is_cross_border` a property of where the
    # payment was actually taken rather than of where the company is registered.
    want = nat.astype(np.int64) * 100 + tr["country_idx"][ci].astype(np.int64)
    pos = np.searchsorted(ctx["site_keys_sorted"], want)
    pos_c = np.clip(pos, 0, ctx["site_keys_sorted"].size - 1)
    found = ctx["site_keys_sorted"][pos_c] == want
    site_off = np.where(
        found,
        ctx["site_idx_sorted"][pos_c],
        ctx["site_start"][nat] + (rng.random(k) * ctx["site_count"][nat]).astype(np.int64),
    )
    site_sk = (site_off + 1).astype(np.int64)

    # ---- the basket, BEFORE the amount ----------------------------------------
    sizes = shape.basket_sizes(rng, k)
    total_lines = int(sizes.sum())
    line_of = np.repeat(np.arange(k, dtype=np.int64), sizes)
    prod = ctx["pi"].sample(rng, cat_idx[line_of])
    qty = np.where(
        rng.random(total_lines) < 0.82, 1, 1 + rng.geometric(0.55, total_lines)
    ).astype(np.int16)
    unit_list = ctx["prod_price"][prod]
    # Price-sensitive customers buy more of what is discounted, so the discount rate
    # is a function of the buyer and not of the shop alone.
    sens = tr["price_sensitivity"][ci][line_of]
    disc = np.where(
        rng.random(total_lines) < 0.09 + 0.28 * sens,
        rng.choice(
            [0.05, 0.10, 0.15, 0.20, 0.30, 0.50],
            total_lines,
            p=[0.28, 0.26, 0.18, 0.14, 0.10, 0.04],
        ),
        0.0,
    )

    # ---- currency, decided BEFORE the lines are priced -------------------------
    mcur = ctx["merchant_currency_idx"][nat]
    cur_idx = np.where(
        rng.random(k) < 0.88, mcur, ctx["country_currency_idx"][tr["country_idx"][ci]]
    ).astype(np.int8)
    rate = ctx["fx_by_day"][day_idx][cur_idx]  # local units per EUR, per row
    # `minor_scale` carries each currency's decimal places relative to the euro's
    # two. Without it a 40 EUR Hungarian basket was written as 1.9 million forints,
    # because "minor units" of a zero-decimal currency are whole forints.
    scale = rate * ctx["minor_scale"][cur_idx]

    # The catalogue is priced in euro cents; the LINE is priced in the currency of
    # the transaction; the authorised amount is the sum of its own lines -- in that
    # order. Converting the TOTAL instead of the lines left 214,123 baskets whose
    # lines did not add up to the payment they belonged to, and grain is the one
    # thing this dataset exists to teach.
    #
    # Getting the direction wrong on the same line, in the first version, gave a
    # median Danish ticket of 34.88 DKK that converted to 4.62 EUR. Four independent
    # reviewers found it from four different directions, which is what an
    # unreconcilable money column does.
    unit_list_local = np.maximum(np.round(unit_list * scale[line_of]).astype(np.int64), 1)
    unit_paid = np.maximum(
        np.round(unit_list * (1 - disc) * scale[line_of]).astype(np.int64), 1
    )
    line_amount = unit_paid * qty
    amount_minor = np.maximum(
        np.bincount(line_of, weights=line_amount, minlength=k).astype(np.int64), 1
    )
    amount_eur_minor = np.maximum(np.round(amount_minor / scale).astype(np.int64), 1)
    amount_eur = amount_eur_minor / 100.0

    # ---- where from: device and network ---------------------------------------
    dcount = np.maximum(ctx["dev_count"][ci], 1)
    dev_sk = ctx["dev_flat"][ctx["dev_start"][ci] + (rng.random(k) * dcount).astype(np.int64)]
    home_block = ctx["home_block"][ci]
    roam = rng.random(k)
    travelling = roam < config.TRAVEL_SESSION_SHARE
    on_vpn = (roam >= config.TRAVEL_SESSION_SHARE) & (
        roam < config.TRAVEL_SESSION_SHARE + config.VPN_IP_SHARE
    )
    block = np.where(
        travelling,
        rng.integers(0, ctx["n_blocks"], k),
        np.where(
            on_vpn, ctx["vpn_blocks"][rng.integers(0, ctx["vpn_blocks"].size, k)], home_block
        ),
    ).astype(np.int64)
    ip_int = (ctx["block_start"][block] + rng.integers(1, 255, k)).astype(np.uint32)
    ip_risk = ctx["block_risk"][block]
    geo_country = ctx["block_country_idx"][block]

    # ---- channel, 3-D Secure, risk, authorisation ------------------------------
    is_test = rng.random(k) < config.TEST_TRAFFIC_SHARE
    hour = np.searchsorted(ctx["hour_cum"], rng.random(k)).clip(0, 23).astype(np.int8)
    channel = np.where(
        ctx["site_is_cp"][site_sk - 1],
        2,
        np.where(rng.random(k) < 0.11, 4, np.where(tr["channel_pref"][ci] == 1, 1, 0)),
    ).astype(np.int8)

    card_off = ctx["card_start"][ci] + (rng.random(k) * ctx["card_count"][ci]).astype(np.int64)

    def _expired(off):
        return (ctx["card_exp_year"][off] < day.year) | (
            (ctx["card_exp_year"][off] == day.year) & (ctx["card_exp_month"][off] < day.month)
        )

    # People replace an expired card. Most of the time the card on file gets
    # updated before it is presented again, so an expired card reaching the issuer
    # is the exception rather than the norm -- three attempts in four fall back to
    # the customer's primary card instead.
    swap = _expired(card_off) & (rng.random(k) < 0.90)
    card_off = np.where(swap, ctx["card_start"][ci], card_off)
    card_sk = (card_off + 1).astype(np.int32)
    funding = ctx["card_funding"][card_off]
    issuer_cc = ctx["card_issuer_cc_idx"][card_off]
    card_expired = _expired(card_off)
    # Issuer country versus the country the payment was ACCEPTED in, which is the
    # definition an acquirer uses and the one the interchange caps hang off.
    is_cross_border = (issuer_cc != ctx["site_country"][site_off]).astype(np.int8)
    issuer_out = ctx["issuer_is_non_eea"][issuer_cc]
    site_out = ctx["issuer_is_non_eea"][ctx["site_country"][site_off]]
    inter_regional = issuer_out != site_out

    high_risk = ctx["mcc_high_risk"][nat]
    ring = (ctx["ring_id"][cust] > 0).astype(np.int8)
    device_new = (rng.random(k) < 0.14).astype(np.int8)
    rscore = facts.risk_score(
        rng,
        amount_eur=amount_eur,
        ip_risk=ip_risk,
        is_cross_border=is_cross_border,
        device_new=device_new,
        ring_member=ring,
        hour=hour,
        high_risk_mcc=high_risk,
    )

    supports_3ds = ctx["site_supports_3ds"][site_sk - 1]
    # PSD2: authentication is the default for e-commerce in the EEA, with
    # exemptions for low value and for low-risk merchants. That is why a
    # `sca_exemption` column exists at all, and why it is NULL when none applied.
    exempt_low_value = amount_eur < 30.0
    tds_requested = supports_3ds & (channel != 2) & (~exempt_low_value | (rng.random(k) < 0.18))
    tds_ok = tds_requested & (rng.random(k) < 0.913)

    p_ok = facts.approval_probability(
        risk_score=rscore,
        amount_eur=amount_eur.astype(np.float32),
        is_cross_border=is_cross_border,
        funding=funding,
        three_ds_ok=tds_ok.astype(np.float32),
        hour=hour,
        issuer_base=ctx["issuer_base"][ctx["card_issuer_idx"][card_off]],
        card_expired=card_expired.astype(np.float32),
        merchant_high_risk=high_risk.astype(np.float32),
    )
    # Thresholds that leave a real queue. At `> 940` the review queue held 134 rows
    # and the block queue held ONE in two years: a risk engine that never fires is
    # not a risk engine, and `risk_decision` was a degenerate column.
    blocked = rscore > ctx["risk_block_threshold"]
    review = (~blocked) & (rscore > ctx["risk_review_threshold"])
    approved = (rng.random(k) < p_ok) & ~blocked

    reason_idx = ctx["reason_sampler"](rng, k, card_expired, blocked, rscore)
    soft = ctx["reason_soft"][reason_idx]
    lift = ctx["reason_lift"][reason_idx]
    attempts, _ = facts.retry_plan(rng, approved, reason_idx, soft, lift)

    # ---- expand intents into attempts ------------------------------------------
    rep = np.repeat(np.arange(k, dtype=np.int64), attempts)
    seq = np.concatenate([np.arange(1, a + 1) for a in attempts]).astype(np.int8)
    n = rep.size
    is_last = seq == attempts[rep]
    # The final attempt of a retried intent succeeds when the plan said so; every
    # earlier attempt is by definition a decline.
    final_ok = np.zeros(k, dtype=bool)
    final_ok[approved] = True
    retried = attempts > 1
    final_ok[retried] = rng.random(int(retried.sum())) < lift[retried]
    row_approved = is_last & final_ok[rep]

    # The intent has ONE start time; every retry is an offset from it. Drawing an
    # independent second-of-hour per ROW instead let attempt 3 land before attempt
    # 2 in 4 % of retried intents -- a window function ordered by attempt_seq would
    # still have looked right, and one ordered by event_ts would have silently
    # returned the wrong "last attempt".
    intent_start = hour.astype(np.int64) * 3600 + rng.integers(0, 3600, k)
    # Lognormal, not uniform. A perfectly flat gap between 20 and 900 seconds is
    # one histogram away from being identified as synthetic, and retry timing in
    # the wild is heavily skewed: most retries are automatic and immediate, a few
    # are a human trying again after lunch.
    gap = np.where(seq == 1, 0, np.clip(rng.lognormal(4.4, 1.15, n), 8, 20_000)).astype(
        np.int64
    )
    group_start = np.concatenate([[0], np.cumsum(attempts)[:-1]]).astype(np.int64)
    cs = np.cumsum(gap)
    within = cs - np.repeat(cs[group_start], attempts)
    event_ts = ((day - _EPOCH).days * 86_400 + intent_start[rep] + within).astype(np.int64)

    intent_id = ctx["intent_base"] + rep + ctx["intent_off"][day_idx]
    # A monotonic ingestion id ACROSS the whole build, not per day. Restarting the
    # counter every morning made it a duplicate key on 99.6 % of rows, which then
    # broke every check that grouped by it -- including one that appeared to be
    # about IP ranges.
    ing_id = ctx["ingest_cursor"] + np.arange(n, dtype=np.int64)
    ctx["ingest_cursor"] += n

    late = rng.random(n) < config.LATE_ARRIVAL_SHARE
    ingested = event_ts + np.where(
        late, rng.integers(86_400, 6 * 86_400, n), rng.integers(2, 900, n)
    )

    # Fees are computed on the EUR amount, because that is what they are settled and
    # reported in. Computing them on the LOCAL amount and writing them into columns
    # named `*_eur_minor` closed 6,342 settlement batches with a negative net.
    fee, interchange, scheme_fee = _fee_model(
        amount_eur_minor[rep],
        ctx["merchant_mdr"][day_idx][nat][rep],
        funding[rep],
        inter_regional[rep],
        high_risk[rep],
    )
    zero = ~row_approved
    fee[zero] = 0
    interchange[zero] = 0
    scheme_fee[zero] = 0

    delay = ctx["mcc_settle_delay"][nat][rep]
    settle_day = (day - _EPOCH).days + delay
    # Nothing settles after the last day of the window: those rows are genuinely
    # pending, which is a different thing from missing.
    horizon = (config.END_DATE - _EPOCH).days
    # A test payment never enters the production ledger, so it never gets a batch.
    # Assigning one and then filtering it out downstream left the fact table claiming
    # a settlement that the settlement table did not contain.
    settled = row_approved & (settle_day <= horizon) & ~is_test[rep]
    batch_id = np.where(
        settled, ctx["merchant_natural_to_batchbase"][nat][rep] * 100_000 + settle_day, -1
    ).astype(np.int64)

    amt = amount_minor[rep]
    drift = rng.random(n) < config.DEPRECATED_COL_DRIFT
    amount_cents_deprecated = np.where(drift, amt + rng.integers(-99, 99, n), amt)

    tbl = pa.table(
        {
            "ingestion_id": pa.array(ing_id, pa.int64()),
            "payment_intent_id": pa.array(intent_id, pa.int64()),
            "attempt_seq": pa.array(seq, pa.int8()),
            "event_ts": pa.array(event_ts, pa.timestamp("s")),
            "event_date": pa.array(
                np.full(n, (day - _EPOCH).days, dtype=np.int32), pa.date32()
            ),
            "ingested_at": pa.array(ingested, pa.timestamp("s")),
            "merchant_sk": pa.array(merchant_sk[rep], pa.int32()),
            "site_sk": pa.array(site_sk[rep].astype(np.int32), pa.int32()),
            "customer_sk": pa.array(ctx["guest_mask"](rng, cust)[rep], pa.int32()),
            "card_sk": pa.array(card_sk[rep], pa.int32()),
            "device_sk": pa.array(dev_sk[rep], pa.int32()),
            "product_category": pa.array(
                np.array(CATEGORIES)[cat_idx[rep]]
            ).dictionary_encode(),
            "channel": pa.array(np.array(facts.CHANNELS)[channel[rep]]).dictionary_encode(),
            "currency_code": pa.array(
                np.array(config.CURRENCIES)[cur_idx[rep]]
            ).dictionary_encode(),
            "amount_minor": pa.array(amt, pa.int64()),
            "amount_eur_minor": pa.array(amount_eur_minor[rep], pa.int64()),
            "amount_cents": pa.array(amount_cents_deprecated, pa.int64()),
            # NULL, not NaN, when the currency is EUR: "no conversion applied" is a
            # different fact from "the rate is missing", and NaN makes every aggregate
            # over the column silently NaN as well.
            "fx_rate": pa.array(np.where(cur_idx[rep] == 0, None, rate[rep]), pa.float64()),
            "auth_status": pa.array(
                np.where(
                    row_approved,
                    "approved",
                    np.where(
                        blocked[rep],
                        "declined",
                        np.where(rng.random(n) < 0.006, "timeout", "declined"),
                    ),
                )
            ).dictionary_encode(),
            "decline_reason_code": pa.array(
                np.where(row_approved, None, ctx["reason_code"][reason_idx[rep]]), pa.string()
            ).dictionary_encode(),
            "auth_code": pa.array(
                np.where(
                    row_approved, np.char.zfill(rng.integers(0, 10**6, n).astype(str), 6), None
                ),
                pa.string(),
            ),
            "risk_score": pa.array(rscore[rep], pa.int16()),
            # Derived from the SAME booleans that decided the outcome. Recomputing the
            # bands from the score at write time let 18,588 rows say
            # `decline_reason = blocked_by_risk_engine` while `risk_decision = allow`.
            "risk_decision": pa.array(
                np.where(blocked[rep], "block", np.where(review[rep], "review", "allow"))
            ).dictionary_encode(),
            "three_ds_requested": pa.array(tds_requested[rep], pa.bool_()),
            "three_ds_result": pa.array(
                np.where(
                    ~tds_requested[rep], None, np.where(tds_ok[rep], "authenticated", "failed")
                ),
                pa.string(),
            ).dictionary_encode(),
            "sca_exemption": pa.array(
                np.where(
                    tds_requested[rep],
                    None,
                    np.where(exempt_low_value[rep], "low_value", "trusted_merchant"),
                ),
                pa.string(),
            ).dictionary_encode(),
            "ip_address_int": pa.array(ip_int[rep], pa.uint32()),
            "geo_country": pa.array(ctx["country_codes"][geo_country[rep]]).dictionary_encode(),
            "is_cross_border": pa.array(is_cross_border[rep].astype(bool), pa.bool_()),
            # Drawn per INTENT and expanded, not per row. A payment cannot be a test in
            # its first attempt and production in its second, and 8,167 of them were.
            "is_test": pa.array(is_test[rep], pa.bool_()),
            "latency_ms": pa.array(
                np.round(rng.lognormal(5.7, 0.62, n)).astype(np.int32), pa.int32()
            ),
            "fee_minor": pa.array(fee, pa.int64()),
            "interchange_minor": pa.array(interchange, pa.int64()),
            "scheme_fee_minor": pa.array(scheme_fee, pa.int64()),
            "settlement_batch_id": pa.array(np.where(settled, batch_id, None), pa.int64()),
            "settlement_date": pa.array(np.where(settled, settle_day, None), pa.date32()),
        }
    )

    # AT-LEAST-ONCE INGESTION. A declared share of rows arrives twice: same business
    # tuple, new ingestion_id, later ingested_at. This was declared in `config`,
    # documented in the README and NEVER IMPLEMENTED -- the reviewer who went
    # looking for it found zero duplicate rows in 6.6 M. A trap that only exists in
    # the documentation is worse than no trap, because it is a claim.
    dup_mask = rng.random(n) < config.DUPLICATE_ROW_SHARE
    n_dup = int(dup_mask.sum())
    if n_dup:
        dup = tbl.filter(pa.array(dup_mask))
        new_ing = ctx["ingest_cursor"] + np.arange(n_dup, dtype=np.int64)
        ctx["ingest_cursor"] += n_dup
        cols = {name: dup.column(name) for name in dup.schema.names}
        cols["ingestion_id"] = pa.array(new_ing, pa.int64())
        cols["ingested_at"] = pa.compute.add(
            dup.column("ingested_at"), pa.array(rng.integers(30, 7200, n_dup), pa.duration("s"))
        )
        tbl = pa.concat_tables([tbl, pa.table(cols)])

    lines = pa.table(
        {
            "payment_intent_id": pa.array(
                (ctx["intent_base"] + np.arange(k) + ctx["intent_off"][day_idx])[line_of],
                pa.int64(),
            ),
            "line_no": pa.array(
                (
                    np.arange(total_lines)
                    - np.repeat(np.concatenate([[0], np.cumsum(sizes)[:-1]]), sizes)
                    + 1
                ).astype(np.int16),
                pa.int16(),
            ),
            "event_date": pa.array(
                np.full(total_lines, (day - _EPOCH).days, dtype=np.int32), pa.date32()
            ),
            "product_sk": pa.array((prod + 1).astype(np.int32), pa.int32()),
            "merchant_sk": pa.array(merchant_sk[line_of], pa.int32()),
            "quantity": pa.array(qty, pa.int16()),
            "unit_list_price_minor": pa.array(unit_list_local, pa.int64()),
            "unit_paid_minor": pa.array(unit_paid, pa.int64()),
            "discount_pct": pa.array((disc * 100).astype(np.float32), pa.float32()),
            "line_amount_minor": pa.array(line_amount, pa.int64()),
            "currency_code": pa.array(
                np.array(config.CURRENCIES)[cur_idx[line_of]]
            ).dictionary_encode(),
        }
    )
    # Exposed so the orchestrator can MEASURE the concentration actually produced,
    # instead of republishing the target it solved for.
    ctx["day_merchant_hits"] = (nat + 1).astype(np.int32)
    return tbl, lines
