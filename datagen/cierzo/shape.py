"""Statistical shape of the dataset.

The contract of this module: NOBODY declares an exponent. You declare the
concentration you want to publish -- "the top 1 % of merchants take 45 % of the
traffic" -- and the exponent is solved for numerically. The published number is
then true by construction instead of true by hope.
"""

from __future__ import annotations

import datetime as dt

import numpy as np

from . import config


def zipf_mandelbrot_weights(n: int, exponent: float, q: float) -> np.ndarray:
    """Normalised rank weights proportional to (i + q)^-exponent.

    Zipf-Mandelbrot, not plain Zipf. The extra parameter `q` flattens the head,
    and it is not decoration: a pure rank power law has ONE free parameter, so it
    can satisfy one concentration target and no more. Asking it for two -- "the
    top 1 % take 45 % AND the top 10 % take 80 %" -- silently gives you whichever
    one you solved for and a lie for the other. This is the same family used for
    word frequencies, and for the same reason.
    """
    ranks = np.arange(1, n + 1, dtype=np.float64)
    w = (ranks + q) ** (-exponent)
    return w / w.sum()


def top_share(n: int, exponent: float, fraction: float, q: float = 0.0) -> float:
    """Share of total weight held by the top `fraction` of ranks."""
    w = zipf_mandelbrot_weights(n, exponent, q)
    k = max(1, round(n * fraction))
    return float(w[:k].sum())


def solve_exponent(
    n: int,
    fraction: float,
    target_share: float,
    q: float = 0.0,
    lo: float = 0.001,
    hi: float = 6.0,
    tol: float = 1e-9,
) -> float:
    """Bisect for the exponent that makes the top `fraction` hold `target_share`.

    Monotonic in the exponent for fixed q, so bisection is exact and cheap. Raises
    when the target is unreachable, which is information rather than an error: it
    means the concentration asked for cannot exist with that many entities.
    """
    if not 0.0 < fraction < 1.0:
        raise ValueError(f"fraction must be in (0,1), got {fraction}")
    s_lo, s_hi = top_share(n, lo, fraction, q), top_share(n, hi, fraction, q)
    if not (s_lo <= target_share <= s_hi):
        raise ValueError(
            f"top {fraction:.1%} share of {target_share:.1%} is unreachable for "
            f"n={n}, q={q}: reachable range is [{s_lo:.3%}, {s_hi:.3%}]"
        )
    for _ in range(300):
        mid = (lo + hi) / 2.0
        if top_share(n, mid, fraction, q) < target_share:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2.0


def _max_feasible_q(n: int, fraction: float, target_share: float) -> float:
    """Largest q for which target 1 is still reachable at all.

    Beyond it, the head is so flattened that no exponent can concentrate the
    required share into the top ranks. Probing for the bound instead of assuming
    one is what keeps the outer solve from failing on small profiles, where the
    feasible window is narrow.
    """
    q, last_ok = 1.0, 0.0
    for _ in range(60):
        try:
            solve_exponent(n, fraction, target_share, q=q)
        except ValueError:
            break
        last_ok, q = q, q * 1.7
    return last_ok


def solve_two_targets(
    n: int, f1: float, s1: float, f2: float, s2: float
) -> tuple[float, float]:
    """Solve (exponent, q) so BOTH concentration targets hold simultaneously.

    Nested bisection: for each candidate q the exponent is pinned by target 1, and
    q is then moved until target 2 lands. With the head held at a fixed share, a
    larger q redistributes the remaining mass toward the ranks just below it, so
    target 2 moves monotonically -- the direction is DETECTED rather than assumed,
    because it flips with the ratio between the two fractions.

    When the two targets are jointly unreachable the error says so with the
    reachable window. That is the honest outcome: it means no single law of this
    family produces both numbers, and the config has to give one of them up.
    """

    def outer(q: float) -> float:
        return top_share(n, solve_exponent(n, f1, s1, q=q), f2, q=q)

    q_hi = _max_feasible_q(n, f1, s1)
    lo_q, hi_q = 0.0, q_hi
    v_lo, v_hi = outer(lo_q), outer(hi_q)
    ascending = v_hi >= v_lo
    span = (v_lo, v_hi) if ascending else (v_hi, v_lo)
    if not span[0] - 1e-9 <= s2 <= span[1] + 1e-9:
        raise ValueError(
            f"targets jointly unreachable for n={n}: with the top {f1:.0%} pinned "
            f"at {s1:.1%}, the top {f2:.0%} can only span "
            f"[{span[0]:.2%}, {span[1]:.2%}] over q in [0, {q_hi:.3g}], and "
            f"{s2:.1%} was asked for"
        )
    for _ in range(120):
        mid = (lo_q + hi_q) / 2.0
        below = outer(mid) < s2
        if below == ascending:
            lo_q = mid
        else:
            hi_q = mid
        if hi_q - lo_q < 1e-7:
            break
    q = (lo_q + hi_q) / 2.0
    return solve_exponent(n, f1, s1, q=q), q


