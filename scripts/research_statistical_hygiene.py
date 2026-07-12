"""
Statistical hygiene (consultant item #7): confidence intervals on the
headline metrics, and a direct test of whether this session's adopted
changes are distinguishable from noise given ~128 periods (~10.7y) of
data — or whether they're within the standard error and were adopted on
vibes dressed up as evidence.

With n=128 non-independent (autocorrelated/overlapping-regime) periods,
a point Sharpe of "0.80 vs 0.78" is not evidence of anything by itself.
This script:

  PART A — analytic Sharpe SE (Lo 2002 autocorrelation-adjusted formula,
  not the naive iid sqrt(n) formula, since strategy returns are NOT iid —
  21-day rebalance periods within the same regime are correlated) plus a
  block-bootstrap Sharpe CI (reuses the block-bootstrap machinery from
  research_drawdown_bootstrap.py) for cross-validation of the analytic SE.

  PART B — paired comparison of EACH adopted decision this session against
  its predecessor, on the SAME data (paired bootstrap: resample rebalance
  dates, recompute BOTH configs' Sharpe on the identical resampled blocks,
  look at the DISTRIBUTION of the paired difference — not two independent
  CIs, which would be too conservative and miss that both configs share
  the same underlying return-generating process):
    - BEAR=2 vs BEAR=4 (concentration-risk-2026-07)
    - hard_close vs laggards_only (monthly-close-cost-2026-07)
    - pre-boost vs post-boost REGIME_EXPOSURE (research-verdicts-2026-07)

Run from scripts/:  python research_statistical_hygiene.py
"""
import numpy as np
import pandas as pd

import strategy_config as sc
import backtest_portfolio as bp

N_BOOT = 5000
BLOCK_LEN = 6
SEED = 42


def sharpe_of(equity):
    p = bp.performance(equity)
    return None if p is None else p[2]


def cagr_of(equity):
    p = bp.performance(equity)
    return None if p is None else p[1]


