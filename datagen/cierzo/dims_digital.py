"""Devices, network origin and the product catalogue.

These three tables carry the re-identification thesis of the whole project. A
name can be masked and a card can be tokenised, but a device fingerprint seen
from the same /24 at the same hour every Tuesday IS an identity, and it is one
that no output-masking scheme touches -- which is the argument the guard exists to
make.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pyarrow as pa

from . import config, pools

SCREEN_SIZES = {
    "mobile": ["390x844", "393x852", "412x915", "360x800", "430x932", "375x667"],
    "tablet": ["820x1180", "1024x1366", "768x1024"],
    "desktop": ["1920x1080", "2560x1440", "1440x900", "1536x864", "3440x1440",
                "1280x720", "3840x2160"],
}


def dim_ip_block(rng: np.random.Generator, n_blocks: int,
                 city_table: pa.Table) -> tuple[pa.Table, dict]:
    """Geolocated /24 ranges, stored as UNSIGNED INTEGERS.

    Two decisions worth defending. First, the address is an integer, not a string:
    at 68 M rows a dotted-quad text column costs about 900 MB more than a uint32
    and cannot be range-joined without parsing. Second, the geo lookup is a RANGE
    join (`ip BETWEEN start AND end`) rather than an equality: it is the single
    most expensive join in the catalogue, which makes it the honest test of a cost
    estimator that claims to predict bytes scanned before executing.
    """
    city_ids = city_table.column("city_id").to_numpy()
    city_cc = np.array(city_table.column("country_code").to_pylist())
    city_lat = city_table.column("lat").to_numpy()
    city_lon = city_table.column("lon").to_numpy()

    cw = np.array([c[6] for c in pools.COUNTRIES], dtype=np.float64)
    cw /= cw.sum()
    cc_by_idx = np.array([c[0] for c in pools.COUNTRIES])
    block_country = cc_by_idx[rng.choice(len(pools.COUNTRIES), n_blocks, p=cw)]

    # Pick a city inside the block's country.
    city_choice = np.empty(n_blocks, dtype=np.int64)
    for code in np.unique(block_country):
        pool = np.flatnonzero(city_cc == code)
        m = block_country == code
        city_choice[m] = rng.choice(pool, int(m.sum()))

    # FLOOR: every country gets at least two blocks, one of them residential.
    # Without it a small country has no home network and its customers borrow an
    # address abroad, which is how the first build ended up reporting that 15 % of
    # all payments were cross-border by IP -- a number that would have quietly
    # invalidated every geo and risk figure downstream.
    forced = []
    for c in pools.COUNTRIES:
        forced.extend([c[0], c[0]])
    if len(forced) <= n_blocks:
        block_country[:len(forced)] = np.array(forced)

    kinds = np.array([a[2] for a in pools.ASN_POOL])
    kind_w = np.where(kinds == "residential", 0.62,
             np.where(kinds == "mobile", 0.22,
             np.where(kinds == "datacenter", 0.11, 0.05)))
    kind_w = kind_w / kind_w.sum()
    asn_idx = rng.choice(len(pools.ASN_POOL), n_blocks, p=kind_w)
    # The first block of each forced pair is pinned residential, so every country
    # has somewhere for its own residents to connect from.
    residential = np.flatnonzero(kinds == "residential")
    n_forced = min(len(pools.COUNTRIES) * 2, n_blocks)
    asn_idx[0:n_forced:2] = rng.choice(residential, len(range(0, n_forced, 2)))
    block_kind = kinds[asn_idx]

    # Non-overlapping /24s carved out of 10.0.0.0/8 upward. Deliberately private
    # ranges: a synthetic dataset must never carry an address that resolves to a
    # real host, and "it is only test data" is not a defence when someone runs a
    # port scan against the top offender in your fraud report.
    start = (10 << 24) + np.arange(n_blocks, dtype=np.int64) * 256
    jitter = rng.normal(0, 0.35, n_blocks)

    return pa.table({
        "ip_block_sk": pa.array(np.arange(1, n_blocks + 1), pa.int32()),
        "ip_start": pa.array(start.astype(np.uint32), pa.uint32()),
        "ip_end": pa.array((start + 255).astype(np.uint32), pa.uint32()),
        "cidr": pa.array([f"10.{(s >> 16) & 255}.{(s >> 8) & 255}.0/24" for s in start]),
        "country_code": pa.array(block_country).dictionary_encode(),
        "city_id": pa.array(city_ids[city_choice].astype(np.int16), pa.int16()),
        "latitude": pa.array((city_lat[city_choice] + jitter * 0.18).astype(np.float32),
                             pa.float32()),
        "longitude": pa.array((city_lon[city_choice] + jitter * 0.24).astype(np.float32),
                              pa.float32()),
        "asn": pa.array([pools.ASN_POOL[i][0] for i in asn_idx], pa.int32()),
        "isp_name": pa.array([pools.ASN_POOL[i][1] for i in asn_idx]).dictionary_encode(),
        "connection_kind": pa.array(block_kind).dictionary_encode(),
        "is_anonymizer": pa.array(np.isin(block_kind, ["vpn", "datacenter"]), pa.bool_()),
        "risk_weight": pa.array(
            np.where(block_kind == "vpn", 0.72,
            np.where(block_kind == "datacenter", 0.61,
            np.where(block_kind == "mobile", 0.14, 0.06))).astype(np.float32), pa.float32()),
    }), {"start": start, "country": block_country, "kind": block_kind}


def dim_device(rng: np.random.Generator, n_devices: int) -> tuple[pa.Table, dict]:
    """One row per physical device seen, identified only by its fingerprint."""
    fam_w = np.array([0.27, 0.05, 0.34, 0.04, 0.17, 0.11, 0.02])
    fam_w /= fam_w.sum()
    fam = rng.choice(len(pools.DEVICE_MODELS), n_devices, p=fam_w)

    os_family = np.array([pools.DEVICE_MODELS[i][0] for i in range(len(pools.DEVICE_MODELS))])[fam]
    dev_class = np.array([pools.DEVICE_MODELS[i][2] for i in range(len(pools.DEVICE_MODELS))])[fam]
    # Draw per FAMILY, not per row. The obvious comprehension is one Python-level
    # `rng.integers` call per device -- nineteen million of them at full scale,
    # and the single slowest thing in the whole generator before this changed.
    os_version = np.empty(n_devices, dtype=object)
    model = np.empty(n_devices, dtype=object)
    for f in range(len(pools.DEVICE_MODELS)):
        m = fam == f
        k = int(m.sum())
        if not k:
            continue
        vers = np.array(pools.DEVICE_MODELS[f][1])
        mods = np.array(pools.DEVICE_MODELS[f][3])
        os_version[m] = vers[rng.integers(0, vers.size, k)]
        model[m] = mods[rng.integers(0, mods.size, k)]
    screen = np.empty(n_devices, dtype=object)
    for cls, opts in SCREEN_SIZES.items():
        m = dev_class == cls
        k = int(m.sum())
        if k:
            arr = np.array(opts)
            screen[m] = arr[rng.integers(0, arr.size, k)]
    os_version = os_version.astype(str)
    model = model.astype(str)
    screen = screen.astype(str)

    bw = np.array([b[1] for b in pools.BROWSERS], dtype=np.float64)
    bw /= bw.sum()
    browser = np.array([b[0] for b in pools.BROWSERS])[
        rng.choice(len(pools.BROWSERS), n_devices, p=bw)]
    # Safari does not exist on Windows or Android, and a dataset where it does is
    # a dataset a fraud analyst stops trusting on the first plot.
    bad = (browser == "Safari") & ~np.isin(os_family, ["iOS", "iPadOS", "macOS"])
    browser[bad] = "Chrome"
    bad = (browser == "Samsung Internet") & (os_family != "Android")
    browser[bad] = "Chrome"

    fp = rng.integers(0, 2**62, n_devices, dtype=np.int64)
    return pa.table({
        "device_sk": pa.array(np.arange(1, n_devices + 1), pa.int32()),
        "device_fingerprint": pa.array([f"fp_{v:016x}" for v in fp]),
        "device_class": pa.array(dev_class).dictionary_encode(),
        "os_family": pa.array(os_family).dictionary_encode(),
        "os_version": pa.array(os_version).dictionary_encode(),
        "device_model": pa.array(model).dictionary_encode(),
        "browser_family": pa.array(browser).dictionary_encode(),
        "screen_resolution": pa.array(screen).dictionary_encode(),
        "is_emulator": pa.array(rng.random(n_devices) < 0.0038, pa.bool_()),
        "first_seen_on": pa.array(
            [config.START_DATE - dt.timedelta(days=int(d))
             for d in rng.integers(0, 900, n_devices)], pa.date32()),
    }), {"fingerprint": fp}


def bridge_customer_device(rng: np.random.Generator, n_customers: int,
                           n_devices: int, per_customer_mean: float
                           ) -> tuple[pa.Table, dict]:
    """Who used which device. Many-to-many in BOTH directions, and that is the point.

    A customer has two or three devices. A device is usually used by one customer
    -- but a declared share is used by several, and those come in two flavours that
    look identical in the data and are completely different in meaning: a
    household sharing a tablet, and a fraud ring sharing a laptop. Telling them
    apart is a genuine analytical question with a real answer in this dataset
    (rings also share an IP block and buy in high-risk categories), which is what
    makes it worth asking.
    """
    # DISJOINT SLICES, not random draws. Assigning each link a random device made
    # 51.5 % of all devices "shared" by pure collision, so the household-versus-ring
    # question -- which is the whole reason this bridge exists -- had no answer: the
    # noise and the signal were the same size. Every customer now owns their own
    # devices, and sharing is only ever deliberate.
    # Mean device count is the profile's own parameter, so the inventory that
    # `generate.py` sizes and the links drawn here cannot drift apart.
    per_customer = 1 + rng.binomial(3, np.clip((per_customer_mean - 1) / 3.0, 0.02, 0.98),
                                    n_customers)
    total_links = int(per_customer.sum())
    if total_links > n_devices:
        raise ValueError(f"{n_devices} devices cannot give {n_customers} customers "
                         f"{total_links} exclusive links; raise devices_per_customer")
    cust = np.repeat(np.arange(1, n_customers + 1), per_customer)
    device = np.arange(1, total_links + 1, dtype=np.int32)
    rng.shuffle(device)

    # Households: a slice of devices gains extra owners drawn from nearby customer
    # ids, which is a cheap stand-in for "same address".
    n_shared = int(n_devices * config.SHARED_DEVICE_SHARE)
    shared_devices = rng.choice(np.arange(1, total_links + 1), n_shared, replace=False)
    extra_cust, extra_dev = [], []
    # Extra owners per shared device: geometric, so most are a couple sharing a
    # tablet and a few are a large household or a small office. The range has to
    # OVERLAP the ring sizes below.
    #
    # Capping households at five owners while rings ran 4-11 made "six or more
    # people" a perfect ring detector at full scale: risk 187 below the line and 320
    # above it, 7 % high-risk categories below and 51 % above. A cliff like that
    # turns the flagship exercise of this dataset into `WHERE people >= 6` and
    # removes the reason to look at networks or categories at all.
    extra_counts = np.clip(rng.geometric(0.38, n_shared), 1, 10)
    for d, kk in zip(shared_devices, extra_counts):
        base = int(rng.integers(1, n_customers + 1))
        for j in range(int(kk)):
            extra_cust.append(1 + (base + j) % n_customers)
            extra_dev.append(int(d))

    # Rings: 4-11 customers who are NOT neighbours in id space, sharing devices.
    # Sharing a DEVICE is not on its own what separates a ring from a household --
    # a family shares a tablet too. What separates them is that a ring also shares
    # a network and buys where money leaves fastest; `ring_id` is returned so the
    # generator can pin those two as well.
    n_rings = max(1, int(n_customers / 10_000 * config.FRAUD_RING_COUNT_PER_10K))
    ring_id = np.zeros(n_customers + 1, dtype=np.int32)
    for r in range(1, n_rings + 1):
        size = int(rng.integers(4, 12))
        members = rng.choice(np.arange(1, n_customers + 1), size, replace=False)
        ring_id[members] = r
        shared = rng.choice(np.arange(1, total_links + 1), max(2, size // 2),
                            replace=False)
        # A ring member uses SOME of the ring's devices, not all of them. Attaching
        # every member to every device gave each ring device exactly `size` owners,
        # so 9-to-11-person devices were rings and nothing else -- a second cliff,
        # just further along the axis. Partial overlap puts ring devices squarely
        # inside the household range, which is the point: the count alone must not
        # answer the question. The network and the category have to.
        for m in members:
            take = shared[rng.random(shared.size) < 0.62]
            if take.size == 0:
                take = shared[:1]
            for d in take:
                extra_cust.append(int(m))
                extra_dev.append(int(d))

    if extra_cust:
        cust = np.concatenate([cust, np.array(extra_cust, dtype=np.int64)])
        device = np.concatenate([device, np.array(extra_dev, dtype=np.int32)])

    total = len(cust)
    uses = 1 + rng.geometric(0.14, total)
    first_off = rng.integers(0, config.N_DAYS - 1, total)
    span = rng.integers(0, 400, total)
    last_off = np.minimum(first_off + span, config.N_DAYS - 1)

    tbl = pa.table({
        "customer_sk": pa.array(cust.astype(np.int32), pa.int32()),
        "device_sk": pa.array(device.astype(np.int32), pa.int32()),
        "n_payments": pa.array(uses.astype(np.int32), pa.int32()),
        "first_seen_on": pa.array(
            [config.START_DATE + dt.timedelta(days=int(d)) for d in first_off],
            pa.date32()),
        "last_seen_on": pa.array(
            [config.START_DATE + dt.timedelta(days=int(d)) for d in last_off],
            pa.date32()),
        "is_trusted": pa.array(rng.random(total) < 0.58, pa.bool_()),
    })

    # Offsets so the fact generator can pick one of a customer's own devices with
    # a single gather instead of a lookup per row.
    order = np.argsort(cust, kind="stable")
    sorted_cust, sorted_dev = cust[order], device[order]
    starts = np.searchsorted(sorted_cust, np.arange(1, n_customers + 1), side="left")
    ends = np.searchsorted(sorted_cust, np.arange(1, n_customers + 1), side="right")
    return tbl, {"dev_flat": sorted_dev.astype(np.int32),
                 "dev_start": starts, "dev_count": (ends - starts),
                 "ring_id": ring_id}


def dim_product(rng: np.random.Generator, n: int,
                weights: np.ndarray) -> tuple[pa.Table, dict]:
    """The catalogue. Products belong to a category, and so do merchants: that
    shared vocabulary is what lets a basket be plausible for the shop it was
    bought in, and what makes "this segment buys these products" answerable."""
    tax = pools.PRODUCT_TAXONOMY
    tw = np.array([t[5] for t in tax], dtype=np.float64)
    sub = rng.choice(len(tax), n, p=tw / tw.sum())

    cat = np.array([t[0] for t in tax])[sub]
    subcat = np.array([t[1] for t in tax])[sub]
    noun = np.array([tax[s][2][rng.integers(len(tax[s][2]))] for s in sub])
    adj = np.array(pools.PRODUCT_ADJECTIVES)[rng.integers(0, len(pools.PRODUCT_ADJECTIVES), n)]
    brand = np.array(pools.PRODUCT_BRANDS)[rng.integers(0, len(pools.PRODUCT_BRANDS), n)]

    lo = np.array([t[3] for t in tax], dtype=np.float64)[sub]
    hi = np.array([t[4] for t in tax], dtype=np.float64)[sub]
    # Skewed log-uniform inside the band. A flat draw between the bounds put the
    # geometric mean of "Flights, 39 to 2400" at 306 EUR and dragged the median
    # basket of the whole warehouse to 450 EUR, which is roughly ten times a real
    # card ticket. The exponent pushes mass toward the cheap end, where a
    # catalogue's mass actually is.
    u = rng.random(n) ** 2.35
    price = np.exp(np.log(lo) + u * (np.log(hi) - np.log(lo)))
    # Charm pricing. Real catalogues end in 9 far more often than chance allows,
    # and a median that lands on .99 is a detail an analyst notices immediately.
    charm = rng.random(n) < 0.71
    price = np.where(charm, np.floor(price) + 0.99, np.round(price, 2))
    price_minor = np.round(price * 100).astype(np.int64)

    margin = np.clip(rng.beta(2.6, 4.0, n) * 0.7 + 0.05, 0.03, 0.78)
    return pa.table({
        "product_sk": pa.array(np.arange(1, n + 1), pa.int32()),
        "sku": pa.array([f"SKU-{i:07d}" for i in range(1, n + 1)]),
        "product_name": pa.array([f"{b} {a} {nn}" for b, a, nn in zip(brand, adj, noun)]),
        "brand": pa.array(brand).dictionary_encode(),
        "category": pa.array(cat).dictionary_encode(),
        "subcategory": pa.array(subcat).dictionary_encode(),
        "list_price_minor": pa.array(price_minor, pa.int64()),
        "unit_cost_minor": pa.array(
            np.round(price_minor * (1 - margin)).astype(np.int64), pa.int64()),
        "is_digital": pa.array(
            np.isin(cat, ["MEDIA", "GAMING", "SAAS", "EDUCATION", "CRYPTO"]), pa.bool_()),
        "is_age_restricted": pa.array(
            np.isin(cat, ["ALCOHOL", "TOBACCO", "GAMBLING"]), pa.bool_()),
        "launched_on": pa.array(
            [config.START_DATE - dt.timedelta(days=int(d))
             for d in rng.integers(0, 1800, n)], pa.date32()),
        # SHUFFLED. The weight vector arrives sorted, so assigning it in order made
        # `product_sk` literally the sales rank: SKU-0000001 was 2.18 % of all lines
        # and 51.8 % of its own category, and `ORDER BY product_sk` was a bestseller
        # list. A surrogate key must carry no information.
        "popularity_weight": pa.array(rng.permutation(weights), pa.float64()),
    }), {"cat": cat, "price_minor": price_minor, "sub": sub}
