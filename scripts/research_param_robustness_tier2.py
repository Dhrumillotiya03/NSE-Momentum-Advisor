"""
Tier-2 parameter robustness: paired 19-window walk-forward per grid value.
(2026-07-17 — Tier-1, research_param_robustness.py, escalated all 5 params;
its rank-based cycle rule proved harsh on grids whose values cluster within
noise, which is itself informative — but the pre-registered escalation path
is this test, so this test decides.)

For each param grid value: run the production engine over the same 19
overlapping 3y windows as walk_forward.py, paired per-window against the
production value.

VERDICT RULE (pre-registered): production value is CURVE-FIT on a param
only if some alternative value BEATS it on Sharpe in >= 14/19 windows
(two-sided sign test ~ p<0.06) AND has a higher mean CAGR across windows.
Anything weaker is cycle noise — consistent with memory
statistical-hygiene-2026-07 (1-2pp deltas between configs are suggestive,
not proven).  Diagnostic only: do NOT retune to any winner here.

Loop warmup pinned to 252 bars for all configs (uniform rebalance dates),
so each 3y window evaluates ~2y — slightly shorter than walk_forward.py's
effective span, uniform across configs.
"""
import numpy as np
import pandas as pd

import backtest_portfolio as bp
import strategy_config as sc
import walk_forward as wf
from research_param_robustness import make_scorer, GRIDS, WARMUP

OUT_CSV = "../data/_research/param_robustness_tier2_2026-07-17.csv"


def run_wf(matrix, index, turnover, windows, lookback=126, hold=21, ma=50,
           vol_win=63, bull_n=10):
    bp.momentum_score = make_scorer(lookback, ma, vol_win)
    bp.LOOKBACK = WARMUP
    bp.HOLD = hold
    sc.REGIME_NAMES["BULL"] = bull_n
    out = []
    for s, e in windows:
        r = wf.run_window(matrix, index, turnover, s, e,
                          engine=bp.run_backtest_laggards_only)
        out.append((r[1], r[2]) if r else (np.nan, np.nan))  # (cagr, sharpe)
    return out


def main():
    orig = (bp.momentum_score, bp.LOOKBACK, bp.HOLD, sc.REGIME_NAMES["BULL"])
    matrix = bp.load_price_matrix()
    index = bp.load_index()
    turnover = bp.load_turnover_matrix(matrix)
    windows = wf.make_windows(matrix, 3, 6)
    print(f"{len(windows)} windows")

    kwname = {"LOOKBACK": "lookback", "HOLD": "hold", "MA_GATE": "ma",
              "VOL_WIN": "vol_win", "BULL_N": "bull_n"}
    rows, verdicts = [], []
    for param, (grid, prod) in GRIDS.items():
        results = {}
        for val in grid:
            kw = dict(lookback=126, hold=21, ma=50, vol_win=63, bull_n=10)
            kw[kwname[param]] = val
            results[val] = run_wf(matrix, index, turnover, windows, **kw)
            m_c = np.nanmean([c for c, _ in results[val]])
            m_s = np.nanmean([s for _, s in results[val]])
            print(f"{param}={val}{'*' if val == prod else ' '}  "
                  f"wf mean CAGR {m_c:+.1%}  mean Sharpe {m_s:.2f}")
            for k, (c, s) in enumerate(results[val]):
                rows.append({"param": param, "value": val, "window": k,
                             "cagr": c, "sharpe": s})
        prod_s = [s for _, s in results[prod]]
        prod_c = np.nanmean([c for c, _ in results[prod]])
        dominated_by = []
        for val in grid:
            if val == prod:
                continue
            alt_s = [s for _, s in results[val]]
            wins = sum(1 for a, p in zip(alt_s, prod_s)
                       if a == a and p == p and a > p)
            alt_c = np.nanmean([c for c, _ in results[val]])
            if wins >= 14 and alt_c > prod_c:
                dominated_by.append((val, wins))
        v = (f"{param}={prod}: " +
             (f"CURVE-FIT SIGNAL — dominated by {dominated_by}"
              if dominated_by else "ROBUST (no alternative wins >=14/19 with higher mean CAGR)"))
        verdicts.append(v)
        print("  ->", v)

    pd.DataFrame(rows).to_csv(OUT_CSV, index=False)
    print(f"\nsaved {OUT_CSV}\n\n========== TIER-2 VERDICTS ==========")
    for v in verdicts:
        print(v)
    bp.momentum_score, bp.LOOKBACK, bp.HOLD, sc.REGIME_NAMES["BULL"] = orig


if __name__ == "__main__":
    main()
