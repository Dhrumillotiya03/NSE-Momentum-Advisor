"""
MONTH-END FULL LIQUIDATION vs LAGGARDS-ONLY — PREREG_month_end_liquidation.md.

The user's mandate empties the whole book every last Tuesday. Production holds
names that re-qualify. This measures the GROSS cost of the mandate on the REAL
last-Tuesday calendar with a true one-difference comparison
(run_backtest_laggards_only liquidate_all=True vs False — same conviction
sizing, sector cap, stop, everything). The tax side is in
research_monthly_close_cost.py (lot-level FIFO); this script is gross only and
says so.

Usage (from scripts/):
    python research_month_end_liquidation.py
"""
import numpy as np
import pandas as pd

import strategy_config as sc
from backtest_portfolio import (load_price_matrix, load_index, load_turnover_matrix,
                                run_backtest_laggards_only, last_tuesday_rebalance_idx,
                                performance)
from walk_forward import make_windows

BLOCK_LEN, N_BOOT, SEED = 6, 2000, 42


def real_idx(matrix):
    return [x for x in last_tuesday_rebalance_idx(matrix)
            if sc.LOOKBACK + 21 <= x < len(matrix) - 1]


def daily_dd(marks):
    s = pd.Series({k: v for k, v in marks}).sort_index()
    v = s.values.astype(float)
    pk = np.maximum.accumulate(v)
    return float(np.max((pk - v) / pk))


def window_run(sub, index, subtv, local, liq):
    dm = []
    eq = run_backtest_laggards_only(sub, index, subtv, rebalance_idx=local,
                                    daily_marks=dm, liquidate_all=liq)
    if len(eq) < 2 or not dm:
        return None
    s = pd.Series({k: v for k, v in dm}).sort_index()
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    gaps = np.diff(local) if len(local) > 1 else [21]
    _, cagr, sharpe, _, _, _ = performance(eq, years=yrs, avg_period_days=float(np.mean(gaps)))
    return cagr, sharpe, daily_dd(dm)


def boot_ci(deltas):
    rng = np.random.default_rng(SEED)
    d = np.asarray(deltas); n = len(d)
    nb = int(np.ceil(n / BLOCK_LEN))
    out = []
    for _ in range(N_BOOT):
        i = np.concatenate([np.arange(s, s + BLOCK_LEN) for s in rng.integers(0, n, nb)]) % n
        out.append(d[i[:n]].mean())
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def main():
    matrix = load_price_matrix(); index = load_index()
    turnover = load_turnover_matrix(matrix)
    ridx = real_idx(matrix)
    print("=" * 76)
    print("MONTH-END FULL LIQUIDATION vs LAGGARDS-ONLY (gross; real last-Tuesday grid)")
    print("=" * 76)

    # full panel
    for liq, lab in [(False, "laggards-only (production)"), (True, "full liquidation (mandate)")]:
        dm = []
        eq = run_backtest_laggards_only(matrix, index, turnover, rebalance_idx=ridx,
                                        daily_marks=dm, liquidate_all=liq)
        s = pd.Series({k: v for k, v in dm}).sort_index()
        yrs = (s.index[-1] - s.index[0]).days / 365.25
        _, cagr, sharpe, _, _, _ = performance(eq, years=yrs,
                                               avg_period_days=float(np.mean(np.diff(ridx))))
        print(f"  full panel  {lab:>30}  CAGR {cagr:+.2%}  Sharpe {sharpe:.2f}  "
              f"maxDD {daily_dd(dm):.2%}")

    # walk-forward
    windows = make_windows(matrix, window_years=3, step_months=6)
    print(f"\n  walk-forward — {len(windows)} x 3y windows\n")
    print(f"  {'window':>25} {'laggards':>10} {'liquidate':>10} {'delta':>8}")
    dL, dH = [], []
    for a, b in windows:
        sub = matrix[(matrix.index >= a) & (matrix.index <= b)]
        sub = sub.loc[:, sub.isna().mean() <= 0.20]
        if len(sub) < 300:
            continue
        subtv = turnover.reindex(sub.index)
        wd = set(sub.index)
        local = sorted(sub.index.get_loc(matrix.index[g]) for g in ridx if matrix.index[g] in wd)
        local = [x for x in local if sc.LOOKBACK + 21 <= x < len(sub) - 1]
        rL = window_run(sub, index, subtv, local, False)
        rH = window_run(sub, index, subtv, local, True)
        if rL is None or rH is None:
            continue
        dL.append(rL[0]); dH.append(rH[0])
        print(f"  {str(a.date())+'..'+str(b.date()):>25} {rL[0]:>+10.2%} {rH[0]:>+10.2%} "
              f"{rH[0]-rL[0]:>+8.2%}", flush=True)
    dL = np.array(dL); dH = np.array(dH)
    delta = dH - dL
    lo, hi = boot_ci(delta)
    print(f"\n  mean CAGR   laggards {dL.mean():+.2%}   liquidate {dH.mean():+.2%}")
    print(f"  mean delta (liquidate - laggards)  {delta.mean():+.2%}   "
          f"95% CI [{lo:+.2%}, {hi:+.2%}]")
    print(f"  liquidate wins {int((delta > 0).sum())}/{len(delta)} windows")
    if lo <= 0 <= hi:
        print(f"\n  GROSS: the two are within timing-luck noise ({delta.mean():+.2%}, CI spans 0).")
        print(f"  The mandate is close to free BEFORE tax. Tax is the real question —")
        print(f"  see research_monthly_close_cost.py (lot-level FIFO): laggards-only")
        print(f"  defers STCG on held names, worth the difference there.")
    else:
        print(f"\n  GROSS delta CI excludes zero. Report {delta.mean():+.2%}/yr to the user")
        print(f"  as the cost of the full-liquidation discipline, before tax on top.")


if __name__ == "__main__":
    main()