def merchant_weights(n: int) -> tuple[np.ndarray, dict[str, float]]:
    """Merchant traffic weights, plus the MEASURED shape for the manifest.

    Both published concentrations are solved for jointly, and both are then
    re-measured off the weights that will actually be used. Nothing here is
    asserted from a parameter.
    """
    a, q = solve_two_targets(
        n, 0.01, config.MERCHANT_WEIGHT_TOP1PCT, 0.10, config.MERCHANT_WEIGHT_TOP10PCT
    )
    w = zipf_mandelbrot_weights(n, a, q)
    k1, k10 = max(1, round(n * 0.01)), max(1, round(n * 0.10))
    measured = {
        "law": "zipf-mandelbrot",
        "exponent": a,
        "q": q,
        "top_1pct_share": float(w[:k1].sum()),
        "top_1pct_weight_target": config.MERCHANT_WEIGHT_TOP1PCT,
        "top_10pct_share": float(w[:k10].sum()),
        "top_10pct_weight_target": config.MERCHANT_WEIGHT_TOP10PCT,
        "top_merchant_share": float(w[0]),
        "median_share": float(np.median(w)),
    }
    return w, measured


def product_weights(n: int) -> tuple[np.ndarray, dict[str, float]]:
    """Bestsellers concentrate too, but a catalogue has a much fatter body than a
    merchant portfolio: most SKUs sell something. One target, one parameter."""
    a = solve_exponent(n, 0.01, config.PRODUCT_TOP1PCT_SHARE)
    w = zipf_mandelbrot_weights(n, a, 0.0)
    k1 = max(1, round(n * 0.01))
    return w, {
        "law": "zipf",
        "exponent": a,
        "top_1pct_share": float(w[:k1].sum()),
        "top_1pct_target": config.PRODUCT_TOP1PCT_SHARE,
    }


