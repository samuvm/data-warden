"""Fact generation: authorization attempts and the order lines beneath them.

Three grains live in this module and they are deliberately different, because
conflating them is the most common and most expensive mistake made against a
payments warehouse:

  * `fact_payment_attempt`  -- one row per AUTHORIZATION ATTEMPT. A payment that
    fails twice and succeeds on the third try is three rows sharing one
    `payment_intent_id`. Counting revenue with `count(*)` over this table
    overstates it, and by a different factor for every merchant, because retry
    behaviour depends on the decline reason mix.
  * `fact_order_line`       -- one row per BASKET LINE. Summing `line_amount_minor`
    over a join to the attempt table multiplies every basket by its attempt count.
  * the settlement tables   -- one row per BATCH, which is where the money actually
    moved and the only grain where "how much did we get paid" has one answer.

The basket is generated BEFORE the amount, not after, so `amount_minor` is the
sum of its own lines. A dataset where the total and the lines disagree cannot be
used to teach anyone anything about grain.
"""

from __future__ import annotations

import datetime as dt

import numpy as np

from . import config, pools
from .dims_people import CATEGORIES, CAT_INDEX

CHANNELS = ["ecommerce", "app", "pos", "moto", "recurring"]
AUTH_STATUS = ["approved", "declined", "error", "timeout"]


