"""
Deployment-gate report — paper months vs the backtest distribution.

The deployment gate (memory paper-trading-loop) is: 3-6 months of forward
paper trading whose monthly returns are CONSISTENT with the walk-forward /
backtest distribution before real capital. "Consistent" was a manual
judgment; this script makes it a number: each completed paper month is
placed at its percentile of the production engine's historical 21d-period
return distribution (full 2015-2026, gold+intl blend — the same engine the
paper trader mirrors).

Reading it:
  - months between p10 and p90: normal — the paper book behaves like the
    backtest said it would.
  - a month below p5 (or two below p10): the live signal path may diverge
    from the backtest (data issue, execution gap, or regime the history
    never saw) — investigate BEFORE deploying, don't average it away.
  - months above p90 prove nothing good either — they're luck, not
    validation. Consistency, not outperformance, is what's being tested.

Run from scripts/ (monthly, or whenever):  python gate_report.py
"""
import os

import numpy as np
import pandas as pd

import backtest_portfolio as bp

EQUITY_PATH = "../data/paper_equity.csv"
MIN_SESSIONS = 15   # a "month" with fewer logged sessions is partial — skip


def paper_monthly_returns():
    if not os.path.exists(EQUITY_PATH):
        return None
    eq = pd.read_csv(EQUITY_PATH, parse_dates=["date"]).set_index("date")["equity"]
    eq = eq[~eq.index.duplicated(keep="last")]
    out = []
    for period, grp in eq.groupby(eq.index.to_period("M")):
        if len(grp) < MIN_SESSIONS:
            continue
        prev_idx = eq.index[eq.index < grp.index[0]]
        start = eq.loc[prev_idx[-1]] if len(prev_idx) else grp.iloc[0]
        out.append((str(period), grp.iloc[-1] / start - 1, len(grp)))
    return out


def main():
    months = paper_monthly_returns()
    if not months:
        print("[gate] no completed paper months yet (need ~15+ logged sessions in a month)")
        return

    matrix = bp.load_price_matrix()
    index = bp.load_index()
    turnover = bp.load_turnover_matrix(matrix)
    eq = bp.run_backtest_gold_blend(matrix, index, turnover)
    ref = eq[1:] / eq[:-1] - 1   # historical 21d-period returns, production engine

    print(f"DEPLOYMENT GATE — paper months vs backtest 21d-return distribution "
          f"(n={len(ref)} periods)")
    print(f"  reference: p5 {np.percentile(ref,5):+.2%}  p10 {np.percentile(ref,10):+.2%}  "
          f"median {np.percentile(ref,50):+.2%}  p90 {np.percentile(ref,90):+.2%}")
    print(f"\n  {'month':>8s} {'return':>8s} {'pctile':>7s}  verdict")
    n_low = 0
    for label, r, n_sess in months:
        pct = (ref < r).mean() * 100
        if pct < 5:
            verdict, n_low = "BELOW p5 — investigate before deploying", n_low + 1
        elif pct < 10:
            verdict, n_low = "below p10 — watch", n_low + 1
        elif pct > 90:
            verdict = "above p90 (luck, not validation)"
        else:
            verdict = "consistent"
        print(f"  {label:>8s} {r:+8.2%} {pct:6.0f}%  {verdict}")

    n = len(months)
    print(f"\n  {n} completed month(s) — gate needs 3-6 consistent months.")
    if n_low >= 2:
        print("  WARNING: 2+ months in the bottom decile — the live path likely does")
        print("  NOT match the backtest; find the divergence before real capital.")
    elif n >= 3 and n_low == 0:
        print("  Gate progressing: no bottom-decile months so far.")


if __name__ == "__main__":
    main()
