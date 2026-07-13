"""
International equity sleeve study (Nasdaq-100 INR ETF, MON100).

QUESTION: after adopting the 15% gold sleeve (gold-sleeve-2026-07), the
book is still 100% India (85% Indian momentum + gold priced in INR).
An INR-denominated Nasdaq-100 ETF adds (a) a different equity market and
(b) implicit USD exposure — the rupee tends to DEPRECIATE in Indian risk-off
episodes, so the INR value of a US asset is partially anti-correlated with
Indian equity stress exactly when it matters. Same methodology as the gold
study, same statistical-hygiene bar.

HONESTY CAVEATS, up front:
  - Nasdaq 2015-2026 was itself an exceptional decade (plus ~3-4%/yr INR
    depreciation tailwind). The durable claim is the CORRELATION/currency
    mechanism, not the return level. Do not size this sleeve off its
    backtested CAGR, same rule as gold.
  - Stacking sleeves that each had a great decade makes any blend look
    good. The bar here is: does the blend beat the CURRENT PRODUCTION
    (85/15 mom/gold) on the paired bootstrap and across walk-forward
    windows, not just on the full-history point.
  - Tax: MON100 is a non-equity-oriented fund for tax purposes (LTCG 12.5%
    only after 24m under current rules; STCG slab) — worse than domestic
    equity. Flag for research_net_returns if adopted.

Blend grid: (momentum, gold, intl) with the current production (0.85,
0.15, 0) as baseline. Run from scripts/:  python research_intl_sleeve.py
"""
import numpy as np
import pandas as pd

import strategy_config as sc
import backtest_portfolio as bp
import research_lowvol_sleeve as rl   # reuse metrics/blend/window/bootstrap machinery

INTL_PATH = "../data/etf_data/MON100.NS.csv"

# (momentum, gold, intl)
BLENDS = [
    (0.85, 0.15, 0.00),   # current production baseline
    (0.80, 0.10, 0.10),
    (0.75, 0.15, 0.10),
    (0.70, 0.15, 0.15),
    (0.70, 0.10, 0.20),
    (0.60, 0.20, 0.20),
]


def load_etf_period_returns(path, matrix):
    """Same alignment + spike guard as bp.load_gold_period_returns."""
    df = pd.read_csv(path, parse_dates=["Date"], low_memory=False)
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df = df[pd.to_datetime(df["Date"], errors="coerce").notna()]
    df = df.dropna(subset=["Close"]).sort_values("Date").set_index("Date")["Close"]
    med = df.rolling(11, center=True, min_periods=3).median()
    ratio = df / med
    df = df[(ratio < 3) & (ratio > 1 / 3)]
    s = df.reindex(matrix.index).ffill()

    marks = []
    for i in rl.rebalance_grid(matrix):
        marks.append(s.iloc[min(i + sc.HOLD, len(matrix) - 1)])
    marks = pd.Series(marks).ffill().bfill()
    return marks.values[1:] / marks.values[:-1] - 1