class MerchantIndex:
    """Per-category merchant lookup, built once and gathered per day.

    Sampling a merchant from a customer-specific distribution is the hot path of
    the whole generator: it runs once per intent. Precomputing one cumulative
    weight vector per category turns it into a `searchsorted`, which is the
    difference between minutes and hours at full scale.
    """

    def __init__(self, mcc_by_natural: np.ndarray, weights: np.ndarray,
                 country_by_natural: np.ndarray | None = None,
                 region_by_natural: np.ndarray | None = None):
        cat_of_mcc = {m[0]: m[2] for m in pools.MCC}
        cats = np.array([cat_of_mcc[c] for c in mcc_by_natural])
        self.by_cat: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        self.cat_mass = np.zeros(len(CATEGORIES))
        for cat, ci in CAT_INDEX.items():
            idx = np.flatnonzero(cats == cat)
            if idx.size == 0:
                continue
            w = weights[idx]
            self.cat_mass[ci] = w.sum()
            self.by_cat[ci] = (idx, np.cumsum(w / w.sum()))
        self.present = np.array(sorted(self.by_cat))
        mass = self.cat_mass[self.present]
        self.cat_p = mass / mass.sum()
        self.cats_of_merchant = cats

        # Per (country, category) index, for the domestic path. Building 600 small
        # cumulative vectors once is what lets "shop at home" stay a searchsorted
        # instead of a filter per row.
        self.by_country_cat: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}
        if country_by_natural is not None:
            for cc in np.unique(country_by_natural):
                in_cc = country_by_natural == cc
                for cat, ci in CAT_INDEX.items():
                    idx = np.flatnonzero(in_cc & (cats == cat))
                    if idx.size == 0:
                        continue
                    w = weights[idx]
                    self.by_country_cat[(int(cc), ci)] = (idx, np.cumsum(w / w.sum()))

        # Regional fallback. A 380-merchant profile cannot populate 30 countries x
        # 20 categories, so most (country, category) cells are empty and the
        # domestic path degenerates into the global one. Falling back to the
        # customer's REGION first keeps the geography plausible at every scale --
        # a Portuguese customer buying crypto ends up in southern Europe, not in
        # Japan.
        self.by_region_cat: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}
        if region_by_natural is not None:
            for rg in np.unique(region_by_natural):
                in_rg = region_by_natural == rg
                for cat, ci in CAT_INDEX.items():
                    idx = np.flatnonzero(in_rg & (cats == cat))
                    if idx.size == 0:
                        continue
                    w = weights[idx]
                    self.by_region_cat[(int(rg), ci)] = (idx, np.cumsum(w / w.sum()))

    def sample_domestic(self, rng: np.random.Generator, cat_idx: np.ndarray,
                        country_idx: np.ndarray, fallback: np.ndarray,
                        region_idx: np.ndarray | None = None) -> np.ndarray:
        """A merchant of the requested category IN the customer's own country.

        Falls back to the caller's choice where that country has no merchant in
        that category -- which is the honest outcome: a Bulgarian customer buying
        crypto does have to go abroad, and forcing a domestic merchant that does
        not exist would be worse than the cross-border flag it is trying to fix.
        """
        out = fallback.copy()
        u = rng.random(cat_idx.size)
        keys = country_idx.astype(np.int64) * 1000 + cat_idx.astype(np.int64)
        placed = np.zeros(cat_idx.size, dtype=bool)
        for key in np.unique(keys):
            cc, ci = int(key // 1000), int(key % 1000)
            entry = self.by_country_cat.get((cc, ci))
            if entry is None:
                continue
            idx, cum = entry
            m = keys == key
            out[m] = idx[np.searchsorted(cum, u[m], side="right").clip(0, idx.size - 1)]
            placed |= m
        if region_idx is not None and not placed.all():
            rkeys = region_idx.astype(np.int64) * 1000 + cat_idx.astype(np.int64)
            for key in np.unique(rkeys[~placed]):
                rg, ci = int(key // 1000), int(key % 1000)
                entry = self.by_region_cat.get((rg, ci))
                if entry is None:
                    continue
                idx, cum = entry
                m = (~placed) & (rkeys == key)
                out[m] = idx[np.searchsorted(cum, u[m], side="right").clip(0, idx.size - 1)]
        return out

    def sample(self, rng: np.random.Generator, cat_idx: np.ndarray) -> np.ndarray:
        """Natural merchant index for each requested category.

        A category with no merchant falls back to the overall mix rather than
        raising. The generator already guarantees a floor of one merchant per
        category, so this path should be dead -- it stays because a silent
        KeyError inside a 730-iteration loop is a bad way to learn otherwise.
        """
        out = np.empty(cat_idx.size, dtype=np.int32)
        u = rng.random(cat_idx.size)
        fallback = int(self.present[int(np.argmax(self.cat_p))])
        for ci in np.unique(cat_idx):
            m = cat_idx == ci
            idx, cum = self.by_cat.get(int(ci), self.by_cat[fallback])
            out[m] = idx[np.searchsorted(cum, u[m], side="right").clip(0, idx.size - 1)]
        return out


class ProductIndex:
    """Same trick for products, keyed by the merchant's category."""

    def __init__(self, prod_cat: np.ndarray, prod_weights: np.ndarray,
                 prod_price: np.ndarray, prod_restricted: np.ndarray | None = None):
        self.by_cat: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        expand = pools.PRODUCT_CAT_TO_MCC_CAT
        for cat, ci in CAT_INDEX.items():
            sources = [k for k, v in expand.items() if cat in v] + [cat]
            idx = np.flatnonzero(np.isin(prod_cat, sources))
            if idx.size == 0:
                idx = np.arange(prod_cat.size)
            w = prod_weights[idx]
            self.by_cat[ci] = (idx, np.cumsum(w / w.sum()))
        self.price = prod_price

        # The same index restricted to goods anybody may buy, for the underage path.
        self.by_cat_open: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        if prod_restricted is not None:
            for ci, (idx, _) in self.by_cat.items():
                keep = idx[~prod_restricted[idx]]
                if keep.size == 0:
                    keep = idx
                w = prod_weights[keep]
                self.by_cat_open[ci] = (keep, np.cumsum(w / w.sum()))

    def sample(self, rng: np.random.Generator, cat_idx: np.ndarray) -> np.ndarray:
        out = np.empty(cat_idx.size, dtype=np.int32)
        u = rng.random(cat_idx.size)
        for ci in np.unique(cat_idx):
            m = cat_idx == ci
            idx, cum = self.by_cat[int(ci)]
            out[m] = idx[np.searchsorted(cum, u[m], side="right").clip(0, idx.size - 1)]
        return out

    def sample_unrestricted(self, rng: np.random.Generator,
                            cat_idx: np.ndarray) -> np.ndarray:
        """Same draw, restricted to goods that carry no age limit."""
        out = np.empty(cat_idx.size, dtype=np.int32)
        u = rng.random(cat_idx.size)
        for ci in np.unique(cat_idx):
            m = cat_idx == ci
            idx, cum = self.by_cat_open.get(int(ci), self.by_cat[int(ci)])
            out[m] = idx[np.searchsorted(cum, u[m], side="right").clip(0, idx.size - 1)]
        return out



def scd2_lookup(valid_from: np.ndarray, valid_to: np.ndarray,
                natural_idx: np.ndarray, n_natural: int,
                dates: list[dt.date]) -> np.ndarray:
    """For every (day, natural merchant), the surrogate key valid that day.

    Materialised once as a (days x merchants) int32 table. Doing the temporal join
    per fact row instead would be correct and roughly forty times slower, and the
    table is 36 MB at full scale -- which is the trade this codebase makes
    everywhere: spend memory on a lookup, never spend time inside the row loop.
    """
    n_days = len(dates)
    out = np.zeros((n_days, n_natural), dtype=np.int32)
    day_nums = np.array([(d - dt.date(1970, 1, 1)).days for d in dates])
    vf = valid_from.astype("datetime64[D]").astype(np.int64)
    vt = valid_to.astype("datetime64[D]").astype(np.int64)
    for row in range(len(natural_idx)):
        nat = natural_idx[row]
        lo = np.searchsorted(day_nums, vf[row], side="left")
        hi = np.searchsorted(day_nums, vt[row], side="right")
        if hi > lo:
            out[lo:hi, nat] = row + 1
    # Any (day, merchant) with no version would silently produce merchant_sk = 0.
    # Version 1 always starts before the fact window, so this must not happen; the
    # assertion is here because a zero foreign key is the kind of defect that
    # survives review and then breaks every join in the README at once.
    if (out == 0).any():
        raise AssertionError("SCD2 coverage hole: a merchant has no valid version "
                             "on some day inside the fact window")
    return out


def approval_probability(*, risk_score, amount_eur, is_cross_border, funding,
                         three_ds_ok, hour, issuer_base, card_expired,
                         merchant_high_risk) -> np.ndarray:
    """Logistic model of whether an issuer authorises the payment.

    Every term is a real driver, and every one of them is something an analyst is
    supposed to be able to REDISCOVER from the data:
      * a bigger ticket is scrutinised harder;
      * a cross-border payment is authorised less often than a domestic one;
      * prepaid cards decline more, debit slightly more than credit;
      * a successful 3-D Secure challenge shifts liability and lifts approval;
      * the small hours have a worse mix;
      * issuers differ from each other by a stable amount.
    If none of that were in the data, "approval rate by issuer" would be a chart of
    noise and no cost or accuracy metric built on it would mean anything.
    """
    f = lambda x: np.asarray(x, dtype=np.float32)
    z = np.full(amount_eur.shape, 2.80, dtype=np.float32)
    z += f(issuer_base)
    z -= 0.21 * np.log1p(f(amount_eur) / 120.0)
    z -= 0.42 * f(is_cross_border)
    z += np.where(funding == "credit", 0.16,
         np.where(funding == "debit", 0.0, -0.55)).astype(np.float32)
    z += 0.48 * f(three_ds_ok)
    z -= 0.0021 * f(risk_score)
    z -= 0.22 * f((hour >= 2) & (hour <= 5))
    z -= 3.4 * f(card_expired)
    z -= 0.18 * f(merchant_high_risk)
    return 1.0 / (1.0 + np.exp(-z))


def risk_score(rng, *, amount_eur, ip_risk, is_cross_border, device_new,
               ring_member, hour, high_risk_mcc) -> np.ndarray:
    """0-999. Deliberately NOT a pure function of the approval model.

    The score is what CIERZO's own engine thinks; approval is what the issuer
    decides. They correlate and disagree, which is the entire reason a risk team
    exists and the reason a query comparing the two is interesting.
    """
    # Every input is coerced to float32 first. The flags arrive as int8 because
    # they cost eight times less to carry through the day loop, and `300 * int8`
    # is a silent overflow in older numpy and a hard error in this one -- which is
    # the better failure of the two, and the reason it is caught here rather than
    # in a percentile that looked slightly off.
    f = lambda x: np.asarray(x, dtype=np.float32)
    base = rng.beta(1.7, 7.5, amount_eur.size).astype(np.float32) * 620.0
    base += 260.0 * f(ip_risk)
    base += 55.0 * f(is_cross_border)
    base += 40.0 * f(device_new)
    # Ring membership raises the score but does NOT stamp it. A flat +300 made
    # `avg(risk_score) > 450` a perfect ring detector, which is label leakage: the
    # exercise becomes reading the answer off the column instead of finding the
    # pattern in devices, networks and categories.
    base += f(ring_member) * rng.uniform(40.0, 240.0, amount_eur.size).astype(np.float32)
    base += 45.0 * f((hour >= 2) & (hour <= 5))
    base += 38.0 * f(high_risk_mcc)
    base += 90.0 * np.log1p(f(amount_eur) / 400.0)
    return np.clip(base, 0, 999).astype(np.int16)


def retry_plan(rng: np.random.Generator, approved: np.ndarray,
               reason_idx: np.ndarray, soft: np.ndarray,
               retry_lift: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """How many attempts each intent produces, and whether the last one succeeded.

    Hard declines are not retried. That is not a simplification: retrying a
    `stolen_card` response is a scheme violation that costs real money, so a
    dataset where hard declines are retried would teach the wrong lesson to
    anything trained or evaluated on it.
    """
    n = approved.size
    attempts = np.ones(n, dtype=np.int8)
    final_ok = approved.copy()

    can_retry = (~approved) & soft
    r1 = can_retry & (rng.random(n) < 0.62)
    attempts[r1] = 2
    ok2 = r1 & (rng.random(n) < retry_lift)
    final_ok[ok2] = True

    r2 = r1 & (~ok2) & (rng.random(n) < 0.31)
    attempts[r2] = 3
    ok3 = r2 & (rng.random(n) < retry_lift * 0.7)
    final_ok[ok3] = True
    return attempts, final_ok
