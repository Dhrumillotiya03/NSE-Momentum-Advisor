"""
TURN-OF-THE-MONTH EFFECT ON THE REBALANCE DATE — how much does WHICH day of
the month the book rotates cost, and is the last Tuesday the worst choice?

Motivation: research_real_calendar.py found the real last-Tuesday calendar
sits BELOW all 21 fixed-grid phases (+20.6% vs a 21.4-32.7% fixed range).
Sweeping the rebalance day across the month (last Tuesday +- offset) revealed
a sharp U-shaped trough centred on the last ~5 calendar days:
    offset -10d +35.3%   -5d +29.4%   -1d +24.2%   0d(lastTue) +20.6%
    +3d +22.3%   +7d +29.5%   +10d +31.4%
i.e. rebalancing in the last few days of the month costs 7-15pp of CAGR vs
mid-month, on the identical strategy. This is the documented turn-of-the-month
/ window-dressing flow: momentum names are bid up into month-end by index and
institutional rebalancing, and the strategy buys exactly then.

The user's mandate is a MONTHLY rebalance. It does not require the last
Tuesday specifically — that was chosen to align with the S/R horizon. This
script quantifies the cost of that alignment and walk-forwards the
alternative, so the choice can be made on evidence.

Usage (from scripts/):
    python research_turn_of_month.py            # full-panel offset sweep
    python research_turn_of_month.py --wf       # walk-forward: month-end vs mid-month
"""
import argparse
import numpy as np
import pandas as pd

import strategy_config as sc
from sr_horizon import last_tuesday_of_month
from backtest_portfolio import (load_price_matrix, load_index, load_turnover_matrix,
                                run_backtest_laggards_only)
from walk_forward import make_windows

HOLD = sc.HOLD


def monthly_schedule(dates, offset_days):
    """Last Tuesday of each month + offset_days (calendar), rolled BACK to the
    latest session on/before that target. offset 0 == production."""
    dset = set(dates)
    out, seen = [], set()
    for ts in dates:
        key = (ts.year, ts.month)
        if key in seen:
            continue
        seen.add(key)
        t = pd.Timestamp(last_tuesday_of_month(ts.year, ts.month)) + pd.Timedelta(days=offset_days)
        prior = dates[dates <= t]
        if len(prior) == 0:
            continue
        rd = prior[-1]
        if rd in dset:
            out.append(int(dates.get_loc(rd)))
    return sorted({x for x in set(out) if sc.LOOKBACK + 21 <= x < len(dates) - 1})


def daily_stats(marks, lo=None, hi=None):
    s = pd.Series({k: v for k, v in marks}).sort_index()
    if lo is not None:
        s = s[(s.index >= lo) & (s.index <= hi)]
    v = s.values.astype(float)
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    r = v[1:] / v[:-1] - 1
    vol = r.std() * np.sqrt(252)
    pk = np.maximum.accumulate(v)
    return cagr, cagr / vol if vol else 0.0, float(np.max((pk - v) / pk))