def lo_sharpe_se(returns, periods_per_year):
    """Lo (2002) autocorrelation-adjusted Sharpe standard error. Naive iid
    SE = sqrt((1+0.5*SR^2)/n) understates SE when returns are positively
    autocorrelated (which 21-day momentum rebalances plausibly are, since
    regime persistence spans multiple rebalances)."""
    n = len(returns)
    mu, sigma = returns.mean(), returns.std(ddof=1)
    sr_period = mu / sigma if sigma > 0 else 0
    # autocorrelation-adjusted variance inflation factor
    max_lag = min(20, n // 4)
    acf = [pd.Series(returns).autocorr(lag=k) for k in range(1, max_lag + 1)]
    acf = [a for a in acf if not np.isnan(a)]
    q = periods_per_year  # annualization periods (252/HOLD)
    infl = 1 + 2 * sum((1 - (k + 1) / q) * a for k, a in enumerate(acf) if k + 1 < q)
    infl = max(infl, 0.1)  # guard against pathological negative inflation
    se_period = np.sqrt((1 + 0.5 * sr_period ** 2) / n * infl)
    se_annual = se_period * np.sqrt(q)
    return se_annual, infl


def block_bootstrap_sharpe(returns, n_boot, block_len, seed, periods_per_year):
    rng = np.random.default_rng(seed)
    n = len(returns)
    n_blocks = int(np.ceil(n / block_len))
    starts = np.arange(n - block_len + 1)
    sharpes = np.empty(n_boot)
    for b in range(n_boot):
        chosen = rng.choice(starts, size=n_blocks, replace=True)
        seq = np.concatenate([returns[s:s + block_len] for s in chosen])[:n]
        mu, sigma = seq.mean(), seq.std(ddof=1)
        sharpes[b] = (mu / sigma * np.sqrt(periods_per_year)) if sigma > 0 else 0
    return sharpes


def part_a(matrix, index, turnover):
    print(f"{'='*70}\nPART A — Sharpe confidence interval on the current production config\n{'='*70}")
    eq = bp.run_backtest_laggards_only(matrix, index, turnover)
    returns = eq[1:] / eq[:-1] - 1
    q = 252 / sc.HOLD
    point_sharpe = returns.mean() / returns.std(ddof=1) * np.sqrt(q)

    se_naive = np.sqrt((1 + 0.5 * (point_sharpe / np.sqrt(q)) ** 2) / len(returns)) * np.sqrt(q)
    se_lo, infl = lo_sharpe_se(returns, q)
    boot = block_bootstrap_sharpe(returns, N_BOOT, BLOCK_LEN, SEED, q)

    print(f"n={len(returns)} periods (~{len(returns)*sc.HOLD/252:.1f} years)")
    print(f"point Sharpe: {point_sharpe:.2f}")
    print(f"\nnaive iid SE:              {se_naive:.2f}  ->  95% CI [{point_sharpe-1.96*se_naive:.2f}, {point_sharpe+1.96*se_naive:.2f}]")
    print(f"Lo(2002) autocorr-adj SE:  {se_lo:.2f}  (inflation factor {infl:.2f}x)  ->  95% CI [{point_sharpe-1.96*se_lo:.2f}, {point_sharpe+1.96*se_lo:.2f}]")
    print(f"block-bootstrap (n={N_BOOT}): mean {boot.mean():.2f}  ->  95% CI [{np.percentile(boot,2.5):.2f}, {np.percentile(boot,97.5):.2f}]")
    print(f"\nP(true Sharpe <= 0, i.e. this is indistinguishable from a coin flip): "
          f"{(boot <= 0).mean():.1%}  (bootstrap)")
    print(f"P(true Sharpe <= 0.5, i.e. \"weakly positive at best\"): {(boot <= 0.5).mean():.1%}")


def paired_bootstrap(matrix, index, turnover, engine_a, engine_b, label_a, label_b,
                     n_boot=2000, block_len=6, seed=42):
    """Resample REBALANCE-DATE blocks once, apply the SAME resampled index
    to both engines' return series, compare paired Sharpe/CAGR differences.
    This requires both engines to produce equity curves of the same length/
    alignment -- true for hard_close vs laggards_only and for exposure/
    REGIME_NAMES variants (all iterate the identical rebalance grid)."""
    eq_a = engine_a(matrix, index, turnover)
    eq_b = engine_b(matrix, index, turnover)
    n = min(len(eq_a), len(eq_b))
    ret_a = eq_a[1:n] / eq_a[:n-1] - 1
    ret_b = eq_b[1:n] / eq_b[:n-1] - 1

    q = 252 / sc.HOLD
    sharpe_a = ret_a.mean() / ret_a.std(ddof=1) * np.sqrt(q)
    sharpe_b = ret_b.mean() / ret_b.std(ddof=1) * np.sqrt(q)
    cagr_a = (eq_a[-1] / eq_a[0]) ** (1 / (len(eq_a) * sc.HOLD / 252)) - 1
    cagr_b = (eq_b[-1] / eq_b[0]) ** (1 / (len(eq_b) * sc.HOLD / 252)) - 1

    rng = np.random.default_rng(seed)
    n_r = len(ret_a)
    n_blocks = int(np.ceil(n_r / block_len))
    starts = np.arange(n_r - block_len + 1)
    sharpe_diffs = np.empty(n_boot)
    cagr_diffs = np.empty(n_boot)
    for b in range(n_boot):
        chosen = rng.choice(starts, size=n_blocks, replace=True)
        idx = np.concatenate([np.arange(s, s + block_len) for s in chosen])[:n_r]
        ra, rb = ret_a[idx], ret_b[idx]
        sa = ra.mean() / ra.std(ddof=1) * np.sqrt(q) if ra.std(ddof=1) > 0 else 0
        sb = rb.mean() / rb.std(ddof=1) * np.sqrt(q) if rb.std(ddof=1) > 0 else 0
        sharpe_diffs[b] = sb - sa
        ea, eb = np.cumprod(1 + ra), np.cumprod(1 + rb)
        yrs = len(ra) * sc.HOLD / 252
        cagr_diffs[b] = (eb[-1] ** (1 / yrs) - 1) - (ea[-1] ** (1 / yrs) - 1)

    print(f"\n{'-'*70}\n{label_a}  vs  {label_b}\n{'-'*70}")
    print(f"  point:  Sharpe {sharpe_a:.2f} -> {sharpe_b:.2f} (delta {sharpe_b-sharpe_a:+.2f})   "
          f"CAGR {cagr_a:.2%} -> {cagr_b:.2%} (delta {cagr_b-cagr_a:+.2%})")
    print(f"  paired bootstrap SHARPE delta: mean {sharpe_diffs.mean():+.2f}, "
          f"95% CI [{np.percentile(sharpe_diffs,2.5):+.2f}, {np.percentile(sharpe_diffs,97.5):+.2f}]  "
          f"P(B better)={(sharpe_diffs > 0).mean():.1%}")
    sharpe_sig = np.percentile(sharpe_diffs, 2.5) > 0 or np.percentile(sharpe_diffs, 97.5) < 0
    print(f"    -> {'DISTINGUISHABLE at 95%' if sharpe_sig else 'within noise at 95% confidence'}")
    print(f"  paired bootstrap CAGR delta:   mean {cagr_diffs.mean():+.2%}, "
          f"95% CI [{np.percentile(cagr_diffs,2.5):+.2%}, {np.percentile(cagr_diffs,97.5):+.2%}]  "
          f"P(B better)={(cagr_diffs > 0).mean():.1%}")
    cagr_sig = np.percentile(cagr_diffs, 2.5) > 0 or np.percentile(cagr_diffs, 97.5) < 0
    print(f"    -> {'DISTINGUISHABLE at 95%' if cagr_sig else 'within noise at 95% confidence'}")


def part_b(matrix, index, turnover):
    print(f"\n{'='*70}\nPART B — were this session's adopted decisions actually distinguishable from noise?\n{'='*70}")

    # 1. BEAR=2 vs BEAR=4
    def engine_bear2(m, i, t):
        old = sc.REGIME_NAMES["BEAR"]
        sc.REGIME_NAMES["BEAR"] = 2
        try:
            return bp.run_backtest_laggards_only(m, i, t)
        finally:
            sc.REGIME_NAMES["BEAR"] = old

    def engine_bear4(m, i, t):
        return bp.run_backtest_laggards_only(m, i, t)  # current config has BEAR=4

    paired_bootstrap(matrix, index, turnover, engine_bear2, engine_bear4,
                     "BEAR=2 (old)", "BEAR=4 (adopted)")

    # 2. hard_close vs laggards_only
    paired_bootstrap(matrix, index, turnover, bp.run_backtest, bp.run_backtest_laggards_only,
                     "hard_close (old)", "laggards_only (adopted)")

    # 3. pre-boost vs post-boost exposure
    def engine_preboost(m, i, t):
        old = dict(sc.REGIME_EXPOSURE)
        sc.REGIME_EXPOSURE.update({"BULL": 0.95, "SIDEWAYS": 0.60, "BEAR": 0.30, "UNKNOWN": 0.60})
        try:
            return bp.run_backtest_laggards_only(m, i, t)
        finally:
            sc.REGIME_EXPOSURE.update(old)

    def engine_postboost(m, i, t):
        return bp.run_backtest_laggards_only(m, i, t)  # current config is boosted

    paired_bootstrap(matrix, index, turnover, engine_preboost, engine_postboost,
                     "exposure pre-boost (old)", "exposure post-boost (adopted)")


if __name__ == "__main__":
    matrix = bp.load_price_matrix()
    index = bp.load_index()
    turnover = bp.load_turnover_matrix(matrix)
    part_a(matrix, index, turnover)
    part_b(matrix, index, turnover)
