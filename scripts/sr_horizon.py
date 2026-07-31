"""
sr_horizon.py
-------------
Single source of truth for the S/R forecast horizon.

The S/R subsystem answers ONE question: "between now and month-end, will price
touch this level?" Month-end here is the user's definition — the LAST TUESDAY
of the calendar month (the rebalance date), NOT the last trading day.

So the horizon is VARIABLE and SHRINKS through the month:
  run on Aug 2  -> horizon = Aug 2  .. Aug 25 (last Tue)  ~17 trading days
  run on Aug 12 -> horizon = Aug 12 .. Aug 25 (last Tue)  ~9  trading days
  run on Aug 26 -> last Tuesday has passed -> roll to Sep's last Tuesday

This matters for ACCURACY, not just presentation: the reach-probability table
is calibrated at a fixed 21-day forward window. Quoting a 21-day probability
for an 9-day horizon systematically OVERSTATES the chance of a touch, because
fewer trading days means strictly fewer opportunities to reach the level.
scale_probability_to_horizon() corrects for that.
"""

import pandas as pd
import numpy as np

# The table ships calibrated at this forward window (sr_reach_table.json
# ["forward_days"]). Horizon scaling is relative to it.
TABLE_FORWARD_DAYS = 21


def last_tuesday_of_month(year, month):
    """Date of the last Tuesday in the given calendar month."""
    last_day = pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)
    # weekday(): Mon=0, Tue=1 — walk back to the most recent Tuesday.
    offset = (last_day.weekday() - 1) % 7
    return (last_day - pd.Timedelta(days=offset)).normalize()


def horizon_end(as_of):
    """The month-end target date for a run made on `as_of`.

    If this month's last Tuesday has already passed (or is today), the relevant
    horizon is next month's — a run on the rebalance date itself is answering
    'what about the NEXT period', not a zero-length window.
    """
    as_of = pd.Timestamp(as_of).normalize()
    end = last_tuesday_of_month(as_of.year, as_of.month)
    if as_of >= end:
        nxt = (as_of + pd.offsets.MonthBegin(1)).normalize()
        end = last_tuesday_of_month(nxt.year, nxt.month)
    return end


def trading_days_until(as_of, end=None, trading_days=None):
    """Number of trading days strictly after `as_of` up to and including `end`.

    `trading_days`: optional DatetimeIndex of real NSE sessions (from
    nifty50.csv). When supplied, holidays are handled exactly. Without it we
    fall back to a business-day count, which OVERSTATES slightly on months with
    NSE holidays — acceptable for display, but pass the real calendar when the
    number feeds a probability.
    """
    as_of = pd.Timestamp(as_of).normalize()
    end = pd.Timestamp(end).normalize() if end is not None else horizon_end(as_of)
    if end <= as_of:
        return 0
    if trading_days is not None and len(trading_days):
        td = pd.DatetimeIndex(trading_days).normalize()
        return int(((td > as_of) & (td <= end)).sum())
    return int(len(pd.bdate_range(as_of + pd.Timedelta(days=1), end)))


def load_trading_calendar(index_path="../data/index_data/nifty50.csv"):
    """Real NSE session dates from the index file, for exact horizon counts.

    Returns None if unavailable — callers then fall back to business days.
    Date parsing is hardened (errors='coerce' + dropna): nifty50.csv has
    historically carried a malformed MultiIndex header row that parses to NaT.
    """
    try:
        df = pd.read_csv(index_path, usecols=["Date"])
        dates = pd.to_datetime(df["Date"], errors="coerce").dropna()
        return pd.DatetimeIndex(dates).normalize().sort_values()
    except Exception:
        return None


def project_calendar_forward(trading_days, end):
    """Extend a historical session calendar forward to `end` with business days.

    The CSV calendar stops at the last download, but the horizon runs into the
    FUTURE. Without this, trading_days_until() counts only sessions already on
    disk and reports a horizon far too short (near 0 late in the month).
    Known NSE holidays inside the horizon are not modelled, so this can
    overstate by the odd day — still far better than truncating at today.
    """
    if trading_days is None or not len(trading_days):
        return None
    td = pd.DatetimeIndex(trading_days).normalize().sort_values()
    end = pd.Timestamp(end).normalize()
    if td[-1] >= end:
        return td
    future = pd.bdate_range(td[-1] + pd.Timedelta(days=1), end)
    return td.append(pd.DatetimeIndex(future))


def scale_probability_to_horizon(prob, horizon_days,
                                 table_days=TABLE_FORWARD_DAYS):
    """Rescale a table probability from `table_days` to `horizon_days`.

    Model: treat "touching the level" as first-passage of a random walk. The
    expected extreme excursion of a random walk grows with sqrt(time), so the
    effective 'reach' shrinks by sqrt(horizon/table). We convert the table
    probability to a survival (no-touch) probability and apply that exponent:

        p_h = 1 - (1 - p_table) ** sqrt(horizon / table_days)

    Properties that make this the right shape rather than a linear haircut:
      - horizon == table_days -> unchanged (exactly)
      - horizon  < table_days -> lower probability (fewer chances to touch)
      - horizon -> 0          -> probability -> 0
      - never exceeds 1, never negative

    This is a PRINCIPLED APPROXIMATION, not an empirically fitted curve. It is
    the right direction and rough magnitude, but it is NOT calibrated against
    NSE data at sub-21-day horizons. Treat scaled numbers as indicative; the
    honest fix is rebuilding the table per horizon (see sr_build_reachtable.py
    FORWARD_DAYS) once enough forward data exists.
    """
    if prob is None or horizon_days is None or horizon_days <= 0:
        return None
    if table_days <= 0:
        return prob
    p = max(0.0, min(1.0, float(prob) / 100.0))
    scaled = 1.0 - (1.0 - p) ** float(np.sqrt(horizon_days / float(table_days)))
    return int(round(scaled * 100))


def describe(as_of, trading_days=None):
    """(end_date, n_trading_days) for the horizon from `as_of`."""
    end = horizon_end(as_of)
    return end, trading_days_until(as_of, end, trading_days)
