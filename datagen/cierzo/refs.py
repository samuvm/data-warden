"""Reference and calendar tables.

These are small, fully deterministic and independent of scale: the same at `dev`
and at `full`. That is deliberate -- a reduced profile that also shrinks its
reference data stops being a smaller copy of the warehouse and becomes a
different warehouse, and every join cardinality changes with it.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pyarrow as pa

from . import config, pools

# Spanish national holidays, plus the two pan-European retail events that move
# more money than any bank holiday. Used by the calendar dimension and by the
# settlement calendar, where a payout that lands on a holiday moves to the next
# business day -- which is the kind of rule that makes a date dimension earn its
# place instead of being a lookup for the month name.
FIXED_HOLIDAYS = [(1, 1), (1, 6), (5, 1), (8, 15), (10, 12), (11, 1),
                  (12, 6), (12, 8), (12, 25)]


def _easter(year: int) -> dt.date:
    """Anonymous Gregorian computus. Good Friday drives a real traffic dip."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def dim_date() -> pa.Table:
    days = (config.CALENDAR_END - config.CALENDAR_START).days + 1
    dates = [config.CALENDAR_START + dt.timedelta(days=i) for i in range(days)]

    holidays: set[dt.date] = set()
    for y in range(config.CALENDAR_START.year, config.CALENDAR_END.year + 1):
        for mo, da in FIXED_HOLIDAYS:
            holidays.add(dt.date(y, mo, da))
        e = _easter(y)
        holidays.add(e - dt.timedelta(days=2))  # Good Friday
        holidays.add(e + dt.timedelta(days=1))  # Easter Monday

    iso = [d.isocalendar() for d in dates]
    return pa.table({
        "date_key": pa.array([int(d.strftime("%Y%m%d")) for d in dates], pa.int32()),
        "full_date": pa.array(dates, pa.date32()),
        "year": pa.array([d.year for d in dates], pa.int16()),
        "quarter": pa.array([(d.month - 1) // 3 + 1 for d in dates], pa.int8()),
        "month": pa.array([d.month for d in dates], pa.int8()),
        "day_of_month": pa.array([d.day for d in dates], pa.int8()),
        "day_of_week": pa.array([d.weekday() + 1 for d in dates], pa.int8()),
        "day_name": pa.array([d.strftime("%A") for d in dates]).dictionary_encode(),
        "month_name": pa.array([d.strftime("%B") for d in dates]).dictionary_encode(),
        "iso_year": pa.array([i[0] for i in iso], pa.int16()),
        "iso_week": pa.array([i[1] for i in iso], pa.int8()),
        "is_weekend": pa.array([d.weekday() >= 5 for d in dates], pa.bool_()),
        "is_holiday_es": pa.array([d in holidays for d in dates], pa.bool_()),
        "is_business_day": pa.array(
            [d.weekday() < 5 and d not in holidays for d in dates], pa.bool_()),
        "fiscal_year": pa.array(
            [d.year if d.month >= 4 else d.year - 1 for d in dates], pa.int16()),
        "fiscal_quarter": pa.array(
            [((d.month - 4) % 12) // 3 + 1 for d in dates], pa.int8()),
        "days_from_epoch": pa.array(
            [(d - dt.date(1970, 1, 1)).days for d in dates], pa.int32()),
    })


def ref_country() -> pa.Table:
    c = pools.COUNTRIES
    total = sum(r[6] for r in c)
    return pa.table({
        "country_code": pa.array([r[0] for r in c]),
        "country_name": pa.array([r[1] for r in c]),
        "region": pa.array([r[2] for r in c]).dictionary_encode(),
        "local_currency": pa.array([r[3] for r in c]).dictionary_encode(),
        "centroid_lat": pa.array([r[4] for r in c], pa.float32()),
        "centroid_lon": pa.array([r[5] for r in c], pa.float32()),
        "traffic_weight": pa.array([r[6] / total for r in c], pa.float32()),
        "income_index": pa.array([r[7] for r in c], pa.float32()),
        "is_sepa": pa.array([r[2] in ("SOUTH_EU", "WEST_EU", "NORTH_EU", "EAST_EU")
                             for r in c], pa.bool_()),
    })


def ref_city() -> pa.Table:
    rows = []
    for cc, cities in pools.CITIES.items():
        for name, lat, lon, prefix in cities:
            rows.append((cc, name, lat, lon, prefix))
    return pa.table({
        "city_id": pa.array(range(1, len(rows) + 1), pa.int16()),
        "country_code": pa.array([r[0] for r in rows]).dictionary_encode(),
        "city_name": pa.array([r[1] for r in rows]),
        "lat": pa.array([r[2] for r in rows], pa.float32()),
        "lon": pa.array([r[3] for r in rows], pa.float32()),
        "postal_prefix": pa.array([r[4] for r in rows]),
    })


def ref_mcc() -> pa.Table:
    m = pools.MCC
    return pa.table({
        "mcc": pa.array([r[0] for r in m], pa.int16()),
        "mcc_description": pa.array([r[1] for r in m]),
        "category": pa.array([r[2] for r in m]).dictionary_encode(),
        "dispute_rate_pct": pa.array([r[3] for r in m], pa.float32()),
        "median_ticket_eur": pa.array([r[4] for r in m], pa.float32()),
        "is_high_risk": pa.array([r[5] for r in m], pa.bool_()),
        # A high-risk MCC pays more and waits longer for its money. Both numbers
        # are what an acquirer actually charges, and both drive fee_minor.
        "reserve_pct": pa.array([2.5 if r[5] else 0.0 for r in m], pa.float32()),
        "settlement_delay_days": pa.array([7 if r[5] else 2 for r in m], pa.int8()),
    })


def ref_decline_reason() -> pa.Table:
    d = pools.DECLINE_REASONS
    return pa.table({
        "decline_reason_code": pa.array([r[0] for r in d]),
        "network_response_code": pa.array([r[1] for r in d]),
        "description": pa.array([r[2] for r in d]),
        "category": pa.array([r[3] for r in d]).dictionary_encode(),
        "is_soft_decline": pa.array([r[4] for r in d], pa.bool_()),
        "retry_success_rate": pa.array([r[5] for r in d], pa.float32()),
        "is_retryable": pa.array([r[4] for r in d], pa.bool_()),
    })


def ref_currency() -> pa.Table:
    cur = config.CURRENCIES
    minor = {"HUF": 0, "JPY": 0}
    return pa.table({
        "currency_code": pa.array(cur),
        "minor_units": pa.array([minor.get(c, 2) for c in cur], pa.int8()),
        "traffic_weight": pa.array(config.CURRENCY_WEIGHTS, pa.float32()),
        "is_settlement_currency": pa.array([c in ("EUR", "GBP", "USD") for c in cur],
                                           pa.bool_()),
    })


# Opening rate per currency, in local units per EUR. Shared with the fact loop so
# both walk from the same starting point instead of drifting apart.
OPENING_EUR_RATE = {
    "EUR": 1.0, "GBP": 0.845, "USD": 1.084, "SEK": 11.42, "DKK": 7.46,
    "NOK": 11.68, "PLN": 4.31, "CHF": 0.951, "CZK": 25.1, "HUF": 392.0,
    "RON": 4.97, "BGN": 1.9558, "MXN": 18.6, "BRL": 5.92,
}


def ref_fx_rate_daily(rng: np.random.Generator) -> pa.Table:
    """Daily EUR rate per currency, as a random walk with drift.

    The important part is not the walk: it is that WEEKENDS AND HOLIDAYS HAVE NO
    QUOTE. "The applicable rate" is therefore the last published one, not the one
    with today's date, and any join written as `ON f.event_date = r.rate_date`
    silently drops a fifth of the rows. That trap is the reason this table exists
    as a table instead of a constant.
    """
    days = (config.END_DATE - config.START_DATE).days + 1
    dates = [config.START_DATE + dt.timedelta(days=i) for i in range(days)]
    base = OPENING_EUR_RATE
    vol = {"EUR": 0.0, "GBP": 0.0032, "USD": 0.0041, "SEK": 0.0048, "DKK": 0.0004,
           "NOK": 0.0055, "PLN": 0.0044, "CHF": 0.0035, "CZK": 0.0038,
           "HUF": 0.0062, "RON": 0.0009, "BGN": 0.0, "MXN": 0.0079, "BRL": 0.0091}

    out_cur, out_date, out_rate, out_prev = [], [], [], []
    for cur in config.CURRENCIES:
        rate = base[cur]
        for d in dates:
            # No quote on weekends. Pegged currencies still publish; EUR is the
            # base and has no row at all, which is a third case a query has to
            # handle: a NULL rate for EUR means "no conversion", not "missing".
            if d.weekday() >= 5 or cur == "EUR":
                continue
            rate *= float(np.exp(rng.normal(0.0, vol[cur])))
            out_cur.append(cur)
            out_date.append(d)
            out_rate.append(round(rate, 6))
            out_prev.append(round(base[cur], 6))
    return pa.table({
        "currency_code": pa.array(out_cur).dictionary_encode(),
        "rate_date": pa.array(out_date, pa.date32()),
        "eur_rate": pa.array(out_rate, pa.float64()),
        "reference_rate": pa.array(out_prev, pa.float64()),
    })
