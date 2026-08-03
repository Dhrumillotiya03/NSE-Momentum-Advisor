"""
test_sr_logger_dates.py
-----------------------
Regression test for S/R logger DATE STAMPING.

Rule (user spec, 2026-08-04): a row must be stamped with the date of the DATA
it describes, never the wall-clock date of the run. Running at 09:00 on Aug 4 —
before the session has produced anything — must log Aug 3, because the CMP and
therefore the S/R levels are Aug 3's.

Two mechanisms enforce this and both are tested here:
  1. drop_partial_candle() removes a same-day bar while the session is still
     open, so the last remaining bar is the previous CLOSED session.
  2. the row's Date is taken from df.index[-1] (the data), not datetime.now().

This has bitten before: wall-clock stamping logged one snapshot under two
different dates, double-counting it in the forward-accuracy analysis. It is
also load-bearing for the month files, which route rows by their own data date.

Run:  python test_sr_logger_dates.py
"""
import sys
from datetime import datetime

import pandas as pd

import support_resistance as S
import sr_daily_logger as L


def _frame(last_date):
    """Minimal OHLC frame whose final bar is `last_date`."""
    idx = pd.bdate_range(end=pd.Timestamp(last_date), periods=90)
    return pd.DataFrame(
        {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0,
         "Volume": 1000.0},
        index=idx)


def _at(when, fn, *a, **kw):
    """Run fn with datetime.now() pinned to `when` in both modules."""
    real_l, real_s = L.datetime, S.datetime

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return when

    L.datetime = Frozen
    S.datetime = Frozen
    try:
        return fn(*a, **kw)
    finally:
        L.datetime, S.datetime = real_l, real_s


def main():
    fails = []

    def check(name, got, want):
        ok = got == want
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: got {got}, want {want}")
        if not ok:
            fails.append(name)

    # A CSV that already contains today's (Aug 4) partial bar.
    with_partial = _frame("2026-08-04")
    # A CSV that ends at the previous session (Aug 3) — the normal evening case.
    closed_only = _frame("2026-08-03")

    print("\n1. Mid-session run must fall back to the previous CLOSED session")
    for hh in (9, 11, 15):
        got = _at(datetime(2026, 8, 4, hh, 0),
                  L.drop_partial_candle, with_partial).index[-1].date()
        check(f"run at {hh:02d}:00 on Aug 4", str(got), "2026-08-03")

    print("\n2. After the close, today's completed bar IS used")
    for hh in (16, 18, 22):
        got = _at(datetime(2026, 8, 4, hh, 0),
                  L.drop_partial_candle, with_partial).index[-1].date()
        check(f"run at {hh:02d}:00 on Aug 4", str(got), "2026-08-04")

    print("\n3. A weekend run never trims (no session to be partial)")
    got = _at(datetime(2026, 8, 8, 11, 0),
              L.drop_partial_candle, with_partial).index[-1].date()
    check("run at 11:00 Sat Aug 8", str(got), "2026-08-04")

    print("\n4. Nothing to trim when the CSV already ends at the prior session")
    got = _at(datetime(2026, 8, 4, 9, 0),
              L.drop_partial_candle, closed_only).index[-1].date()
    check("run at 09:00 Aug 4, CSV ends Aug 3", str(got), "2026-08-03")

    print("\n5. support_resistance shares the same guard (interactive --live path)")
    got = _at(datetime(2026, 8, 4, 11, 0),
              S.drop_partial_candle, with_partial).index[-1].date()
    check("S.drop_partial_candle at 11:00", str(got), "2026-08-03")

    print("\n6. Month files route by the row's own DATA date, not the run date")
    rows = pd.DataFrame([
        {"Symbol": "AAA", "Date": "2026-07-31", "CMP": 1.0},
        {"Symbol": "AAA", "Date": "2026-08-03", "CMP": 2.0},
    ])
    months = sorted(rows["Date"].astype(str).str[:7].unique())
    check("straddling rows split across months", str(months),
          "['2026-07', '2026-08']")

    print()
    if fails:
        print(f"{len(fails)} FAILURE(S): {fails}")
        sys.exit(1)
    print("All date-stamping checks passed.")


if __name__ == "__main__":
    main()
