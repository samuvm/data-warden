#!/usr/bin/env python
"""CIERZO synthetic dataset generator.

    uv run --with-requirements datagen/requirements.txt \
        python datagen/generate.py --profile dev --out datagen/out

Deterministic: same profile plus same seed gives byte-identical Parquet. Nothing
in the pipeline reads the clock, the filesystem or the network.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import shutil
import sys
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from cierzo import (
    build_facts,
    config,
    dims_digital,
    dims_org,
    dims_people,
    facts,
    pools,
    refs,
    shape,
)

_EPOCH = dt.date(1970, 1, 1)


def index_of(haystack: np.ndarray, needles: np.ndarray) -> np.ndarray:
    """Position of each needle inside haystack, for an UNSORTED haystack.

    `np.searchsorted` alone silently returns len(haystack) for anything it cannot
    place, and the country list here is ordered by traffic rather than
    alphabetically. That produced an out-of-bounds index on the first run; on a
    longer list it would have produced a WRONG COUNTRY, which is worse.
    """
    order = np.argsort(haystack)
    pos = order[np.searchsorted(haystack, needles, sorter=order)]
    if not np.array_equal(haystack[pos], needles):
        missing = sorted(set(np.asarray(needles).tolist()) - set(np.asarray(haystack).tolist()))
        raise KeyError(f"values absent from lookup: {missing[:8]}")
    return pos


def first_day_per_customer(
    cust_pool: np.ndarray, counts: np.ndarray, per_day: np.ndarray, n_customers: int
) -> np.ndarray:
    """Day index of each customer's earliest payment; -1 for those who never pay.

    Positions in `cust_pool` are consumed in day order, so a position's day index is
    non-decreasing. A STABLE argsort by customer therefore leaves each customer's
    positions in ascending order and the first one is their earliest day -- which
    makes this two sorts instead of a 62-million-element `np.minimum.at`.
    """
    day_of_position = np.repeat(np.arange(len(per_day), dtype=np.int32), per_day)
    order = np.argsort(cust_pool, kind="stable")
    sorted_cust = cust_pool[order]
    starts = np.searchsorted(sorted_cust, np.arange(1, n_customers + 1), side="left")
    out = np.full(n_customers, -1, dtype=np.int32)
    pays = counts > 0
    out[pays] = day_of_position[order[starts[pays]]]
    return out


def unknown_customer_row(customers: pa.Table) -> pa.Table:
    """The UNKNOWN MEMBER: customer_sk = -1, for guest checkout.

    A dimensional model has a row for its unknown member. A warehouse that uses a
    sentinel key with nothing behind it has hundreds of thousands of dangling
    foreign keys and an INNER JOIN that silently drops every guest payment. The row
    exists, it is labelled, and every other attribute is NULL because nothing is
    known about a guest.
    """
    row = {}
    for field in customers.schema:
        if field.name == "customer_sk":
            row[field.name] = pa.array([-1], field.type)
        elif field.name == "customer_id":
            row[field.name] = pa.array(["CUS-UNKNOWN"], field.type)
        else:
            row[field.name] = pa.array([None], field.type)
    return pa.table(row, schema=customers.schema)


def home_block_for_customers(rng, bmeta, ccodes, country_idx, n_blocks, ring_id=None):
    """Give every customer a home network IN THEIR OWN COUNTRY.

    Drawing the home block uniformly at random -- the first version -- put most
    people's everyday address in a foreign country and a tenth of them in a
    datacentre. Every geographic figure in the warehouse was then wrong in the same
    direction: 15 % of payments looked cross-border by IP against a declared travel
    rate of 3.7 %, and `connection_kind = 'datacenter'` sat at 7.4 % against a
    declared 0.9 %. Both are numbers a fraud analyst reads first.
    """
    home_kind = np.isin(bmeta["kind"], ["residential", "mobile"])
    out = np.empty(country_idx.size, dtype=np.int64)
    for i, code in enumerate(ccodes):
        pool = np.flatnonzero((bmeta["country"] == code) & home_kind)
        if pool.size == 0:
            pool = np.flatnonzero(bmeta["country"] == code)
        if pool.size == 0:
            raise AssertionError(f"country {code} has no IP block to live in")
        m = country_idx == i
        k = int(m.sum())
        if k:
            out[m] = rng.choice(pool, k)

    # A ring operates from one place. Members already share devices; pinning them to
    # a single network as well is what makes "same device AND same /24" a test that
    # separates a fraud ring from a family, instead of a coincidence that separates
    # nothing.
    if ring_id is not None:
        ring_of_customer = ring_id[1:]
        for r in np.unique(ring_of_customer[ring_of_customer > 0]):
            members = np.flatnonzero(ring_of_customer == r)
            out[members] = out[members[0]]
    return out


def patch_note_last4(
    customers: pa.Table, pan_last4: np.ndarray, card_start: np.ndarray
) -> pa.Table:
    """Replace the `0000` placeholder in support notes with the owner's real PAN suffix."""
    notes = customers.column("support_note").to_pylist()
    primary = pan_last4[card_start]
    out = [
        n
        if n is None or "card ending 0000" not in n
        else n.replace("card ending 0000", f"card ending {primary[i]}")
        for i, n in enumerate(notes)
    ]
    idx = customers.schema.get_field_index("support_note")
    return customers.set_column(idx, "support_note", pa.array(out, pa.string()))


