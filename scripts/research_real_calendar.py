"""
THE REAL REBALANCE CALENDAR — does the backtest describe the book the user
actually trades?

Every performance number this repo has published comes from an engine that
steps a rigid 21-session grid from a fixed offset. Production rotates on the
LAST TUESDAY of each month (exit_engine.rebalance_day). Those are not the same
thing: measured on the current panel there are 133 last-Tuesday rebalances
with session gaps of 16-25 (mean 20.6) against the grid's fixed 21, and the
grid's phase alone is worth 11pp of CAGR (research_timing_luck.py).

This script runs the identical strategy on the real last-Tuesday calendar and
places it against the fixed-grid phase distribution. Drawdown is taken from
the DAILY curve (daily_marks), because 21-day sampling understates it ~5.8pp.

Usage (from scripts/):
    python research_real_calendar.py
    python research_real_calendar.py --wf
"""
import argparse
import numpy as np
import pandas as pd

import strategy_config as sc
from backtest_portfolio import (load_price_matrix, load_index, load_turnover_matrix,
                                run_backtest_laggards_only, last_tuesday_rebalance_idx)
from walk_forward import make_windows

HOLD = sc.HOLD


def daily_stats(marks):
    s = pd.Series({k: v for k, v in marks}).sort_index()
    v = s.values.astype(float)
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    r = v[1:] / v[:-1] - 1
    vol = r.std() * np.sqrt(252)
    pk = np.maximum.accumulate(v)
    dd = float(np.max((pk - v) / pk))
    return cagr, cagr / vol if vol else 0.0, dd, s.index[0], s.index[-1]


def real_calendar_idx(matrix):
    return [x for x in last_tuesday_rebalance_idx(matrix)
            if sc.LOOKBACK + 21 <= x < len(matrix) - 1]


def full_panel(matrix, index, turnover):
    print("FULL PANEL — identical strategy, different rebalance calendars\n")
    print(f"  {'calendar':>26} {'CAGR':>9} {'Sharpe':>8} {'maxDD(daily)':>13}  window")

    # fixed grid, all 21 phases
    phase_cagr = []
    for p in range(HOLD):
        dm = []
        run_backtest_laggards_only(matrix, index, turnover, phase=p, daily_marks=dm)
        c, sh, dd, d0, d1 = daily_stats(dm)
        phase_cagr.append(c)
        if p in (0,):
            print(f"  {'fixed grid, phase '+str(p):>26} {c:>+9.2%} {sh:>8.2f} "
                  f"{dd:>13.2%}  {d0.date()}..{d1.date()}")
    pc = np.array(phase_cagr)
    print(f"  {'fixed grid, phases 1-20':>26} {pc[1:].mean():>+9.2%} {'':>8} {'':>13}"
          f"  (mean; range {pc.min():+.2%}..{pc.max():+.2%})")

    # real last-Tuesday calendar
    dm = []
    run_backtest_laggards_only(matrix, index, turnover,
                               rebalance_idx=real_calendar_idx(matrix), daily_marks=dm)
    c, sh, dd, d0, d1 = daily_stats(dm)
    print(f"  {'>>> LAST TUESDAY (real)':>26} {c:>+9.2%} {sh:>8.2f} {dd:>13.2%}"
          f"  {d0.date()}..{d1.date()}")

    pct = (pc < c).mean()
    print(f"\n  The real calendar sits at the {pct:.0%}th percentile of the 21 fixed phases.")
    if c < pc.min():
        print("  IT IS BELOW EVERY FIXED PHASE. The quoted 27-33% is not just one lucky")
        print("  phase — it is a calendar the book never trades. Month-end rotation is")
        print("  a CROWDED time to rebalance (everyone does), and it costs here.")
    elif pct > 0.6:
        print("  WARNING: the real calendar FLATTERS — production numbers benefit from")
        print("  the last-Tuesday timing rather than being neutral to it.")
    return c, pc


def walk_forward(matrix, index, turnover):
    windows = make_windows(matrix, window_years=3, step_months=6)
    ridx_all = real_calendar_idx(matrix)
    print(f"\nWALK-FORWARD — {len(windows)} x 3y windows, real last-Tuesday calendar\n")
    print(f"  {'window':>25} {'lastTue CAGR':>13} {'fixed p0 CAGR':>14} {'delta':>8}")
    rows = []
    for s, e in windows:
        sub = matrix[(matrix.index >= s) & (matrix.index <= e)]
        sub = sub.loc[:, sub.isna().mean() <= 0.20]
        if len(sub) < 300:
            continue
        subtv = turnover.reindex(sub.index)
        # translate the global rebalance indices into this window's local index
        wdates = set(sub.index)
        local = [sub.index.get_loc(matrix.index[g]) for g in ridx_all
                 if matrix.index[g] in wdates]
        local = [x for x in local if sc.LOOKBACK + 21 <= x < len(sub) - 1]
        dm = []
        run_backtest_laggards_only(sub, index, subtv, rebalance_idx=local, daily_marks=dm)
        cL = daily_stats(dm)[0]
        dm0 = []
        run_backtest_laggards_only(sub, index, subtv, daily_marks=dm0)
        c0 = daily_stats(dm0)[0]
        rows.append((s, e, cL, c0))
        print(f"  {str(s.date())+'..'+str(e.date()):>25} {cL:>+13.2%} {c0:>+14.2%} "
              f"{cL - c0:>+8.2%}", flush=True)
    L = np.array([r[2] for r in rows]); F = np.array([r[3] for r in rows])
    print(f"\n  mean CAGR   last-Tuesday {L.mean():+.2%}   fixed p0 {F.mean():+.2%}   "
          f"delta {L.mean() - F.mean():+.2%}")
    print(f"  median      last-Tuesday {np.median(L):+.2%}   fixed p0 {np.median(F):+.2%}")
    print(f"  last-Tuesday wins {int((L > F).sum())}/{len(rows)} windows")
    print(f"  negative windows: last-Tuesday {int((L < 0).sum())}   fixed p0 {int((F < 0).sum())}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wf", action="store_true")
    a = ap.parse_args()
    matrix = load_price_matrix(); index = load_index()
    turnover = load_turnover_matrix(matrix)
    print("=" * 74); print("REAL REBALANCE CALENDAR — last Tuesday vs the fixed grid")
    print("=" * 74)
    print(f"Panel {matrix.index[0].date()} -> {matrix.index[-1].date()}\n")
    full_panel(matrix, index, turnover)
    if a.wf:
        walk_forward(matrix, index, turnover)


if __name__ == "__main__":
    main()
