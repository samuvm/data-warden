"""CIERZO synthetic dataset -- global configuration.

Everything that decides *what* gets generated lives here. Nothing in this module
reads the clock or the environment: given a seed and a profile name, the output is
byte-for-byte reproducible.

Scale profiles stratify by ENTITY, never by row. Sampling rows would destroy the
per-merchant time series, the at-least-once duplicate pairs (they are only
detectable in pairs) and the planted anomalies -- which are the whole reason the
dataset is worth generating.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

MASTER_SEED = 20260828

# The fact tables span exactly two years, both bounds inclusive.
START_DATE = dt.date(2024, 9, 1)
END_DATE = dt.date(2026, 8, 31)
N_DAYS = (END_DATE - START_DATE).days + 1  # 730

# The calendar dimension deliberately covers far more than the facts: a date
# dimension that stops where the data stops is a tutorial date dimension.
CALENDAR_START = dt.date(2016, 1, 1)
CALENDAR_END = dt.date(2028, 12, 31)


@dataclasses.dataclass(frozen=True)
class Profile:
    """One scale profile. `attempts` is a target, not a guarantee: the retry
    simulation decides the real row count and it is reported after the fact."""

    name: str
    merchants: int
    customers: int
    attempts: int
    employees: int
    products: int
    groups: int
    devices_per_customer: float
    row_group_size: int

    @property
    def intents(self) -> int:
        """Authorization ATTEMPTS are the grain of the main fact table; intents
        are fewer, because a declined payment gets retried."""
        return int(self.attempts / 1.092)


PROFILES: dict[str, Profile] = {
    # Under a minute. All 730 days are still present, so every date range quoted
    # in the README returns rows at this scale too.
    "dev": Profile("dev", merchants=380, customers=92_000, attempts=700_000,
                   employees=64, products=1_800, groups=46,
                   devices_per_customer=1.7, row_group_size=64_000),
    # What someone who clones the repository actually runs.
    "demo": Profile("demo", merchants=3_100, customers=920_000, attempts=6_800_000,
                    employees=320, products=9_000, groups=210,
                    devices_per_customer=1.9, row_group_size=128_000),
    # The published dataset. Only this one may back a published metric.
    "full": Profile("full", merchants=12_400, customers=9_200_000, attempts=68_400_000,
                    employees=3_140, products=42_000, groups=1_450,
                    devices_per_customer=2.1, row_group_size=256_000),
}

# ---------------------------------------------------------------------------
# Shape targets.
#
# THE RULE, and it is the whole reason this block exists: fix the CONCENTRATION
# to publish, then solve for the exponent numerically (see `shape.py`). Declaring
# an exponent and asserting a percentage afterwards is exactly how five of six
# design candidates published power laws that do not produce their own numbers.
# ---------------------------------------------------------------------------

# Concentration of the merchant WEIGHT VECTOR -- the propensity each merchant is
# drawn with, not the traffic it ends up with.
#
# The distinction is the whole reason these two constants are named this way now.
# Affinity, merchant loyalty and the domestic bias all move traffic AFTER the
# weights are drawn, so the realised concentration is lower: about 22 % / 62 % at
# demo scale against a 45 % / 80 % vector. The first version published the vector
# figure as if it were the outcome, which is precisely the failure this generator
# exists to prevent. `MANIFEST.json` now carries target, vector and measured.
MERCHANT_WEIGHT_TOP1PCT = 0.45
MERCHANT_WEIGHT_TOP10PCT = 0.80
# Floor on the REALISED concentration, asserted by the gate. It is a floor and not
# a target: what the cost estimator needs is that real skew exists, not that it
# lands on a particular number.
MERCHANT_TRAFFIC_TOP1PCT_FLOOR = 0.15
# FLOOR, not a preference: below ~300 merchants the top 1 % is a single merchant,
# and pinning it at 45 % forces the top 10 % above 87 %. The two published
# concentrations then cannot both hold. Every profile sits above that floor so
# the reduced scales reproduce the shape of the full one instead of approximating
# it -- `shape.solve_two_targets` raises rather than silently missing a target.
MERCHANT_COUNT_FLOOR = 300

# Customers are NOT a rank power law -- that would put a fifth of all traffic on
# one person. Payment counts follow a Gamma-Poisson mixture (negative binomial):
# most people pay once or twice, a thin tail pays hundreds of times.
CUSTOMER_GAMMA_SHAPE = 0.42          # < 1 => strongly over-dispersed
CUSTOMER_ZERO_PAYMENT_SHARE = 0.043  # registered, never paid: a LEFT JOIN trap

PRODUCT_TOP1PCT_SHARE = 0.34    # bestsellers concentrate, but less than merchants
LINES_PER_ORDER_MEAN = 2.24     # basket size, geometric-ish with a long tail

# Data-quality plants. Every one is a documented trap with a business meaning,
# never noise for the sake of noise.
DUPLICATE_ROW_SHARE = 0.0035    # at-least-once ingestion: same tuple, new ingestion_id
LATE_ARRIVAL_SHARE = 0.017      # ingested_at lands 1-6 days after event_ts
TEST_TRAFFIC_SHARE = 0.012      # merchant test keys hitting production
IMPOSSIBLE_BIRTHDATE_SHARE = 0.0021  # 1900-01-01 sentinels and future dates
GUEST_CHECKOUT_SHARE = 0.061    # customer_sk = -1, the unknown member
DEPRECATED_COL_DRIFT = 0.004    # amount_cents disagrees with amount_minor

# Identity plants -- these are what make the re-identification thesis real.
SHARED_DEVICE_SHARE = 0.058     # a device used by 2-5 customers: family, or a fraud ring
FRAUD_RING_COUNT_PER_10K = 1.4  # rings of 4-11 customers sharing devices and IP block
TRAVEL_SESSION_SHARE = 0.037    # payment from outside the customer's home country
VPN_IP_SHARE = 0.024
DATACENTER_IP_SHARE = 0.009

# Role budgets, in bytes scanned. Recalibrated so ALL FOUR roles have a reachable
# ceiling: a budget larger than the warehouse cannot be exceeded, and a metric
# measured over a space with no possible rejection is not a metric.
ROLE_BUDGETS_GB = {"analyst": 0.60, "ops": 0.05, "finance": 0.80, "admin": 1.50}
ROLE_ROW_LIMITS = {"analyst": 50_000, "ops": 2_000, "finance": 100_000, "admin": 250_000}

CURRENCIES = ["EUR", "GBP", "USD", "SEK", "DKK", "NOK", "PLN", "CHF",
              "CZK", "HUF", "RON", "BGN", "MXN", "BRL"]
CURRENCY_WEIGHTS = [0.615, 0.108, 0.086, 0.031, 0.021, 0.019, 0.028, 0.024,
                    0.014, 0.010, 0.008, 0.006, 0.017, 0.013]

# Corporate ownership graph. A merchant is owned by an operating company, which
# may be owned by an intermediate holding, which rolls up to an ultimate parent.
# Depth is what forces a recursive CTE to answer "where does the money land".
GROUP_MAX_DEPTH = 5
GROUP_ULTIMATE_SHARE = 0.11     # fraction of groups that are ultimate parents


# Share of PAYMENT VOLUME per merchant category. Declared, then matched by a
# greedy assignment over merchant ranks and MEASURED afterwards.
#
# Letting this emerge from a random merchant-category draw was the first version,
# and it produced a warehouse where TRAVEL was the top category in all 24 customer
# segments -- because at reduced scale one merchant holds a fifth of the traffic
# and whatever category it landed in swallowed the mix. A supermarket sector that
# is smaller than a betting sector is a warehouse no payments person believes for
# a second, and no amount of correct plumbing underneath recovers that.
CATEGORY_VOLUME_SHARE = {
    "RETAIL": 0.171, "FOOD": 0.124, "FASHION": 0.108, "TRAVEL": 0.071,
    "ELECTRONICS": 0.062, "MEDIA": 0.058, "HOME": 0.052, "GAMING": 0.051,
    "HEALTH": 0.049, "BEAUTY": 0.042, "LEISURE": 0.041, "AUTOMOTIVE": 0.039,
    "SAAS": 0.037, "GAMBLING": 0.028, "EDUCATION": 0.021, "ALCOHOL": 0.018,
    "FINANCIAL": 0.014, "TOBACCO": 0.008, "NONPROFIT": 0.004, "CRYPTO": 0.002,
}


# Risk engine operating point. Declared thresholds on the 0-999 score, chosen from
# the measured score distribution so the two queues are the size a real one is:
# roughly 0.6 % blocked outright and 4-5 % sent to manual review.
#
# The first version blocked above 940 and reviewed above 780, which produced ONE
# blocked payment and 134 reviews in two years. `risk_decision` was then a
# degenerate column, and G-BUDGET-ESCAPE-style questions about the review queue had
# nothing to operate on.
RISK_BLOCK_THRESHOLD = 520
RISK_REVIEW_THRESHOLD = 400

# Share of payments made at a merchant in the customer's OWN country. European
# card e-commerce runs 15-25 % cross-border; the rest is domestic.
DOMESTIC_MERCHANT_SHARE = 0.90

# Countries outside the EEA, where the interchange caps of Regulation (EU)
# 2015/751 do not apply.
NON_EEA = frozenset({"GB", "CH", "US", "MX", "BR", "MA", "TR", "SG", "JP"})


# Share of ALL cards that expire inside the fact window. Declared here so the gate
# can assert it: a documented rate the data does not honour is the same defect as a
# trap that was never implemented.
EXPIRED_CARD_SHARE = 0.022