def log(msg: str, t0: float) -> None:
    print(f"[{time.time() - t0:7.1f}s] {msg}", flush=True)


def write_table(tbl: pa.Table, path: pathlib.Path, row_group: int) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        tbl,
        path,
        compression="zstd",
        compression_level=7,
        row_group_size=row_group,
        use_dictionary=True,
        write_statistics=True,
        version="2.6",
    )
    return path.stat().st_size


def build(profile_name: str, out_dir: pathlib.Path, seed: int) -> dict:
    t0 = time.time()
    p = config.PROFILES[profile_name]
    out = out_dir / profile_name
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    rng = np.random.default_rng(seed)
    manifest: dict = {
        "profile": profile_name,
        "seed": seed,
        "start_date": str(config.START_DATE),
        "end_date": str(config.END_DATE),
        "tables": {},
        "shape": {},
    }

    def emit(name: str, tbl: pa.Table, sub: str | None = None) -> None:
        rel = f"{name}/{sub}" if sub else f"{name}/part-0000.parquet"
        n = write_table(tbl, out / rel, p.row_group_size)
        e = manifest["tables"].setdefault(
            name, {"rows": 0, "bytes": 0, "columns": tbl.num_columns}
        )
        e["rows"] += tbl.num_rows
        e["bytes"] += n

    # ---------------- reference ------------------------------------------------
    log("reference tables", t0)
    city = refs.ref_city()
    for name, tbl in [
        ("dim_date", refs.dim_date()),
        ("ref_country", refs.ref_country()),
        ("ref_city", city),
        ("ref_mcc", refs.ref_mcc()),
        ("ref_decline_reason", refs.ref_decline_reason()),
        ("ref_currency", refs.ref_currency()),
    ]:
        emit(name, tbl)
    # ONE fx table, emitted and then reused to convert the facts. Building a second
    # one from a different seed -- which the first version did -- left the published
    # reference and the rate actually applied as two independent random walks, so
    # `amount_eur_minor` could not be reconciled from `ref_fx_rate_daily` at all
    # (up to 29 % apart). The published rate has to be the rate that was used.
    fx_table = refs.ref_fx_rate_daily(rng)
    emit("ref_fx_rate_daily", fx_table)

    # ---------------- organisation ---------------------------------------------
    log("corporate groups, merchants, sites, employees", t0)
    groups = dims_org.dim_corporate_group(rng, p.groups)
    emit("dim_corporate_group", groups)

    m_weights, m_shape = shape.merchant_weights(p.merchants)
    manifest["shape"]["merchant_concentration"] = m_shape
    merchants, mmeta = dims_org.dim_merchant(rng, p.merchants, p.groups, m_weights)
    manifest["shape"]["category_volume"] = {
        "target": mmeta["category_volume_target"],
        "measured_on_merchant_weights": mmeta["category_volume_measured"],
    }
    emit("dim_merchant", merchants)

    sites, _site_owner, site_country, site_counts, site_first = dims_org.dim_merchant_site(
        rng, p.merchants, mmeta["country_by_natural"], m_weights
    )
    emit("dim_merchant_site", sites)

    name_pools = pools.name_pools(seed)
    employees = dims_org.dim_employee(rng, p.employees, name_pools)
    emit("dim_employee", employees)
    emit(
        "bridge_merchant_account_manager",
        dims_org.bridge_merchant_account_manager(rng, p.merchants, employees),
    )

    # ---------------- payment assignment, BEFORE the customers ------------------
    #
    # The day-by-day assignment of payments to customers is computed here, ahead of
    # the customer dimension, for one reason: a sign-up date has to precede the
    # first payment, and that is only knowable once the assignment exists. Building
    # the dimension first and dating it independently gave 19.3 % of paying
    # customers a first payment before they had an account.
    log("assigning payments to days and customers", t0)
    dates = [config.START_DATE + dt.timedelta(days=i) for i in range(config.N_DAYS)]
    dweights = shape.day_weights(np.array(dates, dtype=object))
    per_day = rng.multinomial(p.intents, dweights)

    counts, cust_stats = shape.customer_payment_counts(rng, p.customers, int(per_day.sum()))
    manifest["shape"]["customer_payments"] = cust_stats
    cust_pool = np.repeat(np.arange(1, p.customers + 1, dtype=np.int32), counts)
    rng.shuffle(cust_pool)
    cust_off = np.concatenate([[0], np.cumsum(per_day)])[:-1]
    intent_off = cust_off.copy()
    first_payment_day = first_day_per_customer(cust_pool, counts, per_day, p.customers)

    # ---------------- people ----------------------------------------------------
    log(f"customers ({p.customers:,})", t0)
    customers, traits = dims_people.dim_customer(
        rng, p.customers, name_pools, city, first_payment_day
    )
    cards, cmeta = dims_people.dim_card(rng, p.customers, traits, first_payment_day)
    emit("dim_card", cards)
    # The support note quotes the customer's own card suffix, which only exists once
    # the cards do. Everything else the note leaks -- name, phone, national id,
    # email, date of birth -- already comes from the customer's own row; the last
    # four digits were the one placeholder left, and a placeholder in the flagship
    # leak is the difference between a demonstration and a claim.
    customers = patch_note_last4(customers, cmeta["pan_last4"], cmeta["card_start"])
    emit("dim_customer", pa.concat_tables([unknown_customer_row(customers), customers]))

    log("devices, ip blocks, products", t0)
    # 12 % headroom over the expected link count: the per-customer draw is random and
    # the links are EXCLUSIVE, so the inventory has to cover the upper tail.
    n_dev = max(64, int(p.customers * p.devices_per_customer * 1.12))
    devices, _ = dims_digital.dim_device(rng, n_dev)
    emit("dim_device", devices)
    bridge_dev, dmeta = dims_digital.bridge_customer_device(
        rng, p.customers, n_dev, p.devices_per_customer
    )
    emit("bridge_customer_device", bridge_dev)

    n_blocks = max(96, p.customers // 220)
    ipb, bmeta = dims_digital.dim_ip_block(rng, n_blocks, city)
    emit("dim_ip_block", ipb)

    pw, p_shape = shape.product_weights(p.products)
    manifest["shape"]["product_concentration"] = p_shape
    products, pmeta = dims_digital.dim_product(rng, p.products, pw)
    emit("dim_product", products)
    # The weights the catalogue actually carries, after shuffling, so the fact loop
    # samples the same popularity the dimension publishes.
    prod_weight_used = products.column("popularity_weight").to_numpy(zero_copy_only=False)
    prod_restricted = products.column("is_age_restricted").to_numpy(zero_copy_only=False)

    # ---------------- context for the fact loop ---------------------------------
    log("building fact context", t0)

    # Ring members' category affinity is overwritten toward where money leaves
    # fastest. Declared here rather than inside the customer dimension because it
    # depends on the device bridge, which is built later.
    ring_members = np.flatnonzero(dmeta["ring_id"][1:] > 0)
    if ring_members.size:
        hot = [
            dims_people.CAT_INDEX[c]
            for c in ("GAMBLING", "CRYPTO", "FINANCIAL", "ELECTRONICS", "GAMING")
        ]
        traits["affinity_cat"][ring_members] = rng.choice(
            hot, ring_members.size, p=[0.30, 0.22, 0.16, 0.20, 0.12]
        )
        traits["affinity_strength"][ring_members] = 0.93
        traits["risk_propensity"][ring_members] = np.clip(
            traits["risk_propensity"][ring_members] + 0.35, 0, 1
        )
    manifest["shape"]["fraud_rings"] = {
        "rings": int(dmeta["ring_id"].max()),
        "members": int(ring_members.size),
        "share_of_customers": float(ring_members.size / p.customers),
    }

    # Categories in which every SKU carries an age limit: a minor cannot be routed
    # to a merchant there at all.
    gated_names = {"ALCOHOL", "TOBACCO", "GAMBLING"}
    age_gated = np.array(
        [dims_people.CAT_INDEX[c] for c in gated_names if c in dims_people.CAT_INDEX]
    )

    regions = sorted({c[2] for c in pools.COUNTRIES})
    region_of_country = np.array([regions.index(c[2]) for c in pools.COUNTRIES])
    mi = facts.MerchantIndex(
        mmeta["mcc_by_natural"],
        m_weights,
        mmeta["country_by_natural"],
        region_of_country[mmeta["country_by_natural"]],
    )
    mi_cat_index = np.array(
        [dims_people.CAT_INDEX[c] for c in mi.cats_of_merchant], dtype=np.int8
    )
    open_mask = ~np.isin(mi.present, age_gated)
    open_cats = mi.present[open_mask]
    open_p = mi.cat_p[open_mask] / mi.cat_p[open_mask].sum()
    pref_merchant = mi.sample(rng, traits["affinity_cat"].astype(np.int8))

    scd2 = facts.scd2_lookup(
        np.array(merchants.column("valid_from").to_pylist(), dtype="datetime64[D]"),
        np.array(merchants.column("valid_to").to_pylist(), dtype="datetime64[D]"),
        mmeta["natural_index"],
        p.merchants,
        dates,
    )
    mdr_by_sk = np.concatenate([[0], merchants.column("mdr_bps").to_numpy()])
    merchant_mdr = mdr_by_sk[scd2]

    # Sorted (merchant, country) -> site lookup, so the fact loop can find "this
    # merchant's site in the customer's country" with one searchsorted instead of a
    # per-row scan.
    site_keys = np.repeat(np.arange(p.merchants), site_counts).astype(
        np.int64
    ) * 100 + site_country.astype(np.int64)
    key_order = np.argsort(site_keys, kind="stable")
    site_keys_sorted = site_keys[key_order]
    site_idx_sorted = key_order.astype(np.int32)

    mcc_high = {m[0]: m[5] for m in pools.MCC}
    mcc_delay = dict(
        zip([m[0] for m in pools.MCC], [7 if m[5] else 2 for m in pools.MCC], strict=False)
    )
    ccodes = np.array([c[0] for c in pools.COUNTRIES])
    cur_of_country = np.array(
        [config.CURRENCIES.index(c[3]) for c in pools.COUNTRIES], dtype=np.int8
    )

    fx_tbl = fx_table.to_pydict()
    fx_by_day = np.ones((config.N_DAYS, len(config.CURRENCIES)), dtype=np.float64)
    ci_of_cur = {c: i for i, c in enumerate(config.CURRENCIES)}
    # Minor units per currency. The forint has none, so an amount in "minor units" of
    # HUF is whole forints. Ignoring this wrote a 40 EUR Hungarian basket as 1.9
    # million forints.
    minor_units = np.array([0 if c in ("HUF", "JPY") else 2 for c in config.CURRENCIES])
    # Seed the carry-forward with each currency's OPENING rate. Starting at 1.0 and
    # waiting for the first quote left every currency at parity with the euro until
    # its first weekday -- and 2024-09-01 is a Sunday, so day one of the dataset
    # priced a Danish basket as if a krone were a euro.
    last = np.array([refs.OPENING_EUR_RATE[c] for c in config.CURRENCIES])
    quotes: dict[tuple[int, int], float] = {}
    for c, d, r in zip(
        fx_tbl["currency_code"], fx_tbl["rate_date"], fx_tbl["eur_rate"], strict=False
    ):
        quotes[((d - config.START_DATE).days, ci_of_cur[c])] = r
    for di in range(config.N_DAYS):
        for cidx in range(len(config.CURRENCIES)):
            if (di, cidx) in quotes:
                last[cidx] = quotes[(di, cidx)]
        fx_by_day[di] = last

    issuers = sorted({b[2] for b in pools.CARD_BINS})
    issuer_base = (np.random.default_rng(seed + 2).normal(0, 0.28, len(issuers))).astype(
        np.float32
    )
    issuer_of_bin = np.array([issuers.index(b[2]) for b in pools.CARD_BINS])

    reason_soft = np.array([r[4] for r in pools.DECLINE_REASONS])
    reason_lift = np.array([r[5] for r in pools.DECLINE_REASONS], dtype=np.float32)
    reason_code = np.array([r[0] for r in pools.DECLINE_REASONS])
    idx_expired = reason_code.tolist().index("expired_card")
    idx_blocked = reason_code.tolist().index("blocked_by_risk_engine")
    base_reason_p = np.array(
        [
            0.29,
            0.17,
            0.05,
            0.03,
            0.08,
            0.06,
            0.04,
            0.03,
            0.01,
            0.01,
            0.02,
            0.02,
            0.06,
            0.04,
            0.01,
            0.01,
            0.03,
            0.04,
        ]
    )
    base_reason_p = base_reason_p / base_reason_p.sum()

    # `blocked_by_risk_engine` is reserved: it is the reason the engine gives when it
    # blocks, so it cannot be drawn for a payment the engine allowed. Leaving it in
    # the base distribution let 1,506 rows say the engine blocked them while
    # `risk_decision` said `allow` -- a contradiction inside a single row, which is
    # the kind of defect that makes a reviewer stop trusting the rest.
    open_reason_p = base_reason_p.copy()
    open_reason_p[idx_blocked] = 0.0
    open_reason_p = open_reason_p / open_reason_p.sum()

    def reason_sampler(r, k, expired, blocked, rscore):
        out = r.choice(len(reason_code), k, p=open_reason_p)
        out[expired] = idx_expired
        out[blocked] = idx_blocked
        return out

    def guest_mask(r, cust_arr):
        g = r.random(cust_arr.size) < config.GUEST_CHECKOUT_SHARE
        return np.where(g, -1, cust_arr).astype(np.int32)

    ctx = {
        "traits": traits,
        "cust_pool": cust_pool,
        "cust_off": cust_off,
        "intent_off": intent_off,
        "intent_base": 10_000_000_000,
        "ingest_cursor": 900_000_000_000,
        "mi": mi,
        "mi_cat_index": mi_cat_index,
        "pref_merchant": pref_merchant,
        "scd2": scd2,
        "merchant_mdr": merchant_mdr,
        "site_start": site_first,
        "site_count": site_counts,
        "site_keys_sorted": site_keys_sorted,
        "site_idx_sorted": site_idx_sorted,
        "site_country": site_country,
        "site_is_cp": sites.column("is_card_present").to_numpy(zero_copy_only=False),
        "site_supports_3ds": sites.column("supports_3ds").to_numpy(zero_copy_only=False),
        "card_start": cmeta["card_start"],
        "card_count": cmeta["card_count"],
        "card_funding": cmeta["funding"],
        "card_issuer_cc_idx": index_of(ccodes, cmeta["issuer_country"]),
        "card_issuer_idx": issuer_of_bin[cmeta["card_bin_idx"]],
        "card_exp_year": cmeta["exp_year"],
        "card_exp_month": cmeta["exp_month"],
        "dev_flat": dmeta["dev_flat"],
        "dev_start": dmeta["dev_start"],
        "dev_count": dmeta["dev_count"],
        "ring_id": dmeta["ring_id"],
        "home_block": home_block_for_customers(
            rng, bmeta, ccodes, traits["country_idx"], n_blocks, dmeta["ring_id"]
        ),
        "block_start": bmeta["start"],
        "n_blocks": n_blocks,
        "block_risk": ipb.column("risk_weight").to_numpy(zero_copy_only=False),
        "block_country_idx": index_of(ccodes, bmeta["country"]),
        "vpn_blocks": np.flatnonzero(np.isin(bmeta["kind"], ["vpn", "datacenter"])),
        "hour_cum": np.cumsum(shape.hour_weights()),
        "merchant_currency_idx": cur_of_country[mmeta["country_by_natural"]],
        "country_currency_idx": cur_of_country,
        "merchant_country_idx": mmeta["country_by_natural"],
        "mcc_high_risk": np.array([mcc_high[c] for c in mmeta["mcc_by_natural"]]),
        "mcc_settle_delay": np.array([mcc_delay[c] for c in mmeta["mcc_by_natural"]]),
        "merchant_natural_to_batchbase": np.arange(p.merchants) + 1,
        "fx_by_day": fx_by_day,
        "issuer_base": issuer_base,
        "minor_scale": 10.0 ** (minor_units - 2),
        "domestic_share": config.DOMESTIC_MERCHANT_SHARE,
        "risk_block_threshold": config.RISK_BLOCK_THRESHOLD,
        "risk_review_threshold": config.RISK_REVIEW_THRESHOLD,
        "issuer_is_non_eea": np.array([c in config.NON_EEA for c in ccodes]),
        "region_of_country": region_of_country,
        "reason_sampler": reason_sampler,
        "reason_soft": reason_soft,
        "reason_lift": reason_lift,
        "reason_code": reason_code,
        "country_codes": ccodes,
        "prod_price": pmeta["price_minor"],
        "pi": facts.ProductIndex(
            pmeta["cat"], prod_weight_used, pmeta["price_minor"], prod_restricted
        ),
        "prod_restricted": prod_restricted,
        "age_gated_categories": age_gated,
        "open_categories": open_cats,
        "open_category_p": open_p,
        "guest_mask": guest_mask,
    }

    # ---------------- the fact loop ---------------------------------------------
    log(f"facts: {config.N_DAYS} days, {p.intents:,} intents target", t0)
    total_attempts = total_lines = 0
    hits = np.zeros(p.merchants + 1, dtype=np.int64)
    for di, day in enumerate(dates):
        tbl, lines = build_facts.build_day(di, day, int(per_day[di]), rng, ctx)
        if tbl is None:
            continue
        emit("fact_payment_attempt", tbl, f"event_date={day.isoformat()}/part.parquet")
        emit("fact_order_line", lines, f"event_date={day.isoformat()}/part.parquet")
        total_attempts += tbl.num_rows
        total_lines += lines.num_rows
        hits += np.bincount(ctx.pop("day_merchant_hits"), minlength=p.merchants + 1)
        if di % 90 == 0:
            log(
                f"  day {di + 1}/{config.N_DAYS} ({day}) "
                f"attempts={total_attempts:,} lines={total_lines:,}",
                t0,
            )

    # MEASURED concentration, from the traffic actually generated -- not the solver's
    # target. Publishing the target as if it were an outcome is the exact failure
    # this generator claims to prevent, and a reviewer caught it here first: the
    # weight vector said 45.00 % / 80.00 % while the facts said 47.5 % / 81.0 %,
    # because loyalty, affinity and the domestic bias all move traffic afterwards.
    srt = np.sort(hits[1:])[::-1]
    tot = max(1, srt.sum())
    k1, k10 = max(1, p.merchants // 100), max(1, p.merchants // 10)
    mc = manifest["shape"]["merchant_concentration"]
    mc["top_1pct_measured_on_traffic"] = float(srt[:k1].sum() / tot)
    mc["top_10pct_measured_on_traffic"] = float(srt[:k10].sum() / tot)
    mc["top_1pct_traffic_floor"] = config.MERCHANT_TRAFFIC_TOP1PCT_FLOOR
    if mc["top_1pct_measured_on_traffic"] < config.MERCHANT_TRAFFIC_TOP1PCT_FLOOR:
        raise AssertionError(
            f"realised merchant concentration {mc['top_1pct_measured_on_traffic']:.1%} "
            f"is below the declared floor {config.MERCHANT_TRAFFIC_TOP1PCT_FLOOR:.0%}; "
            "without real skew the cost estimator has nothing to be right or wrong about"
        )

    manifest["shape"]["retry_expansion_factor"] = total_attempts / max(1, int(per_day.sum()))
    manifest["totals"] = {
        "attempts": total_attempts,
        "order_lines": total_lines,
        "intents": int(per_day.sum()),
        "bytes": sum(t["bytes"] for t in manifest["tables"].values()),
    }
    log(
        f"done: {total_attempts:,} attempts, {total_lines:,} lines, "
        f"{manifest['totals']['bytes'] / 1e9:.2f} GB",
        t0,
    )

    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, default=str))
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="dev", choices=sorted(config.PROFILES))
    ap.add_argument("--out", default="datagen/out", type=pathlib.Path)
    ap.add_argument("--seed", default=config.MASTER_SEED, type=int)
    a = ap.parse_args()
    m = build(a.profile, a.out, a.seed)
    print(json.dumps(m["totals"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