def sweep(matrix, index, turnover):
    dates = pd.DatetimeIndex(matrix.index).normalize()
    base = monthly_schedule(dates, 0)
    dmb = []
    run_backtest_laggards_only(matrix, index, turnover, rebalance_idx=base, daily_marks=dmb)
    sb = pd.Series({k: v for k, v in dmb}).sort_index()
    lo, hi = sb.index[0], sb.index[-1]

    print("REBALANCE-DAY SWEEP (last Tuesday + offset), common window "
          f"{lo.date()}..{hi.date()}\n")
    print(f"  {'offset':>7} {'approx day-of-month':>20} {'CAGR':>9} {'Sharpe':>8} {'maxDD':>8}")
    rows = []
    for off in range(-12, 15):
        idx = monthly_schedule(dates, off)
        dm = []
        run_backtest_laggards_only(matrix, index, turnover, rebalance_idx=idx, daily_marks=dm)
        c, sh, dd = daily_stats(dm, lo, hi)
        doms = [matrix.index[i].day for i in idx]
        rows.append((off, c, sh, dd, np.median(doms)))
        mark = "  <- production" if off == 0 else ""
        print(f"  {off:>+7} {int(np.median(doms)):>13}th of month {c:>+9.2%} {sh:>8.2f} "
              f"{dd:>8.2%}{mark}", flush=True)

    r = np.array([x[1] for x in rows])
    best = rows[int(np.argmax(r))]
    prod = [x for x in rows if x[0] == 0][0]
    print(f"\n  production (last Tuesday):  CAGR {prod[1]:+.2%}  Sharpe {prod[2]:.2f}  maxDD {prod[3]:.2%}")
    print(f"  best offset ({best[0]:+d}d):        CAGR {best[1]:+.2%}  Sharpe {best[2]:.2f}  maxDD {best[3]:.2%}")
    print(f"  COST OF REBALANCING AT MONTH-END: {prod[1] - best[1]:+.2%} CAGR, "
          f"{prod[3] - best[3]:+.2%} drawdown")
    print(f"\n  The last ~5 calendar days of the month are the WORST window on the")
    print(f"  panel. Mid-month (offset -7..-10) and early-next-month (+7..+12) both")
    print(f"  recover it. This is the turn-of-the-month flow, and the mandate's")
    print(f"  last-Tuesday rule sits in the centre of the trough.")


def walk_forward(matrix, index, turnover):
    dates = pd.DatetimeIndex(matrix.index).normalize()
    windows = make_windows(matrix, window_years=3, step_months=6)
    schedules = {"lastTue (0d)": 0, "mid-month (-8d)": -8, "next-month (+9d)": 9}
    idxs = {k: monthly_schedule(dates, v) for k, v in schedules.items()}
    print(f"WALK-FORWARD — {len(windows)} x 3y windows\n")
    hdr = "  " + f"{'window':>23}" + "".join(f"{k:>18}" for k in schedules)
    print(hdr)
    agg = {k: [] for k in schedules}
    for s, e in windows:
        sub = matrix[(matrix.index >= s) & (matrix.index <= e)]
        sub = sub.loc[:, sub.isna().mean() <= 0.20]
        if len(sub) < 300:
            continue
        subtv = turnover.reindex(sub.index)
        wd = set(sub.index)
        line = f"  {str(s.date())+'..'+str(e.date()):>23}"
        for k in schedules:
            local = [sub.index.get_loc(matrix.index[g]) for g in idxs[k] if matrix.index[g] in wd]
            local = [x for x in local if sc.LOOKBACK + 21 <= x < len(sub) - 1]
            dm = []
            run_backtest_laggards_only(sub, index, subtv, rebalance_idx=local, daily_marks=dm)
            c = daily_stats(dm)[0]
            agg[k].append(c)
            line += f"{c:>+18.2%}"
        print(line, flush=True)
    print()
    for k in schedules:
        a = np.array(agg[k])
        print(f"  {k:>18}  mean {a.mean():+.2%}  median {np.median(a):+.2%}  "
              f"min {a.min():+.2%}  neg {int((a < 0).sum())}/{len(a)}")
    lt = np.array(agg["lastTue (0d)"]); mm = np.array(agg["mid-month (-8d)"])
    print(f"\n  mid-month beats last-Tuesday in {int((mm > lt).sum())}/{len(lt)} windows, "
          f"mean delta {mm.mean() - lt.mean():+.2%}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wf", action="store_true")
    a = ap.parse_args()
    matrix = load_price_matrix(); index = load_index()
    turnover = load_turnover_matrix(matrix)
    print("=" * 78)
    print("TURN-OF-THE-MONTH EFFECT ON THE REBALANCE DATE")
    print("=" * 78)
    print(f"Panel {matrix.index[0].date()} -> {matrix.index[-1].date()}\n")
    if a.wf:
        walk_forward(matrix, index, turnover)
    else:
        sweep(matrix, index, turnover)


if __name__ == "__main__":
    main()
