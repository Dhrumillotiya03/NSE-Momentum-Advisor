"""
Drawdown distribution via block bootstrap (consultant item #3).

The 39.0% max-DD figure is the worst peak-to-trough on ONE historical price
path. It is a single draw, not a property of the strategy. This resamples
the actual sequence of 21-day period returns (block bootstrap — blocks of
consecutive periods, not single periods, to preserve the autocorrelation
regime clustering creates: bull runs and bear stretches cluster in time,
so scrambling individual periods would understate real drawdown risk) to
build the DISTRIBUTION of max drawdowns the strategy's realized return
profile is consistent with, then reports percentiles instead of a point
estimate.

This is NOT a claim about future returns (it reuses the historical return
sample, so it inherits any luck/skill in that specific sample) — it answers
a narrower, still-useful question: "given the return behavior this strategy
has actually shown, how unlucky could the ORDERING have been?"

Block length: 6 periods (~6 months) — long enough to keep multi-month
regime runs (e.g. a BEAR stretch) intact within a block, short enough to
generate genuine resampling variety from 128 periods.

Run from scripts/:  python research_drawdown_bootstrap.py
"""
import numpy as np
import pandas as pd

import backtest_portfolio as bp

N_BOOT = 20000
BLOCK_LEN = 6
SEED = 42


def max_drawdown(equity):
    peak = np.maximum.accumulate(equity)
    return np.max((peak - equity) / peak)


def block_bootstrap_dd(returns, n_boot, block_len, seed):
    rng = np.random.default_rng(seed)
    n = len(returns)
    n_blocks_needed = int(np.ceil(n / block_len))
    starts = np.arange(n - block_len + 1)

    dds = np.empty(n_boot)
    finals = np.empty(n_boot)
    for b in range(n_boot):
        chosen_starts = rng.choice(starts, size=n_blocks_needed, replace=True)
        blocks = [returns[s:s + block_len] for s in chosen_starts]
        seq = np.concatenate(blocks)[:n]
        equity = np.cumprod(1 + seq)
        dds[b] = max_drawdown(equity)
        finals[b] = equity[-1]
    return dds, finals


def main():
    matrix = bp.load_price_matrix()
    index = bp.load_index()
    turnover = bp.load_turnover_matrix(matrix)

    print("Running the validated backtest to get the realized return sequence...")
    equity = bp.run_backtest(matrix, index, turnover)
    returns = equity[1:] / equity[:-1] - 1
    n = len(returns)
    hist_dd = max_drawdown(equity)
    hist_total = equity[-1] / equity[0] - 1

    print(f"\n{n} realized 21-day periods (~{n * 21 / 252:.1f} years)")
    print(f"Historical single-path max DD: {hist_dd:.2%}")
    print(f"Historical single-path total return: {hist_total:+.1%}")

    print(f"\nBlock bootstrap: {N_BOOT} resamples, block length {BLOCK_LEN} periods "
          f"(~{BLOCK_LEN} months)...")
    dds, finals = block_bootstrap_dd(returns, N_BOOT, BLOCK_LEN, SEED)

    print(f"\n{'='*60}\nMAX DRAWDOWN DISTRIBUTION (resampled orderings)\n{'='*60}")
    pcts = [5, 10, 25, 50, 75, 90, 95, 99]
    for p in pcts:
        print(f"  p{p:>2d}:  {np.percentile(dds, p):.1%}")
    print(f"\n  mean: {dds.mean():.1%}   worst-of-{N_BOOT}: {dds.max():.1%}")
    print(f"\n  P(max DD > 39.0% i.e. worse than the observed historical path): "
          f"{(dds > hist_dd).mean():.1%}")
    print(f"  P(max DD > 50%): {(dds > 0.50).mean():.1%}")
    print(f"  P(max DD > 60%): {(dds > 0.60).mean():.1%}")

    print(f"\n{'='*60}\nTOTAL RETURN DISTRIBUTION (same resamples, sanity check)\n{'='*60}")
    total_rets = finals - 1
    for p in pcts:
        print(f"  p{p:>2d}:  {np.percentile(total_rets, p):+.0%}")
    print(f"  P(net loss over the full period): {(total_rets < 0).mean():.1%}")

    # ---- sensitivity: shorter block length (less autocorrelation preserved) ----
    print(f"\n{'='*60}\nSENSITIVITY — block length\n{'='*60}")
    for bl in [1, 3, 6, 12]:
        d, _ = block_bootstrap_dd(returns, 5000, bl, SEED)
        print(f"  block={bl:2d} periods: median DD {np.median(d):.1%}, "
              f"p95 {np.percentile(d, 95):.1%}, p99 {np.percentile(d, 99):.1%}")

    # ---- compare against the un-boosted exposure config ----
    print(f"\n{'='*60}\nOLD (pre-boost) EXPOSURE — same bootstrap, for comparison\n{'='*60}")
    import strategy_config as sc
    old_exposure = dict(sc.REGIME_EXPOSURE)
    sc.REGIME_EXPOSURE["BULL"], sc.REGIME_EXPOSURE["SIDEWAYS"] = 0.95, 0.60
    sc.REGIME_EXPOSURE["BEAR"], sc.REGIME_EXPOSURE["UNKNOWN"] = 0.30, 0.60
    equity_old = bp.run_backtest(matrix, index, turnover)
    sc.REGIME_EXPOSURE.update(old_exposure)  # restore
    returns_old = equity_old[1:] / equity_old[:-1] - 1
    dds_old, _ = block_bootstrap_dd(returns_old, N_BOOT, BLOCK_LEN, SEED)
    print(f"  old exposure  — median DD {np.median(dds_old):.1%}, p95 {np.percentile(dds_old, 95):.1%}, p99 {np.percentile(dds_old, 99):.1%}")
    print(f"  boosted (now) — median DD {np.median(dds):.1%}, p95 {np.percentile(dds, 95):.1%}, p99 {np.percentile(dds, 99):.1%}")


if __name__ == "__main__":
    main()
