"""People, their instruments, their devices and the catalogue they buy from.

The design rule of this module: every trait a profiling query might look for has
to CAUSE something downstream. A `segment_code` that does not change what the
customer buys, how much they spend or where they connect from is a label, and a
query that groups by it finds noise dressed as insight. So affinity drives the
merchant category, income drives the basket value, age drives the affinity, and
home country drives the IP block -- and only then does "customers of this profile
buy these products" become a fact about the data instead of a coincidence.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from . import config, pools

CATEGORIES = sorted({m[2] for m in pools.MCC})
CAT_INDEX = {c: i for i, c in enumerate(CATEGORIES)}

AGE_BANDS = ["18-24", "25-34", "35-44", "45-54", "55-64", "65+"]
# Share of active card holders per band. Declared, not fitted -- and therefore
# assertable: `validate.py` fails the build if the generated data drifts from it.
AGE_BAND_MIX = np.array([0.081, 0.212, 0.221, 0.198, 0.161, 0.127])

# Category affinity by age band. Rows sum to 1 after normalisation. These are the
# numbers a profiling query is supposed to REDISCOVER, so they are strong enough
# to be found and mixed enough not to be trivial.
_AFFINITY_BY_AGE: dict[str, dict[str, float]] = {
    "18-24": {
        "GAMING": 4.2,
        "MEDIA": 3.1,
        "FASHION": 3.4,
        "FOOD": 2.6,
        "GAMBLING": 1.9,
        "ELECTRONICS": 1.6,
        "BEAUTY": 1.7,
        "RETAIL": 1.0,
    },
    "25-34": {
        "ELECTRONICS": 2.6,
        "TRAVEL": 2.4,
        "FASHION": 2.5,
        "SAAS": 2.2,
        "FOOD": 2.1,
        "GAMING": 1.8,
        "BEAUTY": 1.6,
        "HOME": 1.5,
        "RETAIL": 1.2,
    },
    "35-44": {
        "HOME": 2.7,
        "RETAIL": 2.3,
        "TRAVEL": 2.2,
        "ELECTRONICS": 1.9,
        "EDUCATION": 1.8,
        "AUTOMOTIVE": 1.7,
        "HEALTH": 1.4,
        "SAAS": 1.6,
    },
    "45-54": {
        "HOME": 2.5,
        "AUTOMOTIVE": 2.4,
        "RETAIL": 2.4,
        "HEALTH": 2.0,
        "TRAVEL": 1.9,
        "LEISURE": 1.6,
        "FINANCIAL": 1.4,
        "ALCOHOL": 1.3,
    },
    "55-64": {
        "HEALTH": 2.9,
        "RETAIL": 2.6,
        "TRAVEL": 2.1,
        "HOME": 1.9,
        "AUTOMOTIVE": 1.5,
        "NONPROFIT": 1.5,
        "LEISURE": 1.4,
        "MEDIA": 1.1,
    },
    "65+": {
        "HEALTH": 3.6,
        "RETAIL": 2.8,
        "NONPROFIT": 2.2,
        "MEDIA": 1.5,
        "HOME": 1.4,
        "TRAVEL": 1.3,
        "LEISURE": 1.2,
        "FOOD": 1.1,
    },
}
# Income tier tilts the same distribution again: the tail categories are the ones
# that separate a VIP from a bargain hunter.
_AFFINITY_BY_INCOME = {
    0: {
        "GAMBLING": 1.9,
        "GAMING": 1.5,
        "FOOD": 1.4,
        "RETAIL": 1.4,
        "TRAVEL": 0.5,
        "ELECTRONICS": 0.6,
        "SAAS": 0.5,
        "CRYPTO": 1.3,
    },
    1: {"RETAIL": 1.2, "FOOD": 1.2, "FASHION": 1.1, "TRAVEL": 0.9},
    2: {"TRAVEL": 1.5, "ELECTRONICS": 1.4, "HOME": 1.3, "SAAS": 1.3, "GAMBLING": 0.6},
    3: {
        "TRAVEL": 2.2,
        "ELECTRONICS": 1.7,
        "HOME": 1.6,
        "FINANCIAL": 1.7,
        "SAAS": 1.5,
        "EDUCATION": 1.4,
        "GAMBLING": 0.3,
        "GAMING": 0.5,
    },
}
INCOME_TIERS = ["budget", "standard", "affluent", "premium"]
VALUE_TIERS = ["bronze", "silver", "gold", "platinum"]

# The real control letter is `"TRWAGMYFPDXBNJZSQVHLCKE"[n % 23]`. This is that
# string ROTATED, so every generated identifier fails the real check digit while
# keeping the exact shape, length and letter distribution of a Spanish NIF.
#
# The first version used the real table, and a reviewer measured 920,000
# identifiers with a correct control letter, 55.6 % of them inside the range
# already issued. Synthetic personal data must not be able to belong to anybody;
# the same rule that kept every address inside 10.0.0.0/8 applies to identifiers.
_NIF_LETTERS = "RWAGMYFPDXBNJZSQVHLCKET"

# Free-text support notes. These matter more than any other column in this table
# for what the project is trying to prove: a masking policy that works by column
# name protects `phone_e164` and then hands over a note that reads "called {name}
# on {phone}". Real warehouses are full of these fields and they are where personal
# data actually escapes.
SUPPORT_NOTE_TEMPLATES = [
    "Called {first} {last} on {phone}, card ending {last4} declined twice.",
    "{first} {last} disputes the charge; asked us to email {email}.",
    "Customer {first} says the address on file ({street}) is out of date.",
    "Refund agreed with {first} {last}. Bank details confirmed by phone {phone}.",
    "{first} reported the card lost. New card posted to {street}.",
    "Escalated: {first} {last}, DOB {dob}, cannot complete 3DS on their phone.",
    "Left voicemail for {first} at {phone}. Follow up Monday.",
    "{first} {last} asked to close the account and delete their data.",
    "Chargeback paperwork sent to {email}. Reference the NIF {nif}.",
    "Fraud review: {first} {last} confirmed the transaction was theirs.",
    "Customer is travelling; {first} will pay from abroad this month.",
    "{first} {last} requested a copy of all data we hold. Passed to the DPO.",
]

# Latin-1 and Central European letters that a mail server will not accept in a
# local part. Folding them here keeps `email` and `email_domain` consistent with
# each other, which matters because the policy publishes one as the generalisation
# of the other.
_FOLD = str.maketrans(
    {
        "á": "a",
        "à": "a",
        "ä": "a",
        "â": "a",
        "ã": "a",
        "å": "a",
        "ā": "a",
        "é": "e",
        "è": "e",
        "ë": "e",
        "ê": "e",
        "ē": "e",
        "ę": "e",
        "í": "i",
        "ì": "i",
        "ï": "i",
        "î": "i",
        "ī": "i",
        "ó": "o",
        "ò": "o",
        "ö": "o",
        "ô": "o",
        "õ": "o",
        "ø": "o",
        "ō": "o",
        "ú": "u",
        "ù": "u",
        "ü": "u",
        "û": "u",
        "ū": "u",
        "ñ": "n",
        "ń": "n",
        "ç": "c",
        "ć": "c",
        "č": "c",
        "ß": "ss",
        "ł": "l",
        "ś": "s",
        "š": "s",
        "ż": "z",
        "ź": "z",
        "ž": "z",
        "ý": "y",
        "ÿ": "y",
        "ð": "d",
        "þ": "t",
        "æ": "ae",
        "œ": "oe",
    }
)


def _ascii_fold(arr) -> pa.Array:
    """Strip diacritics from a pyarrow string array via a Python translate table.

    Done on the POOL, never on the row set: the pools hold a few thousand distinct
    names, so this runs thousands of times instead of millions.
    """
    vals = arr.to_pylist() if isinstance(arr, (pa.Array, pa.ChunkedArray)) else list(arr)
    return pa.array([v.translate(_FOLD) if v else v for v in vals], pa.string())


def _affinity_matrix() -> np.ndarray:
    """One categorical distribution per (age band, income tier) -- 24 rows.

    Building 24 distributions and sampling within groups is what keeps this
    O(customers) instead of O(customers x categories): a per-customer affinity
    vector at full scale would be a 736 MB float array that nothing ever reads
    twice.
    """
    n_cat = len(CATEGORIES)
    out = np.zeros((len(AGE_BANDS), len(INCOME_TIERS), n_cat), dtype=np.float64)
    for ai, band in enumerate(AGE_BANDS):
        for ii in range(len(INCOME_TIERS)):
            row = np.full(n_cat, 0.55)  # every category keeps a floor
            for cat, w in _AFFINITY_BY_AGE[band].items():
                row[CAT_INDEX[cat]] *= w / 0.55 * 0.55 + w
            for cat, w in _AFFINITY_BY_INCOME[ii].items():
                row[CAT_INDEX[cat]] *= w
            out[ai, ii] = row / row.sum()
    return out


AFFINITY = _affinity_matrix()


def dim_customer(
    rng: np.random.Generator,
    n: int,
    name_pools: dict,
    city_table: pa.Table,
    first_payment_day: np.ndarray | None = None,
) -> tuple[pa.Table, dict]:
    """The customer dimension, plus the trait arrays the fact generator needs.

    Personal data lives here on purpose and in variety: a direct identifier
    (`full_name` via its parts), a national identifier, contact details, a date of
    birth, a postal code -- and, next to each one that a role may not filter on, a
    GENERALISED column published for exactly that purpose. `birth_date` has
    `age_band`, `email` has `email_domain`, `postal_code` has `region_code`. That
    pairing is the difference between a policy that blocks work and one that
    redirects it.
    """
    country_w = np.array([c[6] for c in pools.COUNTRIES], dtype=np.float64)
    country_w /= country_w.sum()
    ci = rng.choice(len(pools.COUNTRIES), n, p=country_w)
    cc = np.array([pools.COUNTRIES[i][0] for i in range(len(pools.COUNTRIES))])[ci]
    income_index = np.array([pools.COUNTRIES[i][7] for i in range(len(pools.COUNTRIES))])[ci]

    # City within the country. Built as one offset table so the lookup stays a
    # single vectorised gather instead of a per-country loop at 9 M rows.
    city_cc = city_table.column("country_code").to_pylist()
    city_ids = city_table.column("city_id").to_numpy()
    by_country: dict[str, np.ndarray] = {}
    for code in set(city_cc):
        by_country[code] = city_ids[np.array([x == code for x in city_cc])]
    offsets = np.zeros(len(pools.COUNTRIES) + 1, dtype=np.int64)
    flat = []
    for i, c in enumerate(pools.COUNTRIES):
        ids = by_country[c[0]]
        flat.append(ids)
        offsets[i + 1] = offsets[i] + len(ids)
    flat = np.concatenate(flat)
    counts = np.diff(offsets)[ci]
    city_id = flat[offsets[ci] + (rng.random(n) * counts).astype(np.int64)]

    # Age. The band mix is DECLARED (see AGE_BAND_MIX) and the age is drawn inside
    # the band, rather than the other way round.
    #
    # Every single-component distribution tried for this -- gamma at four
    # parameterisations -- reproduced the 25-44 body and then under-represented
    # the over-55s by a factor of two, because a card-holder pyramid is not one
    # process: it is young adults entering plus a long retirement plateau. Fitting
    # a shape until its tail looks acceptable would have made `age_band` an
    # emergent artefact of two magic numbers. Declaring the mix makes it an input
    # that a test can assert against.
    age_band_idx = rng.choice(len(AGE_BANDS), n, p=AGE_BAND_MIX).astype(np.int8)
    lo = np.array([18, 25, 35, 45, 55, 65])[age_band_idx]
    hi = np.array([25, 35, 45, 55, 65, 93])[age_band_idx]
    # Inside a band ages are close to uniform, with a mild downward tilt in the
    # open-ended top band: there are more 66-year-olds than 88-year-olds.
    u = rng.random(n)
    u = np.where(age_band_idx == len(AGE_BANDS) - 1, u**1.9, u)
    age = (lo + u * (hi - lo)).astype(np.int16)
    ref = dt.date(2026, 8, 31)
    birth_days = (
        np.array([(ref - dt.date(1970, 1, 1)).days] * n)
        - age.astype(np.int64) * 365
        - rng.integers(0, 365, n)
    )

    # Income tier: country wealth plus personal variation. This is what makes
    # "affluent customers in Portugal" a smaller and more interesting cohort than
    # "affluent customers in Switzerland".
    income_score = income_index * rng.lognormal(0.0, 0.42, n)
    q = np.quantile(income_score, [0.45, 0.78, 0.94])
    income_tier = np.searchsorted(q, income_score).astype(np.int8)

    # Dominant category, sampled per (age band, income tier) group.
    affinity_cat = np.empty(n, dtype=np.int8)
    for a in range(len(AGE_BANDS)):
        for i in range(len(INCOME_TIERS)):
            m = (age_band_idx == a) & (income_tier == i)
            k = int(m.sum())
            if k:
                affinity_cat[m] = rng.choice(len(CATEGORIES), k, p=AFFINITY[a, i])

    affinity_strength = np.clip(rng.beta(4.2, 2.6, n), 0.05, 0.97)
    price_sensitivity = np.clip(rng.beta(3.0, 3.0, n) + (3 - income_tier) * 0.06, 0, 1)
    merchant_loyalty = np.clip(rng.beta(2.4, 3.1, n), 0.02, 0.95)
    risk_propensity = np.clip(rng.beta(1.5, 26.0, n), 0.0, 1.0)
    channel_pref = rng.choice([0, 1, 2], n, p=[0.58, 0.27, 0.15])  # web / app / pos

    spend_factor = (income_index * rng.lognormal(0.0, 0.55, n)).astype(np.float32)
    value_tier = np.searchsorted(
        np.quantile(spend_factor, [0.50, 0.82, 0.96]), spend_factor
    ).astype(np.int8)

    firsts = np.concatenate([name_pools[f"{c[0]}_first"] for c in pools.COUNTRIES])
    lasts = np.concatenate([name_pools[f"{c[0]}_last"] for c in pools.COUNTRIES])
    streets = np.concatenate([name_pools[f"{c[0]}_street"] for c in pools.COUNTRIES])
    f_off = np.cumsum([0] + [len(name_pools[f"{c[0]}_first"]) for c in pools.COUNTRIES])
    l_off = np.cumsum([0] + [len(name_pools[f"{c[0]}_last"]) for c in pools.COUNTRIES])
    s_off = np.cumsum([0] + [len(name_pools[f"{c[0]}_street"]) for c in pools.COUNTRIES])
    fi = f_off[ci] + (rng.random(n) * np.diff(f_off)[ci]).astype(np.int64)
    li = l_off[ci] + (rng.random(n) * np.diff(l_off)[ci]).astype(np.int64)
    l2 = l_off[ci] + (rng.random(n) * np.diff(l_off)[ci]).astype(np.int64)
    si = s_off[ci] + (rng.random(n) * np.diff(s_off)[ci]).astype(np.int64)

    first_name = pa.array(firsts[fi].astype(str)).dictionary_encode()
    last_name_1 = pa.array(lasts[li].astype(str)).dictionary_encode()
    # A second surname exists in Spain and Portugal and nowhere else in this
    # dataset. NULL here means "this naming system has no second surname", which
    # is a different thing from "we failed to collect it" -- and any query that
    # coalesces the two is wrong in a way no test catches.
    has_second = np.isin(cc, ["ES", "PT", "MX", "BR"])
    last_name_2 = pa.array(
        np.where(has_second, lasts[l2].astype(str), None), pa.string()
    ).dictionary_encode()

    dom_names = np.array([d[0] for d in pools.EMAIL_DOMAINS])
    dom_w = np.array([d[1] for d in pools.EMAIL_DOMAINS], dtype=np.float64)
    dom_w /= dom_w.sum()
    email_domain = dom_names[rng.choice(len(dom_names), n, p=dom_w)]
    # Local part: first.last + digits, transliterated. Real mailboxes have no
    # accents, and leaving them in would make `email_domain` -- the generalised
    # column the policy points at -- the only part of the address that is clean.
    # Fold the POOLS, then index. The first version folded the expanded row array,
    # which is a Python-level `str.translate` per customer: correct, and ninety
    # seconds of it at full scale for a result with only a few thousand distinct
    # values. The docstring said pool; the call said rows.
    firsts_folded = np.array(
        _ascii_fold(pa.array([x.lower() for x in firsts.astype(str)])).to_pylist()
    )
    lasts_folded = np.array(
        _ascii_fold(pa.array([x.lower() for x in lasts.astype(str)])).to_pylist()
    )
    local = pa.array(np.char.add(firsts_folded[fi], lasts_folded[li]))
    local = pc.replace_substring_regex(local, r"[^a-z0-9]", "")
    email = pc.binary_join_element_wise(
        local, pa.array(rng.integers(1, 9999, n).astype(str)), pa.array(email_domain), "@"
    ).cast(pa.string())
    email = pc.replace_substring_regex(email, r"@(\d+)@", r"\1@")

    nif_num = rng.integers(10_000_000, 99_999_999, n)
    national_id = np.array([f"{v:08d}{_NIF_LETTERS[v % 23]}" for v in nif_num])

    # The postal prefix comes from the customer's actual city, not from their
    # country's first city: the region code has to be a real generalisation of the
    # postal code, or the "use region_code instead" advice in a rejection message
    # would point at a column that answers a different question.
    prefix_by_city = np.empty(int(city_ids.max()) + 1, dtype=object)
    for cid, pref in zip(
        city_ids, city_table.column("postal_prefix").to_pylist(), strict=False
    ):
        prefix_by_city[cid] = pref
    postal = prefix_by_city[city_id].astype(str)
    postal_code = np.char.add(postal, np.char.zfill(rng.integers(0, 1000, n).astype(str), 3))

    # SIGN-UP MUST PRECEDE THE FIRST PAYMENT. `first_payment_day` is the day index
    # of each customer's earliest assigned payment (-1 for those who never pay),
    # computed before this dimension is built precisely so the constraint can hold.
    #
    # Drawing the sign-up date independently -- the first version -- gave 19.3 % of
    # paying customers a first payment before they existed, by up to 698 days. It is
    # the kind of defect that survives every foreign-key check and then makes every
    # cohort and retention query wrong.
    signup_days = rng.integers(30, 2600, n)
    if first_payment_day is not None:
        pays = first_payment_day >= 0
        # Days measured backwards from END_DATE, so a LARGER value is EARLIER.
        days_from_end_of_first = (
            (config.N_DAYS - 1 - first_payment_day)
            + (config.END_DATE - config.START_DATE).days
            - (config.N_DAYS - 1)
        )
        latest_signup = np.where(pays, days_from_end_of_first, 0)
        # Sign-up lands from one day to two years before the first payment, with a
        # heavy skew toward "signed up shortly before buying", which is what a
        # checkout-time registration looks like.
        lead = 1 + rng.geometric(0.012, n)
        signup_days = np.where(pays, latest_signup + lead, signup_days)
    epoch = config.END_DATE

    # Planted impossible dates. A 1900-01-01 sentinel is what an upstream system
    # writes when the field was mandatory and unknown; a future birth date is a
    # transposition typo. Both exist in every real customer table.
    bad = rng.random(n) < config.IMPOSSIBLE_BIRTHDATE_SHARE
    n_bad = int(bad.sum())
    if n_bad:
        sentinel = rng.random(n_bad) < 0.7
        bd = np.where(
            sentinel,
            (dt.date(1900, 1, 1) - dt.date(1970, 1, 1)).days,
            (dt.date(2031, 6, 1) - dt.date(1970, 1, 1)).days + rng.integers(0, 900, n_bad),
        )
        birth_days[bad] = bd

    # Phones are built before the notes so a note can quote its owner's real number.
    phone_e164 = np.array(
        [
            f"+{a}{b:09d}"
            for a, b in zip(rng.integers(30, 99, n), rng.integers(0, 10**9, n), strict=False)
        ]
    )

    # --- support notes, KYC and the GDPR lifecycle -----------------------------
    has_note = rng.random(n) < 0.081
    note_idx = np.flatnonzero(has_note)
    notes = np.full(n, None, dtype=object)
    if note_idx.size:
        tmpl = rng.integers(0, len(SUPPORT_NOTE_TEMPLATES), note_idx.size)
        fn = firsts[fi[note_idx]].astype(str)
        ln = lasts[li[note_idx]].astype(str)
        # The phone in the note is the customer's OWN phone, drawn from the column
        # three positions to the left. Re-sampling it -- which the first version did
        # -- made the flagship demo of this whole dataset a lie: "mask the column and
        # the same number turns up in free text" only works if it IS the same number.
        # Four independent reviewers measured 0 matches out of 18,374.
        ph = phone_e164[note_idx]
        st = streets[si[note_idx]].astype(str)
        # Placeholder; replaced with the real PAN suffix once the cards exist. Cards
        # are built after customers, so the note is patched in `dim_card`.
        l4 = np.full(note_idx.size, "0000")
        note_email = np.array(email.take(pa.array(note_idx)).to_pylist())
        note_dob = np.array([str(np.datetime64(int(v), "D")) for v in birth_days[note_idx]])
        built = np.empty(note_idx.size, dtype=object)
        for t in range(len(SUPPORT_NOTE_TEMPLATES)):
            m = tmpl == t
            if not m.any():
                continue
            body = SUPPORT_NOTE_TEMPLATES[t]
            # The note quotes the customer's OWN date of birth and OWN address, not a
            # placeholder. A hardcoded 1900-01-01 in every escalation note would have
            # made the leak fake: the whole point of this column is that the value it
            # exposes is the same value the policy is protecting three columns to the
            # left.
            built[m] = [
                body.format(
                    first=a, last=b, phone=c, street=d, last4=e, email=h, dob=dob_txt, nif=g
                )
                for a, b, c, d, e, g, h, dob_txt in zip(
                    fn[m],
                    ln[m],
                    ph[m],
                    st[m],
                    l4[m],
                    national_id[note_idx][m],
                    note_email[m],
                    note_dob[m],
                    strict=False,
                )
            ]
        notes[note_idx] = built

    # KYC. Only business accounts and high-value tiers are verified; the rest never
    # needed it. `kyc_verified_on` is NULL for every status other than `verified`,
    # which is a different NULL from "we lost the date".
    kyc_needed = (email_domain == "empresa-ejemplo.es") | (value_tier >= 2)
    kyc_roll = rng.random(n)
    kyc_status = np.where(
        ~kyc_needed,
        "not_required",
        np.where(
            kyc_roll < 0.79,
            "verified",
            np.where(
                kyc_roll < 0.90, "pending", np.where(kyc_roll < 0.96, "expired", "rejected")
            ),
        ),
    )
    kyc_days = rng.integers(1, 1800, n)
    kyc_verified = np.where(kyc_status == "verified", np.minimum(kyc_days, signup_days), -1)

    # Right to erasure. A tiny share of customers have asked to be deleted, and the
    # retention clock is a real column with a real rule behind it: six years from
    # sign-up for anti-money-laundering records.
    erased = rng.random(n) < 0.0034
    erasure_days = np.where(erased, rng.integers(0, 400, n), -1)

    tbl = pa.table(
        {
            "customer_sk": pa.array(np.arange(1, n + 1), pa.int32()),
            "customer_id": pa.array([f"CUS-{i:08d}" for i in range(1, n + 1)]),
            "first_name": first_name,
            "last_name_1": last_name_1,
            "last_name_2": last_name_2,
            "email": email,
            "email_domain": pa.array(email_domain).dictionary_encode(),
            "phone_e164": pa.array(phone_e164),
            "national_id": pa.array(national_id),
            "birth_date": pa.array(
                birth_days.astype("datetime64[D]").astype("int32"), pa.date32()
            ),
            "age_band": pa.array(np.array(AGE_BANDS)[age_band_idx]).dictionary_encode(),
            "street_address": pa.array(
                [
                    f"{s} {n_}"
                    for s, n_ in zip(
                        streets[si].astype(str), rng.integers(1, 220, n), strict=False
                    )
                ]
            ),
            "postal_code": pa.array(postal_code),
            "region_code": pa.array(
                np.char.add(cc.astype(str) + "-", postal)
            ).dictionary_encode(),
            "city_id": pa.array(city_id.astype(np.int16), pa.int16()),
            "country_code": pa.array(cc).dictionary_encode(),
            "income_tier": pa.array(np.array(INCOME_TIERS)[income_tier]).dictionary_encode(),
            "value_tier": pa.array(np.array(VALUE_TIERS)[value_tier]).dictionary_encode(),
            "primary_category": pa.array(
                np.array(CATEGORIES)[affinity_cat]
            ).dictionary_encode(),
            "segment_code": pa.array(
                np.char.add(
                    np.char.add(
                        np.array(VALUE_TIERS)[value_tier].astype(str) + "-",
                        np.array(AGE_BANDS)[age_band_idx].astype(str),
                    ),
                    np.char.add("-", np.array(CATEGORIES)[affinity_cat].astype(str)),
                )
            ).dictionary_encode(),
            "signed_up_on": pa.array(
                [epoch - dt.timedelta(days=int(d)) for d in signup_days], pa.date32()
            ),
            "marketing_opt_in": pa.array(rng.random(n) < 0.41, pa.bool_()),
            "is_business_account": pa.array(email_domain == "empresa-ejemplo.es", pa.bool_()),
            # THE COLUMN A COLUMN-NAME POLICY CANNOT PROTECT.
            "support_note": pa.array(notes.tolist(), pa.string()),
            "kyc_status": pa.array(kyc_status).dictionary_encode(),
            "kyc_verified_on": pa.array(
                [None if d < 0 else epoch - dt.timedelta(days=int(d)) for d in kyc_verified],
                pa.date32(),
            ),
            "erasure_requested_on": pa.array(
                [None if d < 0 else epoch - dt.timedelta(days=int(d)) for d in erasure_days],
                pa.date32(),
            ),
            "retention_expires_on": pa.array(
                [
                    epoch - dt.timedelta(days=int(d)) + dt.timedelta(days=6 * 365)
                    for d in signup_days
                ],
                pa.date32(),
            ),
        }
    )

    traits = {
        "signup_days_before_end": signup_days,
        "birth_days": birth_days.astype(np.int64),
        "country_idx": ci.astype(np.int16),
        "city_id": city_id.astype(np.int16),
        "age": age,
        "age_band_idx": age_band_idx.astype(np.int8),
        "income_tier": income_tier,
        "value_tier": value_tier,
        "affinity_cat": affinity_cat,
        "affinity_strength": affinity_strength.astype(np.float32),
        "price_sensitivity": price_sensitivity.astype(np.float32),
        "merchant_loyalty": merchant_loyalty.astype(np.float32),
        "risk_propensity": risk_propensity.astype(np.float32),
        "channel_pref": channel_pref.astype(np.int8),
        "spend_factor": spend_factor,
        "impossible_birthdate": bad,
    }
    return tbl, traits


def dim_card(
    rng: np.random.Generator,
    n_customers: int,
    traits: dict,
    first_payment_day: np.ndarray | None = None,
) -> tuple[pa.Table, dict]:
    """Payment instruments. Modelled the way PCI DSS forces you to.

    The full card number is never stored, not even synthetically: there is a
    surrogate token, the six-digit BIN (which is issuer metadata, not a secret)
    and the last four digits. Generating a valid-looking PAN would be a liability
    in a public repository for no analytical gain whatsoever.
    """
    n_cards = 1 + rng.binomial(3, 0.31 + 0.10 * traits["value_tier"] / 3.0)
    total = int(n_cards.sum())
    owner = np.repeat(np.arange(1, n_customers + 1), n_cards)
    card_starts = np.zeros(n_customers + 1, dtype=np.int64)
    card_starts[1:] = np.cumsum(n_cards)
    # First card of each customer is the primary one. Built by arithmetic rather
    # than by concatenating nine million little Python lists.
    is_primary = (np.arange(total) - np.repeat(card_starts[:-1], n_cards)) == 0

    bw = np.array([b[5] for b in pools.CARD_BINS], dtype=np.float64)
    bw /= bw.sum()
    # Issuer country correlates with the cardholder's country: a Spanish customer
    # mostly holds Spanish cards, and the exceptions are what make
    # `is_cross_border` a real feature rather than a coin flip.
    bin_country = np.array([b[3] for b in pools.CARD_BINS])
    cust_cc = np.array([pools.COUNTRIES[i][0] for i in traits["country_idx"]])[owner - 1]
    bin_idx = rng.choice(len(pools.CARD_BINS), total, p=bw)
    # Most people hold a card issued in the country they live in. At 79 % the
    # residual, combined with the merchant side, kept `is_cross_border` above 50 %
    # against a European reality of 15-25 %, and that one flag feeds approval,
    # interchange and risk at once.
    domestic_pref = rng.random(total) < 0.93
    for cc in np.unique(cust_cc):
        local = np.flatnonzero(bin_country == cc)
        if local.size == 0:
            continue
        m = domestic_pref & (cust_cc == cc)
        k = int(m.sum())
        if k:
            lw = bw[local] / bw[local].sum()
            bin_idx[m] = rng.choice(local, k, p=lw)

    scheme = np.array([b[0] for b in pools.CARD_BINS])[bin_idx]
    bin6 = np.array([b[1] for b in pools.CARD_BINS])[bin_idx]
    issuer = np.array([b[2] for b in pools.CARD_BINS])[bin_idx]
    icc = bin_country[bin_idx]
    funding = np.array([b[4] for b in pools.CARD_BINS])[bin_idx]

    exp_month = rng.integers(1, 13, total)
    # A LIVE portfolio expires in the future. Drawing 2026-2031 put a sixth of every
    # card's expiry inside the last year of the window, so the approval rate slid
    # from 89.0 % to 85.7 % over the final two quarters -- a trend an analyst reads
    # as a deteriorating book and which is really a dataset that forgot cards get
    # reissued. Only the declared early-expiry cohort below expires in-window.
    exp_year = 2027 + rng.integers(0, 5, total)
    # A declared slice of cards expires inside the fact window. Those are the
    # rows that make `expired_card` declines appear for a real reason instead of
    # being sprinkled at random. Kept at 2.2 %, and paired with the replacement
    # behaviour in the fact loop: at 5 % `expired_card` came out as the single most
    # common decline reason in the whole warehouse, ahead of insufficient funds.
    # That ordering is wrong in a way anyone who has worked on payments spots in
    # one glance, and no amount of correct plumbing underneath survives it.
    # DECLARED SHARE, DELIVERED. The cohort is drawn among NON-PRIMARY cards only --
    # a primary card that expires mid-window would drag the whole approval rate down
    # -- but the rate is applied to that population, not to all cards and then halved
    # by the filter. The first version wrote `early = early & ~is_primary`, which
    # delivered 0.98 % against a declared 2.2 %: a documented figure that the data
    # did not honour, which is the same class of defect as the missing duplicates.
    early = (~is_primary) & (
        rng.random(total) < config.EXPIRED_CARD_SHARE / max(1e-9, float((~is_primary).mean()))
    )
    exp_year[early] = 2024 + rng.integers(0, 3, int(early.sum()))

    earliest_use = np.full(n_customers, config.N_DAYS - 1, dtype=np.int64)
    if first_payment_day is not None:
        pays = first_payment_day >= 0
        earliest_use[pays] = first_payment_day[pays]
    # Days before END_DATE of each owner's first payment; larger means earlier.
    use_days_before_end = ((config.END_DATE - config.START_DATE).days - earliest_use)[owner - 1]
    signup_before_end = traits["signup_days_before_end"][owner - 1]
    added_days = np.clip(rng.integers(10, 2400, total), use_days_before_end, signup_before_end)
    added_days = np.maximum(added_days, use_days_before_end)

    tbl = pa.table(
        {
            "card_sk": pa.array(np.arange(1, total + 1), pa.int32()),
            "card_token": pa.array([f"tok_{i:011x}" for i in rng.integers(0, 16**11, total)]),
            "customer_sk": pa.array(owner.astype(np.int32), pa.int32()),
            "card_scheme": pa.array(scheme).dictionary_encode(),
            "card_bin": pa.array(bin6).dictionary_encode(),
            "pan_last4": pa.array(np.char.zfill(rng.integers(0, 10000, total).astype(str), 4)),
            "issuer_name": pa.array(issuer).dictionary_encode(),
            "issuer_country": pa.array(icc).dictionary_encode(),
            "funding_type": pa.array(funding).dictionary_encode(),
            "expiry_month": pa.array(exp_month.astype(np.int8), pa.int8()),
            "expiry_year": pa.array(exp_year.astype(np.int16), pa.int16()),
            "is_primary": pa.array(is_primary, pa.bool_()),
            # A card is added AFTER its owner signed up and BEFORE it is first used.
            # Both bounds are measured in days-before-END_DATE, so the later of the two
            # limits is the LARGER number -- which is why this is a maximum and not a
            # minimum, and why getting it backwards produced 98,769 payments made on a
            # card that did not exist yet.
            "added_on": pa.array(
                [config.END_DATE - dt.timedelta(days=int(d)) for d in added_days], pa.date32()
            ),
        }
    )
    return tbl, {
        "pan_last4": tbl.column("pan_last4").to_numpy(zero_copy_only=False),
        "card_start": card_starts[:-1],
        "card_count": n_cards,
        "card_bin_idx": bin_idx,
        "issuer_country": icc,
        "funding": funding,
        "exp_year": exp_year,
        "exp_month": exp_month,
    }
