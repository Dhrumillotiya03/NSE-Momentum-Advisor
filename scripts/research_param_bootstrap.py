"""
Amber-param bootstrap — follow-up to research_param_robustness(_tier2).py.

Tier-2 left two BOUNDARY curve-fit signals (each at exactly 14/19 windows,
the minimum, with 24 comparisons at p~0.06 — i.e. within chance):
    LOOKBACK 126 vs 147
    VOL_WIN  63  vs 126
This script applies the project's standard adoption bar (paired
moving-block bootstrap on period returns, the same test the gold/intl
sleeves cleared): both configs run on the IDENTICAL rebalance grid
(warmup pinned to 252), and the delta distribution of Sharpe and CAGR is
bootstrapped over resampled blocks of the SAME periods.

VERDICT RULE (pre-registered): the alternative is REAL only if the 95% CI
of its Sharpe delta excludes 0. IMPORTANT EXTRA CAVEAT vs the sleeve
tests: these alternatives were SELECTED by scanning a grid on this same
data (post-hoc selection), unlike the sleeves (hypothesis-first) — so even
a clean CI here is weaker evidence than the sleeves' CIs were, and
adoption would additionally need the mechanism argument + ideally fresh
out-of-sample confirmation (the paper/sim months). Default action on ANY
outcome: change nothing now.
"""
import numpy as np

import backtest_portfolio as bp
import strategy_config as sc
from research_param_robustness import make_scorer, WARMUP

N_BOOT = 2000
BLOCK = 6          # ~6 months of 21d periods per block
SEED = 7


def period_returns(matrix, index, turnover, lookback, vol_win):
    bp.momentum_score = make_scorer(lookback, 50, vol_win)
    bp.LOOKBACK = WARMUP
    bp.HOLD = 21
    eq = bp.run_backtest_laggards_only(matrix, index, turnover)
    return eq[1:] / eq[:-1] - 1


def perf(r):
    ann = (np.prod(1 + r)) ** (252 / (21 * len(r))) - 1
    vol = np.std(r) * np.sqrt(252 / 21)
    return ann, ann / vol if vol > 0 else 0.0


def paired_bootstrap(r_base, r_alt, rng):
    n = min(len(r_base), len(r_alt))
    r_base, r_alt = r_base[:n], r_alt[:n]
    d_sharpe, d_cagr = [], []
    n_blocks = int(np.ceil(n / BLOCK))
    for _ in range(N_BOOT):
        starts = rng.integers(0, n - BLOCK + 1, size=n_blocks)
        idx = np.concatenate([np.arange(s, s + BLOCK) for s in starts])[:n]
        ca, sa = perf(r_alt[idx])
        cb, sb = perf(r_base[idx])
        d_sharpe.append(sa - sb)
        d_cagr.append(ca - cb)
    return np.array(d_sharpe), np.array(d_cagr)


def main():
    orig = (bp.momentum_score, bp.LOOKBACK, bp.HOLD)
    matrix = bp.load_price_matrix()
    index = bp.load_index()
    turnover = bp.load_turnover_matrix(matrix)
    rng = np.random.default_rng(SEED)

    r_base = period_returns(matrix, index, turnover, 126, 63)
    cb, sb = perf(r_base)
    print(f"baseline LOOKBACK=126/VOL_WIN=63: CAGR {cb:+.2%}  Sharpe {sb:.2f}  "
          f"({len(r_base)} periods)\n")

    for label, lb, vw in [("LOOKBACK 147", 147, 63), ("VOL_WIN 126", 126, 126)]:
        r_alt = period_returns(matrix, index, turnover, lb, vw)
        ca, sa = perf(r_alt)
        ds, dc = paired_bootstrap(r_base, r_alt, rng)
        lo_s, hi_s = np.percentile(ds, [2.5, 97.5])
        lo_c, hi_c = np.percentile(dc, [2.5, 97.5])
        sig = lo_s > 0
        print(f"{label}: point CAGR {ca:+.2%} Sharpe {sa:.2f}")
        print(f"  Sharpe delta {np.mean(ds):+.3f}  95% CI [{lo_s:+.3f}, {hi_s:+.3f}]  "
              f"P(alt better) {np.mean(ds > 0):.0%}")
        print(f"  CAGR   delta {np.mean(dc):+.2%}  95% CI [{lo_c:+.2%}, {hi_c:+.2%}]")
        print(f"  => {'SIGNIFICANT (but post-hoc-selected — see docstring caveat)' if sig else 'NOT significant — boundary signal was cycle noise'}\n")

    bp.momentum_score, bp.LOOKBACK, bp.HOLD = orig


if __name__ == "__main__":
    main()