def main():
    matrix = bp.load_price_matrix()
    index = bp.load_index()
    turnover = bp.load_turnover_matrix(matrix)

    print(f"{'='*74}\nPART A — sleeves standalone + correlations\n{'='*74}")
    eq_mom = bp.run_backtest_laggards_only(matrix, index, turnover)
    r_mom = rl.period_returns(eq_mom)
    r_gold = bp.load_gold_period_returns(matrix)
    r_intl = load_etf_period_returns(INTL_PATH, matrix)
    n = min(len(r_mom), len(r_gold), len(r_intl))
    r_mom, r_gold, r_intl = r_mom[:n], r_gold[:n], r_intl[:n]

    for name, r in [("momentum sleeve (w/ cash yield)", r_mom),
                    ("gold (GOLDBEES)", r_gold),
                    ("intl (MON100 Nasdaq-100 INR)", r_intl)]:
        c, s, d = rl.metrics(r)
        print(f"  {name:32s} CAGR {c:7.2%}  Sharpe {s:5.2f}  MaxDD {d:6.2%}")

    corr = np.corrcoef(np.vstack([r_mom, r_gold, r_intl]))
    print(f"\n  21d-period return correlations:")
    print(f"    momentum vs intl: {corr[0,2]:+.2f}")
    print(f"    gold     vs intl: {corr[1,2]:+.2f}")
    print(f"    momentum vs gold: {corr[0,1]:+.2f}")

    sleeves = [r_mom, r_gold, r_intl]
    print(f"\n{'='*74}\nPART B — blend grid (inter-sleeve rebal cost charged)\n{'='*74}")
    print(f"  {'mom':>5s} {'gold':>5s} {'intl':>5s}   {'CAGR':>8s} {'Sharpe':>7s} {'MaxDD':>8s}")
    blend_series = {}
    for wts in BLENDS:
        r = rl.blend_returns(sleeves, wts)
        blend_series[wts] = r
        c, s, d = rl.metrics(r)
        print(f"  {wts[0]:5.0%} {wts[1]:5.0%} {wts[2]:5.0%}   {c:8.2%} {s:7.2f} {d:8.2%}")

    print(f"\n{'='*74}\nPART C — rolling 3y windows vs CURRENT PRODUCTION (85/15/0)\n{'='*74}")
    base_windows = rl.rolling_windows(blend_series[BLENDS[0]])
    print(f"  ({len(base_windows)} overlapping 3y windows, step 6 periods)")
    print(f"  {'blend':>16s} {'wfSharpe mean':>14s} {'wfCAGR mean':>12s} "
          f"{'Sharpe>base':>12s} {'DD<base':>8s} {'CAGR>base':>10s}")
    for wts in BLENDS[1:]:
        wins = rl.rolling_windows(blend_series[wts])
        sh_w, cg_w, sh_b, dd_b, cg_b = [], [], 0, 0, 0
        for wb, wx in zip(base_windows, wins):
            cb, sb, db = rl.metrics(wb)
            cx, sx, dx = rl.metrics(wx)
            sh_w.append(sx); cg_w.append(cx)
            sh_b += sx > sb; dd_b += dx < db; cg_b += cx > cb
        label = f"{wts[0]:.0%}/{wts[1]:.0%}/{wts[2]:.0%}"
        print(f"  {label:>16s} {np.mean(sh_w):14.2f} {np.mean(cg_w):12.2%} "
              f"{sh_b:>3d}/{len(base_windows):<3d}    {dd_b:>3d}/{len(base_windows):<3d}  "
              f"{cg_b:>3d}/{len(base_windows)}")
    bsh = [rl.metrics(wb)[1] for wb in base_windows]
    bcg = [rl.metrics(wb)[0] for wb in base_windows]
    print(f"  {'base 85/15/0':>16s} {np.mean(bsh):14.2f} {np.mean(bcg):12.2%}")

    print(f"\n{'='*74}\nPART D — paired block-bootstrap vs current production\n{'='*74}")
    for wts in BLENDS[1:]:
        sh_d, cg_d = rl.paired_bootstrap_delta(blend_series[BLENDS[0]], blend_series[wts])
        label = f"{wts[0]:.0%}/{wts[1]:.0%}/{wts[2]:.0%}"
        sig_s = np.percentile(sh_d, 2.5) > 0 or np.percentile(sh_d, 97.5) < 0
        sig_c = np.percentile(cg_d, 2.5) > 0 or np.percentile(cg_d, 97.5) < 0
        print(f"  {label:>16s}  Sharpe delta {sh_d.mean():+.2f} "
              f"[{np.percentile(sh_d,2.5):+.2f},{np.percentile(sh_d,97.5):+.2f}] "
              f"P={(sh_d>0).mean():4.0%} {'SIG' if sig_s else '   '} | "
              f"CAGR delta {cg_d.mean():+.2%} "
              f"[{np.percentile(cg_d,2.5):+.2%},{np.percentile(cg_d,97.5):+.2%}] "
              f"P={(cg_d>0).mean():4.0%} {'SIG' if sig_c else ''}")


if __name__ == "__main__":
    main()
