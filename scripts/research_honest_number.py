"""
THE HONEST NUMBER — the strategy's expected return on the calendar it actually
trades, with every known measurement correction applied at once.

The repo has quoted 33.4% CAGR. That is a fixed-21-day-grid, single-phase,
21-day-sampled-drawdown, gross number. Corrected:
  - REAL CALENDAR: rebalance on the last Tuesday (research_real_calendar.py)
  - DAILY DRAWDOWN: 21-day sampling understates it ~5.8pp
  - WALK-FORWARD not full-panel: the full-panel CAGR is a compounding-path
    artifact dominated by 2019-2021 and by wherever the weak windows land
  - NET: after STCG (20%, FY-netted) and measured ~2bps/side impact

Usage:  python research_honest_number.py
"""
import numpy as np
import pandas as pd

import strategy_config as sc
from backtest_portfolio import (load_price_matrix, load_index, load_turnover_matrix,
                                run_backtest_laggards_only, last_tuesday_rebalance_idx,
                                performance)
from walk_forward import make_windows
from research_net_returns import apply_stcg

HOLD = sc.HOLD


def real_idx(m):
    return [x for x in last_tuesday_rebalance_idx(m) if sc.LOOKBACK + 21 <= x < len(m) - 1]


def daily(marks):
    s = pd.Series({k: v for k, v in marks}).sort_index()
    v = s.values.astype(float)
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    r = v[1:] / v[:-1] - 1
    vol = r.std() * np.sqrt(252)
    pk = np.maximum.accumulate(v)
    return cagr, cagr / vol if vol else 0.0, float(np.max((pk - v) / pk)), s


def main():
    m = load_price_matrix(); ix = load_index(); tv = load_turnover_matrix(m)
    ridx = real_idx(m)
    gaps = float(np.mean(np.diff(ridx)))
    print("=" * 76); print("THE HONEST NUMBER — real last-Tuesday calendar"); print("=" * 76)

    dm = []
    eq = run_backtest_laggards_only(m, ix, tv, rebalance_idx=ridx, daily_marks=dm)
    cagr, sharpe, dd, s = daily(dm)
    print(f"\n[full panel]  gross CAGR {cagr:+.2%}  Sharpe {sharpe:.2f}  maxDD(daily) {dd:.2%}")
    print(f"              (a single compounding path — NOT the number to quote; see walk-forward)")

    # net of STCG
    reb_dates = [m.index[min(ridx[k] + int(round(gaps)), len(m) - 1)]
                 for k in range(len(eq))]
    taxed = apply_stcg(np.asarray(eq, float), reb_dates)
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    net_cagr = (taxed[-1] / taxed[0]) ** (1 / yrs) - 1
    print(f"[full panel]  net of STCG (20%, FY-netted)  {net_cagr:+.2%}   "
          f"(tax drag {cagr - net_cagr:.2%})")
    print(f"[full panel]  less measured impact ~2bps/side (depth gate, carve-out size)  "
          f"~{net_cagr - 0.005:+.2%}")

    # walk-forward on the real calendar
    windows = make_windows(m, window_years=3, step_months=6)
    print(f"\n[walk-forward] {len(windows)} x 3y windows, real calendar, daily drawdown\n")
    rows = []
    for a, b in windows:
        sub = m[(m.index >= a) & (m.index <= b)]
        sub = sub.loc[:, sub.isna().mean() <= 0.20]
        if len(sub) < 300:
            continue
        subtv = tv.reindex(sub.index)
        wd = set(sub.index)
        local = sorted(sub.index.get_loc(m.index[g]) for g in ridx if m.index[g] in wd)
        local = [x for x in local if sc.LOOKBACK + 21 <= x < len(sub) - 1]
        d2 = []
        run_backtest_laggards_only(sub, ix, subtv, rebalance_idx=local, daily_marks=d2)
        c, sh, ddd, _ = daily(d2)
        rows.append((a, b, c, sh, ddd))
    C = np.array([r[2] for r in rows]); S = np.array([r[3] for r in rows])
    D = np.array([r[4] for r in rows])
    print(f"  gross CAGR   mean {C.mean():+.2%}   median {np.median(C):+.2%}   "
          f"min {C.min():+.2%}   max {C.max():+.2%}")
    print(f"  Sharpe       mean {S.mean():.2f}    median {np.median(S):.2f}")
    print(f"  max DD       mean {D.mean():.2%}    worst {D.max():.2%}")
    print(f"  negative windows: {int((C < 0).sum())}/{len(rows)}")
    print(f"\n  -> net ~= {C.mean() - (cagr - net_cagr) - 0.005:+.2%} after STCG + impact")

    # era split
    print(f"\n[era]  (real calendar, non-overlapping)")
    for lo, hi, lab in [("2015", "2019", "2015-2018"), ("2019", "2022", "2019-2021"),
                        ("2022", "2027", "2022-2026")]:
        w = s[(s.index >= lo) & (s.index < hi)]
        if len(w) < 250:
            continue
        v = w.values.astype(float)
        yy = (w.index[-1] - w.index[0]).days / 365.25
        cc = (v[-1] / v[0]) ** (1 / yy) - 1
        rr = v[1:] / v[:-1] - 1
        pk = np.maximum.accumulate(v)
        print(f"  {lab}   CAGR {cc:>+8.2%}   Sharpe {cc/(rr.std()*np.sqrt(252)):>5.2f}   "
              f"maxDD {float(np.max((pk-v)/pk)):>7.2%}")
    print(f"\n  The full-panel CAGR is carried by 2019-2021. Size expectations on the")
    print(f"  walk-forward mean and the RECENT era, not the headline.")


if __name__ == "__main__":
    main()