def customer_payment_counts(
    rng: np.random.Generator, n_customers: int, total_intents: int
) -> tuple[np.ndarray, dict]:
    """Exact per-customer payment counts summing to `total_intents`.

    Two facts drive this, and getting either wrong is invisible until a query
    returns nonsense:

    1. A declared share of registered customers NEVER pays. They are the reason an
       INNER JOIN written where a LEFT JOIN belongs silently drops 4 % of the
       customer base, which is the single most common bug in this kind of query.
    2. Everyone else has AT LEAST ONE payment -- a customer with zero payments who
       is not in the never-pays cohort is a contradiction, not a long tail. So the
       count is 1 + NegativeBinomial, not NegativeBinomial.

    The Gamma-Poisson mixture on the remainder is what produces the real shape:
    a mode at one purchase, a mean several times higher, and a tail in the
    hundreds. A rank power law here would be wrong in a way that is easy to miss
    and impossible to defend -- it would make ONE customer responsible for a fifth
    of the payments in Europe.
    """
    counts = np.zeros(n_customers, dtype=np.int64)
    never = rng.random(n_customers) < config.CUSTOMER_ZERO_PAYMENT_SHARE
    payers = ~never
    n_payers = int(payers.sum())
    if n_payers == 0 or total_intents < n_payers:
        raise ValueError(
            f"{total_intents} intents cannot cover {n_payers} paying customers "
            "with at least one payment each; raise `attempts` or lower `customers`"
        )

    lam = rng.gamma(shape=config.CUSTOMER_GAMMA_SHAPE, scale=1.0, size=n_payers)
    extra_budget = total_intents - n_payers
    lam *= extra_budget / lam.sum()
    counts[payers] = 1 + rng.poisson(lam)

    # Poisson noise leaves the total a few thousand off. Close the gap on random
    # customers weighted by their own intensity, so the correction cannot distort
    # the shape it is correcting.
    idx_payers = np.flatnonzero(payers)
    p = lam / lam.sum()
    delta = total_intents - int(counts.sum())
    while delta != 0:
        step = min(abs(delta), 1_000_000)
        picks = rng.choice(idx_payers, size=step, p=p, replace=True)
        if delta > 0:
            np.add.at(counts, picks, 1)
        else:
            # Never take a payer below one payment.
            picks = picks[counts[picks] > 1]
            np.add.at(counts, picks, -1)
        delta = total_intents - int(counts.sum())

    paying = counts[counts > 0]
    # P-003-b (Samuel, 2026-09-02). This used to publish a single `never_paid_share`
    # holding `never.mean()`, and the report printed it as the MEASURED value next to
    # the target. It is not a measurement of anything downstream: it is the realised
    # draw of the never-pays cohort, so it tracks the target by construction and always
    # will. Publishing a target under the name of a measure is the exact error this
    # project exists to prevent, and the same file already gets this right for merchant
    # concentration (`top_1pct_measured_on_traffic` vs `top_1pct_share`).
    #
    # The three numbers are genuinely different and each is now named for what it is:
    #   never_paid_target        the design constant. What we asked for.
    #   never_paid_drawn         the cohort actually drawn. Tracks the target by
    #                            construction; it is a seed check, not evidence.
    #   never_paid_zero_attempts customers who end with no ATTEMPT at all, counted off
    #                            the vector. Equals `drawn` while every payer is forced
    #                            to >= 1 attempt, and stops equalling it the day that
    #                            changes -- which is precisely when we want to be told.
    #
    # None of the three is the number the glossary declares. What an INNER JOIN drops is
    # customers with no successful PAYMENT (6,2 %), which is larger because a customer
    # whose attempts all failed has no row in `v_payment_intent`. That one can only be
    # counted against the facts, and `scripts/measure_traps.py` does it there.
    stats = {
        "never_paid_target": config.CUSTOMER_ZERO_PAYMENT_SHARE,
        "never_paid_drawn": float(never.mean()),
        "never_paid_zero_attempts": float((counts == 0).mean()),
        "mean_per_paying_customer": float(paying.mean()),
        "median_per_paying_customer": float(np.median(paying)),
        "p90": int(np.percentile(paying, 90)),
        "p99": int(np.percentile(paying, 99)),
        "max": int(paying.max()),
        "share_with_one_payment": float((counts == 1).mean()),
        "top_1pct_customer_share": float(
            np.sort(counts)[::-1][: max(1, n_customers // 100)].sum() / total_intents
        ),
    }
    return counts, stats


def day_weights(dates: np.ndarray) -> np.ndarray:
    """Per-day traffic weights: trend, weekday shape, seasonality, holidays.

    Multi-scale on purpose. A single sine wave is detectable as synthetic in one
    plot; overlapping weekly, annual and event-driven components are not.
    """
    n = len(dates)
    t = np.arange(n, dtype=np.float64)

    # Business growth: +38 % over the two years, compounded daily.
    trend = (1.0 + 0.38) ** (t / n)

    dow = np.array([d.weekday() for d in dates])
    # Retail-heavy mix: Monday and Tuesday busiest, Saturday quietest.
    dow_factor = np.array([1.10, 1.08, 1.04, 1.02, 1.00, 0.83, 0.86])[dow]

    doy = np.array([d.timetuple().tm_yday for d in dates], dtype=np.float64)
    # Annual: December peak, August trough.
    annual = 1.0 + 0.17 * np.cos(2 * np.pi * (doy - 340.0) / 365.25)
    # Second harmonic: a smaller spring bump that a single cosine cannot make.
    annual += 0.05 * np.cos(4 * np.pi * (doy - 100.0) / 365.25)

    w = trend * dow_factor * annual

    # Named events. These are the spikes that make a cost estimator earn its keep:
    # a query over Black Friday scans far more than the daily average predicts.
    for i, d in enumerate(dates):
        md = (d.month, d.day)
        if d.month == 11 and 24 <= d.day <= 30:  # Black Friday week
            w[i] *= 2.35
        elif d.month == 12 and 1 <= d.day <= 3:  # Cyber Monday tail
            w[i] *= 1.75
        elif d.month == 12 and 15 <= d.day <= 23:  # Christmas run-up
            w[i] *= 1.55
        elif md in ((1, 7), (7, 1)):  # sales seasons open
            w[i] *= 1.40
        elif md == (12, 25) or md == (1, 1):  # nobody shops
            w[i] *= 0.42
        elif md == (1, 6):  # Reyes
            w[i] *= 0.55

    return w / w.sum()


def hour_weights() -> np.ndarray:
    """Hour-of-day profile: a lunchtime bump and a larger evening peak.

    Deliberately not symmetric. Real card traffic has a long, slow morning ramp
    and a sharp drop after 23:00, and the small 03:00 floor is what makes the
    'unusual hour' risk feature mean something.
    """
    h = np.arange(24, dtype=np.float64)
    lunch = np.exp(-0.5 * ((h - 13.2) / 2.1) ** 2) * 0.62
    evening = np.exp(-0.5 * ((h - 20.6) / 2.6) ** 2) * 1.00
    morning = np.exp(-0.5 * ((h - 10.4) / 2.4) ** 2) * 0.44
    floor = 0.035
    w = lunch + evening + morning + floor
    return w / w.sum()


def basket_sizes(rng: np.random.Generator, n: int) -> np.ndarray:
    """Lines per order: mostly one, occasionally many. Geometric with a heavy tail
    grafted on, because real baskets have a 40-line outlier that breaks naive
    per-order aggregation."""
    p = 1.0 / config.LINES_PER_ORDER_MEAN
    sizes = rng.geometric(p, size=n).astype(np.int16)
    # 0.4 % of baskets are bulk orders. These are what make a MAX() interesting.
    bulk = rng.random(n) < 0.004
    sizes[bulk] += rng.integers(8, 45, size=int(bulk.sum()), dtype=np.int16)
    return np.clip(sizes, 1, 60)


def business_days_between(start: dt.date, end: dt.date) -> int:
    return int(np.busday_count(start, end))
