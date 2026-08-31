"""Organisational dimensions: the ownership graph, merchants, sites, employees.

The corporate graph is the reason `run_query` needs a recursion limit at all.
A merchant is billed by an operating company; that company may be owned by an
intermediate holding; that holding rolls up to an ultimate parent, sometimes in a
different jurisdiction from every merchant beneath it. Answering "how much money
ended up at group X" is therefore a recursive CTE over an arbitrary depth -- and
`WITH RECURSIVE` with no depth bound is a denial-of-service written in SQL, which
is exactly the class of query a validating guard has to refuse by shape rather
than by keyword.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pyarrow as pa

from . import config, pools

# Jurisdictions that appear as places of incorporation but carry no card traffic.
# The gap between where a merchant trades and where its parent is registered is
# the whole point of following the money.
HOLDING_JURISDICTIONS = [
    ("NL", 0.19),
    ("LU", 0.16),
    ("IE", 0.14),
    ("ES", 0.13),
    ("MT", 0.07),
    ("CY", 0.05),
    ("CH", 0.08),
    ("DE", 0.06),
    ("GB", 0.07),
    ("FR", 0.05),
]
SECTORS = [
    "Retail",
    "Hospitality",
    "Digital Services",
    "Travel",
    "Logistics",
    "Financial Services",
    "Health",
    "Education",
    "Media",
    "Industrial",
]

# Total IBAN length per country, from the ISO 13616 registry. A Spanish IBAN is 24
# characters and a Maltese one is 31; emitting a 24-character "MT" account is the
# kind of detail a treasury system rejects on sight.
IBAN_LENGTH = {
    "ES": 24,
    "FR": 27,
    "DE": 22,
    "IT": 27,
    "PT": 25,
    "GB": 22,
    "NL": 18,
    "BE": 16,
    "IE": 22,
    "PL": 28,
    "SE": 24,
    "DK": 18,
    "NO": 15,
    "FI": 18,
    "AT": 20,
    "CH": 21,
    "CZ": 24,
    "HU": 28,
    "RO": 24,
    "BG": 22,
    "GR": 27,
    "LT": 20,
    "MT": 31,
    "LU": 20,
    "CY": 28,
    "US": 24,
    "MX": 24,
    "BR": 29,
    "MA": 28,
    "TR": 26,
    "SG": 24,
    "JP": 24,
}


def _synthetic_iban(country: str, seed_value: int) -> str:
    """An IBAN with the right country, the right length -- and a WRONG check digit.

    The check digits are the correct mod-97 value shifted by one, deliberately. The
    reasoning is the same as for the national identifier: a synthetic account number
    must not be able to belong to anybody, and the cheapest guarantee of that is
    that it fails the standard's own validation while keeping every other property
    a query might care about.

    The version this replaces put every payout into a Spanish IBAN with bank code
    0000, whatever the beneficiary's country. Half a million payments to British,
    Swiss and American parents all landing in the same Spanish bank contradicted the
    single story the ownership graph exists to tell.
    """
    length = IBAN_LENGTH.get(country, 24)
    bban = f"{seed_value:0{length - 4}d}"[-(length - 4) :]
    rearranged = bban + country + "00"
    numeric = "".join(str(ord(c) - 55) if c.isalpha() else c for c in rearranged)
    correct = 98 - (int(numeric) % 97)
    wrong = (correct % 97) + 1  # never equal to `correct`
    return f"{country}{wrong:02d}{bban}"


def dim_corporate_group(rng: np.random.Generator, n: int) -> pa.Table:
    """A forest of ownership trees, built parent-before-child.

    Depth is assigned first and parents are drawn only from strictly shallower
    groups, so the graph is acyclic by construction rather than by a check
    afterwards. A cycle here would hang every recursive query in the catalogue,
    and "we tested it and it did not hang" is not the same guarantee.
    """
    n_ultimate = max(3, int(n * config.GROUP_ULTIMATE_SHARE))
    depths = np.zeros(n, dtype=np.int8)
    # Depth decays geometrically: most groups sit one or two levels under a root,
    # a few chains run the full five. Both extremes matter -- the shallow ones
    # keep the common query cheap, the deep ones are what the recursion limit is
    # for.
    remaining = n - n_ultimate
    tail = rng.geometric(0.52, size=remaining).astype(np.int8)
    depths[n_ultimate:] = np.clip(tail, 1, config.GROUP_MAX_DEPTH)
    order = np.argsort(depths, kind="stable")
    depths = depths[order]

    parent = np.full(n, -1, dtype=np.int32)
    for d in range(1, config.GROUP_MAX_DEPTH + 1):
        at_depth = np.flatnonzero(depths == d)
        if at_depth.size == 0:
            continue
        candidates = np.flatnonzero(depths < d)
        parent[at_depth] = rng.choice(candidates, size=at_depth.size, replace=True)

    juris = np.array([j[0] for j in HOLDING_JURISDICTIONS])
    jw = np.array([j[1] for j in HOLDING_JURISDICTIONS], dtype=np.float64)
    jw /= jw.sum()
    # An ultimate parent is far more likely to sit in a holding jurisdiction than
    # an operating company is. That skew is the signal a "where does the money
    # land" query is meant to find.
    country = np.empty(n, dtype=object)
    is_root = depths == 0
    country[is_root] = rng.choice(juris, size=int(is_root.sum()), p=jw)
    trade_w = np.array([c[6] for c in pools.COUNTRIES], dtype=np.float64)
    trade_w /= trade_w.sum()
    trade_cc = np.array([c[0] for c in pools.COUNTRIES])
    country[~is_root] = rng.choice(trade_cc, size=int((~is_root).sum()), p=trade_w)

    pre = rng.integers(0, len(pools.GROUP_PREFIX), n)
    root = rng.integers(0, len(pools.GROUP_ROOT), n)
    suf = rng.integers(0, len(pools.GROUP_SUFFIX), n)
    names = [
        f"{pools.GROUP_PREFIX[p]} {pools.GROUP_ROOT[r]} {pools.GROUP_SUFFIX[s]}"
        for p, r, s in zip(pre, root, suf, strict=False)
    ]
    # Names collide by construction (the pools are small on purpose): two distinct
    # legal entities with the same trading name is a real and painful thing, and a
    # query that groups by name instead of by key gets it wrong.
    inc_days = rng.integers(-14_600, -400, n)
    epoch = dt.date(2026, 1, 1)

    return pa.table(
        {
            "group_sk": pa.array(np.arange(1, n + 1), pa.int32()),
            "group_id": pa.array([f"GRP-{i:06d}" for i in range(1, n + 1)]),
            "group_name": pa.array(names),
            "parent_group_sk": pa.array(
                np.where(parent < 0, -1, parent + 1).astype(np.int32), pa.int32()
            ),
            "depth": pa.array(depths, pa.int8()),
            "is_ultimate_parent": pa.array(is_root, pa.bool_()),
            "incorporation_country": pa.array(country.astype(str)).dictionary_encode(),
            "sector": pa.array(rng.choice(SECTORS, n)).dictionary_encode(),
            "vat_number": pa.array(
                [
                    f"{c}{n_:09d}"
                    for c, n_ in zip(
                        country.astype(str), rng.integers(10**8, 10**9 - 1, n), strict=False
                    )
                ]
            ),
            "incorporated_on": pa.array(
                [epoch + dt.timedelta(days=int(d)) for d in inc_days], pa.date32()
            ),
            "employee_count_declared": pa.array(
                np.round(rng.lognormal(3.4, 1.5, n)).astype(np.int32), pa.int32()
            ),
            "payout_iban": pa.array(
                [
                    _synthetic_iban(c, int(v))
                    for c, v in zip(
                        country.astype(str), rng.integers(10**9, 10**12, n), strict=False
                    )
                ]
            ),
        }
    )


def _assign_mcc_by_volume(
    rng: np.random.Generator, weights: np.ndarray, mcc_arr: np.ndarray, mcc_cat: dict
) -> np.ndarray:
    """Give each merchant an MCC so that VOLUME per category hits its declared share.

    Merchants arrive sorted by traffic weight (rank 1 is the biggest). Walking them
    from largest to smallest and handing each to whichever category is furthest
    below its target closes the gap greedily; the biggest merchants land in the
    biggest sectors, which is both what the targets ask for and what is true.
    Within a category the specific MCC is then drawn at random, because the
    difference between 5411 and 5300 is not something anyone declares.
    """
    cats = list(config.CATEGORY_VOLUME_SHARE)
    target = np.array([config.CATEGORY_VOLUME_SHARE[c] for c in cats])
    target = target / target.sum()
    mcc_by_cat = {c: mcc_arr[[mcc_cat[m] == c for m in mcc_arr]] for c in cats}
    assigned = np.zeros(len(cats))
    out = np.full(weights.size, -1, dtype=mcc_arr.dtype)
    order = np.argsort(-weights)

    # FLOOR FIRST: every category gets one merchant before the greedy pass starts.
    # Without it, CRYPTO at 0.2 % of volume gets zero merchants in a 380-merchant
    # profile, and a customer whose affinity points there has nowhere to shop --
    # which surfaced as a KeyError deep in the day loop rather than as a warning.
    # The floor is taken from the SMALLEST merchants so it barely moves the
    # volume shares it is protecting.
    if weights.size < len(cats):
        raise ValueError(
            f"{weights.size} merchants cannot cover {len(cats)} "
            "categories with at least one each"
        )
    for slot, i in enumerate(order[::-1][: len(cats)]):
        pool = mcc_by_cat[cats[slot]]
        out[i] = pool[rng.integers(len(pool))]
        assigned[slot] += weights[i]

    running = float(assigned.sum())
    for i in order:
        if out[i] != -1:
            continue
        w = weights[i]
        running += w
        deficit = target * running - assigned
        c = int(np.argmax(deficit))
        assigned[c] += w
        pool = mcc_by_cat[cats[c]]
        out[i] = pool[rng.integers(len(pool))]
    return out


def dim_merchant(
    rng: np.random.Generator, n: int, n_groups: int, weights: np.ndarray
) -> tuple[pa.Table, dict]:
    """Merchants as a type-2 slowly changing dimension.

    Every merchant has one or more versions with `valid_from` / `valid_to` and
    exactly one `is_current`. What makes it a real SCD2 rather than a decorated
    one: `merchant_sk` is the SURROGATE key and changes with each version, while
    `merchant_id` is the natural key and does not. A query that joins facts on
    `merchant_id` instead of `merchant_sk` silently multiplies its rows by the
    version count and rewrites history -- and it looks completely reasonable.
    """
    mcc_arr = np.array([m[0] for m in pools.MCC])
    mcc_cat = {m[0]: m[2] for m in pools.MCC}
    mcc_high = {m[0]: m[5] for m in pools.MCC}

    # Bigger merchants change more often: repricing, risk re-tiering, a new IBAN
    # after a refinancing. Version count is drawn from traffic weight, so the SCD2
    # trap concentrates exactly where the volume is.
    rel = weights / weights.max()
    p_multi = 0.10 + 0.55 * rel
    versions = 1 + rng.binomial(3, p_multi)

    natural_idx = np.repeat(np.arange(n), versions)
    total = int(versions.sum())
    ver_no = np.concatenate([np.arange(1, v + 1) for v in versions])

    country_w = np.array([c[6] for c in pools.COUNTRIES], dtype=np.float64)
    country_w /= country_w.sum()
    m_country = rng.choice(len(pools.COUNTRIES), n, p=country_w)
    m_mcc = _assign_mcc_by_volume(rng, weights, mcc_arr, mcc_cat)
    m_group = rng.integers(1, n_groups + 1, n, dtype=np.int32)

    pre = rng.integers(0, len(pools.MERCHANT_PREFIX), n)
    rt = rng.integers(0, len(pools.MERCHANT_ROOT), n)
    trade_names = [
        f"{pools.MERCHANT_PREFIX[p]} {pools.MERCHANT_ROOT[r]}"
        for p, r in zip(pre, rt, strict=False)
    ]

    # Version-level attributes: these are what actually change over time.
    risk_tier = rng.choice(
        ["low", "standard", "elevated", "high"], total, p=[0.31, 0.47, 0.16, 0.06]
    )
    mdr_bps = np.round(rng.normal(185, 46, total)).astype(np.int16)
    mdr_bps = np.clip(mdr_bps, 65, 480)
    high = np.array([mcc_high[c] for c in m_mcc])[natural_idx]
    mdr_bps = np.where(high, mdr_bps + 120, mdr_bps).astype(np.int16)

    # Validity windows. Version 1 starts before the fact window so that every
    # payment has a matching version -- an SCD2 with a hole is a bug, not a trap.
    start_base = config.START_DATE - dt.timedelta(days=400)
    valid_from, valid_to, is_current = [], [], []
    cursor = 0
    for v in versions:
        cuts = (
            sorted(rng.choice(np.arange(30, config.N_DAYS + 300), size=v - 1, replace=False))
            if v > 1
            else []
        )
        bounds = [start_base] + [start_base + dt.timedelta(days=int(c)) for c in cuts]
        for k in range(v):
            valid_from.append(bounds[k])
            if k == v - 1:
                valid_to.append(dt.date(9999, 12, 31))
                is_current.append(True)
            else:
                valid_to.append(bounds[k + 1] - dt.timedelta(days=1))
                is_current.append(False)
        cursor += v

    tbl = pa.table(
        {
            "merchant_sk": pa.array(np.arange(1, total + 1), pa.int32()),
            "merchant_id": pa.array([f"MER-{i + 1:06d}" for i in natural_idx]),
            "version_no": pa.array(ver_no, pa.int8()),
            "valid_from": pa.array(valid_from, pa.date32()),
            "valid_to": pa.array(valid_to, pa.date32()),
            "is_current": pa.array(is_current, pa.bool_()),
            "trade_name": pa.array([trade_names[i] for i in natural_idx]),
            "legal_name": pa.array([f"{trade_names[i]} SL" for i in natural_idx]),
            "group_sk": pa.array(m_group[natural_idx], pa.int32()),
            "mcc": pa.array(m_mcc[natural_idx].astype(np.int16), pa.int16()),
            "category": pa.array([mcc_cat[c] for c in m_mcc[natural_idx]]).dictionary_encode(),
            "country_code": pa.array(
                [pools.COUNTRIES[i][0] for i in m_country[natural_idx]]
            ).dictionary_encode(),
            "risk_tier": pa.array(risk_tier).dictionary_encode(),
            "mdr_bps": pa.array(mdr_bps, pa.int16()),
            "onboarded_on": pa.array(
                [start_base - dt.timedelta(days=int(d)) for d in rng.integers(1, 2200, total)],
                pa.date32(),
            ),
            "is_active": pa.array(rng.random(total) > 0.037, pa.bool_()),
            "settlement_currency": pa.array(
                [pools.COUNTRIES[i][3] for i in m_country[natural_idx]]
            ).dictionary_encode(),
            "traffic_weight": pa.array(weights[natural_idx], pa.float64()),
        }
    )
    cat_of_mcc = {m[0]: m[2] for m in pools.MCC}
    measured_share = {}
    for c in set(cat_of_mcc.values()):
        sel = np.array([cat_of_mcc[m] == c for m in m_mcc])
        measured_share[c] = float(weights[sel].sum())
    meta = {
        "category_volume_measured": dict(sorted(measured_share.items(), key=lambda kv: -kv[1])),
        "category_volume_target": config.CATEGORY_VOLUME_SHARE,
        "natural_merchants": n,
        "versions_total": total,
        "avg_versions": float(versions.mean()),
        "pct_with_multiple_versions": float((versions > 1).mean()),
        "natural_index": natural_idx,
        "current_sk_by_natural": np.array(
            [i + 1 for i, cur in enumerate(is_current) if cur], dtype=np.int32
        ),
        "mcc_by_natural": m_mcc,
        "country_by_natural": m_country,
        "group_by_natural": m_group,
    }
    return tbl, meta


def dim_merchant_site(
    rng: np.random.Generator, n_merchants: int, home_country: np.ndarray, weights: np.ndarray
) -> tuple:
    """Where the payment was taken. One merchant, several channels AND SEVERAL
    COUNTRIES.

    The site carries its own country, and that is the fix for the single worst
    number the reviewers found. Modelling a merchant as belonging to exactly one
    country made half of all payments cross-border, because a customer in a small
    market has almost no domestic merchant to shop at -- and `is_cross_border`
    feeds approval, interchange and risk simultaneously.

    Reality is that a large merchant has a local acquiring entity in every market it
    sells into, and a corner shop has one. Site count therefore scales with traffic
    weight: the biggest merchants reach a dozen countries, the long tail stays home.
    """
    rel = weights / weights.max()
    counts = 1 + rng.poisson(1.2 + 9.0 * rel)
    counts = np.clip(counts, 1, 14)
    total = int(counts.sum())
    owner = np.repeat(np.arange(1, n_merchants + 1), counts)

    cw = np.array([c[6] for c in pools.COUNTRIES], dtype=np.float64)
    cw /= cw.sum()
    site_country = rng.choice(len(pools.COUNTRIES), total, p=cw)
    # The FIRST site of every merchant is in its own country of incorporation.
    first_of_merchant = np.concatenate([[0], np.cumsum(counts)[:-1]])
    site_country[first_of_merchant] = home_country

    kind = rng.choice(
        pools.SITE_KINDS, total, p=[0.34, 0.13, 0.13, 0.08, 0.16, 0.04, 0.03, 0.09]
    )
    tbl = pa.table(
        {
            "site_sk": pa.array(np.arange(1, total + 1), pa.int32()),
            "merchant_natural_id": pa.array([f"MER-{i:06d}" for i in owner]),
            "country_code": pa.array(
                [pools.COUNTRIES[i][0] for i in site_country]
            ).dictionary_encode(),
            "site_kind": pa.array(kind).dictionary_encode(),
            "site_label": pa.array(
                [f"{k}-{i:07d}" for k, i in zip(kind, range(1, total + 1), strict=False)]
            ),
            "is_card_present": pa.array(np.isin(kind, ["pos_terminal", "kiosk"]), pa.bool_()),
            "supports_3ds": pa.array(
                ~np.isin(kind, ["pos_terminal", "kiosk", "call_center"]), pa.bool_()
            ),
            "opened_on": pa.array(
                [
                    config.START_DATE - dt.timedelta(days=int(d))
                    for d in rng.integers(30, 2000, total)
                ],
                pa.date32(),
            ),
        }
    )
    return tbl, owner, site_country, counts, first_of_merchant


def dim_employee(rng: np.random.Generator, n: int, name_pools: dict) -> pa.Table:
    """CIERZO's own staff, as a self-referencing hierarchy.

    The second recursive query in the catalogue, and the dangerous one: it reaches
    personal data and a salary band. "Show me everyone under Nuria" is a perfectly
    normal request that, written as an unbounded recursive CTE joined to
    `salary_band`, walks the whole payroll.
    """
    np.array([t[1] for t in pools.JOB_TITLES])
    [t[0] for t in pools.JOB_TITLES]
    # Level 0 is one person; every level below is wider than the one above.
    weights = np.array([1, 3, 3, 6, 8, 79], dtype=np.float64)
    lvl = rng.choice(np.arange(1, 6), n, p=(weights[1:] / weights[1:].sum()))
    lvl = np.sort(lvl)
    # EXACTLY ONE chief executive, and exactly one root. The previous version drew
    # level 0 from the same multinomial as everyone else and got four CEOs, four
    # roots, six chief risk officers and an org chart nobody would sign off.
    lvl[0] = 0

    manager = np.full(n, -1, dtype=np.int32)
    for d in range(1, 6):
        at = np.flatnonzero(lvl == d)
        above = np.flatnonzero(lvl < d)
        if at.size and above.size:
            manager[at] = rng.choice(above, size=at.size, replace=True) + 1

    title_by_level: dict[int, list[str]] = {}
    for t, l in pools.JOB_TITLES:
        title_by_level.setdefault(l, []).append(t)
    title = [rng.choice(title_by_level[int(l)]) for l in lvl]
    # The one C-suite title that cannot be held twice.
    title[0] = "Chief Executive Officer"
    for i in range(1, n):
        if title[i] == "Chief Executive Officer":
            title[i] = rng.choice(
                [t for t in title_by_level[int(lvl[i])] if t != "Chief Executive Officer"]
            )

    firsts, lasts = name_pools["ES_first"], name_pools["ES_last"]
    fi = rng.integers(0, len(firsts), n)
    li = rng.integers(0, len(lasts), n)
    salary_band = np.array(["E1", "E2", "E3", "E4", "E5", "E6"])[5 - lvl]

    return pa.table(
        {
            "employee_sk": pa.array(np.arange(1, n + 1), pa.int32()),
            "employee_id": pa.array([f"EMP-{i:05d}" for i in range(1, n + 1)]),
            "first_name": pa.array([firsts[i] for i in fi]).dictionary_encode(),
            "last_name": pa.array([lasts[i] for i in li]).dictionary_encode(),
            "job_title": pa.array(title).dictionary_encode(),
            "org_level": pa.array(lvl.astype(np.int8), pa.int8()),
            "manager_employee_sk": pa.array(manager, pa.int32()),
            "department": pa.array(
                rng.choice(
                    ["Operations", "Risk", "Finance", "Sales", "Engineering", "Compliance"],
                    n,
                    p=[0.26, 0.14, 0.15, 0.19, 0.18, 0.08],
                )
            ).dictionary_encode(),
            "work_email": pa.array(
                [
                    f"{firsts[a]}.{lasts[b]}@cierzo.example".lower().replace(" ", "")
                    for a, b in zip(fi, li, strict=False)
                ]
            ),
            "personal_phone": pa.array([f"+346{d:08d}" for d in rng.integers(0, 10**8, n)]),
            "salary_band": pa.array(salary_band).dictionary_encode(),
            "hired_on": pa.array(
                [
                    dt.date(2026, 1, 1) - dt.timedelta(days=int(d))
                    for d in rng.integers(60, 4000, n)
                ],
                pa.date32(),
            ),
            "is_active": pa.array(rng.random(n) > 0.08, pa.bool_()),
        }
    )


def bridge_merchant_account_manager(
    rng: np.random.Generator, n_merchants: int, employees: pa.Table
) -> pa.Table:
    """Many-to-many WITH WEIGHT AND VALIDITY, which is what separates a real bridge
    from a tutorial one.

    A large merchant is covered by two or three managers at once, each owning a
    declared share of the account. Summing revenue through this bridge without
    applying `allocation_pct` counts the same money two or three times -- and the
    total still looks plausible, which is why it is the trap that survives review.
    """
    lvl = employees.column("org_level").to_numpy(zero_copy_only=False)
    active = employees.column("is_active").to_numpy(zero_copy_only=False)
    pool = np.flatnonzero((lvl >= 4) & active) + 1
    counts = 1 + rng.binomial(2, 0.28, n_merchants)
    total = int(counts.sum())
    merch = np.repeat(np.arange(1, n_merchants + 1), counts)

    alloc = np.empty(total, dtype=np.float64)
    cursor = 0
    for c in counts:
        raw = rng.dirichlet(np.full(c, 2.4))
        alloc[cursor : cursor + c] = np.round(raw, 4)
        # Force the allocation to close at exactly 1.0 so the test that asserts it
        # is testing the data, not floating point.
        alloc[cursor + c - 1] = round(1.0 - alloc[cursor : cursor + c - 1].sum(), 4)
        cursor += c

    assigned = rng.choice(pool, total).astype(np.int32)
    hired = np.array(employees.column("hired_on").to_pylist())[assigned - 1]

    # An assignment starts AFTER the manager was hired. 955 of them used to begin
    # before their owner joined the company, which is the same class of defect as a
    # payment before the customer signed up -- invisible to every foreign key and
    # fatal to anything that looks at tenure.
    offset = rng.integers(0, 700, total)
    valid_from = [
        max(
            config.START_DATE - dt.timedelta(days=int(d)),
            h + dt.timedelta(days=int(rng.integers(7, 90))),
        )
        for d, h in zip(offset, hired, strict=False)
    ]
    # And a book of business is reassigned. Leaving every row open forever meant the
    # dimension had no history at all: one distinct `valid_to` across the table.
    ends = rng.random(total) < 0.19
    valid_to = [
        (vf + dt.timedelta(days=int(rng.integers(120, 700)))) if e else dt.date(9999, 12, 31)
        for vf, e in zip(valid_from, ends, strict=False)
    ]

    return pa.table(
        {
            "merchant_natural_id": pa.array([f"MER-{i:06d}" for i in merch]),
            "employee_sk": pa.array(assigned, pa.int32()),
            "allocation_pct": pa.array(alloc, pa.float64()),
            "role": pa.array(
                rng.choice(["primary", "secondary", "technical"], total, p=[0.62, 0.26, 0.12])
            ).dictionary_encode(),
            "valid_from": pa.array(valid_from, pa.date32()),
            "valid_to": pa.array(valid_to, pa.date32()),
            "is_current": pa.array([not e for e in ends], pa.bool_()),
        }
    )
