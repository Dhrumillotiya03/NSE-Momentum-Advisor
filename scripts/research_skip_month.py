"""
Skip-month momentum test (the academic '12-2' construction).

HYPOTHESIS (literature-causal, not mined): the most recent ~month of
returns contains SHORT-TERM REVERSAL, not momentum — published momentum
factors (Jegadeesh-Titman, Fama-French UMD) measure momentum over months
t-12..t-2, skipping the last month. Our score uses the full 126d window
including the last 21 days. If the reversal effect holds on NSE, measuring
the momentum legs at i-21 instead of i should improve selection.

One variable: skip_days on run_backtest_laggards_only's momentum legs
(6m and 3m return legs both end at i-skip; entry price, 50MA trend filter,
vol sizing all unchanged and still use data through i). Tested on the
MOMENTUM SLEEVE only (sleeves are unaffected by scoring).

Per statistical-hygiene-2026-07: point deltas of 1-2pp CAGR won't clear
95% on ~128 periods; the causal literature backing is why this is worth
one clean test, but adoption still requires the walk-forward distribution
to agree, not just the full-history point.

Run from scripts/:  python research_skip_month.py
"""
import numpy as np

import strategy_config as sc
import backtest_portfolio as bp
import research_lowvol_sleeve as rl

SKIPS = [0, 10, 21]


def wf_windows_metrics(returns):
    return [rl.metrics(w) for w in rl.rolling_windows(returns)]


def main():
    matrix = bp.load_price_matrix()
    index = bp.load_index()
    turnover = bp.load_turnover_matrix(matrix)

    series = {}
    print(f"{'='*70}\nFull-history momentum sleeve by skip_days\n{'='*70}")
    for skip in SKIPS:
        eq = bp.run_backtest_laggards_only(matrix, index, turnover, skip_days=skip)
        r = rl.period_returns(eq)
        series[skip] = r
        c, s, d = rl.metrics(r)
        print(f"  skip={skip:2d}d: CAGR {c:7.2%}  Sharpe {s:5.2f}  MaxDD {d:6.2%}")

    base = series[0]
    print(f"\n{'='*70}\nRolling 3y windows vs skip=0\n{'='*70}")
    base_w = wf_windows_metrics(base)
    for skip in SKIPS[1:]:
        w = wf_windows_metrics(series[skip])
        sh_better = sum(x[1] > b[1] for x, b in zip(w, base_w))
        cg_better = sum(x[0] > b[0] for x, b in zip(w, base_w))
        print(f"  skip={skip:2d}d: wf mean Sharpe {np.mean([x[1] for x in w]):.2f} "
              f"(base {np.mean([b[1] for b in base_w]):.2f}) | "
              f"Sharpe better in {sh_better}/{len(w)}, CAGR better in {cg_better}/{len(w)}")

    print(f"\n{'='*70}\nPaired block-bootstrap vs skip=0\n{'='*70}")
    for skip in SKIPS[1:]:
        n = min(len(base), len(series[skip]))
        sh_d, cg_d = rl.paired_bootstrap_delta(base[:n], series[skip][:n])
        sig = np.percentile(sh_d, 2.5) > 0 or np.percentile(sh_d, 97.5) < 0
        print(f"  skip={skip:2d}d: Sharpe delta {sh_d.mean():+.2f} "
              f"[{np.percentile(sh_d,2.5):+.2f},{np.percentile(sh_d,97.5):+.2f}] "
              f"P(better)={(sh_d>0).mean():4.0%} {'SIG' if sig else ''} | "
              f"CAGR delta {cg_d.mean():+.2%} "
              f"[{np.percentile(cg_d,2.5):+.2%},{np.percentile(cg_d,97.5):+.2%}] "
              f"P={(cg_d>0).mean():4.0%}")


if __name__ == "__main__":
    main()
