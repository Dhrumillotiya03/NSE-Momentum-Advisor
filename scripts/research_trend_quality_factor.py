"""
Tests PREREG_trend_quality_factor.md — does blending core.trend_quality into
the momentum score improve the walk-forward distribution? Pre-registered
2026-08-05, BEFORE this script was run: 5 configs (H1 x 3 weights, H2 x 2
thresholds), fixed decision rule (bootstrap CI excludes zero AND >=12/19
windows win AND max DD doesn't worsen >2pp mean). Do not add configs or
change the decision rule after seeing results — see the prereg doc for why.

Reuses walk_forward.py's window-slicing and backtest_portfolio's validated
engine unchanged; this is a harness, not a new backtest implementation.

Usage:
    python research_trend_quality_factor.py
"""
import functools

import numpy as np
import pandas as pd

import strategy_config as sc
from core import trend_quality
from backtest_portfolio import (load_price_matrix, load_index, load_turnover_matrix,
                                run_backtest_laggards_only, performance)
from walk_forward import make_windows, run_window

N_BOOT = 2000
BLOCK_LEN = 6
SEED = 42


# ---------- score_fn configs (H1: blend, H2: tiebreak) ----------
# Both receive the WHOLE date's eligible pool ({sym: (close_window,
# momentum_result)}) per backtest_portfolio.run_backtest_laggards_only's
# score_fn contract, so H1 can compute a TRUE cross-sectional z-score across
# today's eligible names (not a fixed-scale approximation).

def _zscore(values_by_sym):
    vals = np.array(list(values_by_sym.values()), dtype=float)
    mu, sd = vals.mean(), vals.std(ddof=0)
    if sd == 0 or np.isnan(sd):
        return {k: 0.0 for k in values_by_sym}
    return {k: (v - mu) / sd for k, v in values_by_sym.items()}


def make_blend_score_fn(weight):
    """H1: combined = zscore(momentum_score) + weight * zscore(trend_quality),
    z-scored across the eligible pool AT EACH REBALANCE DATE (proper
    cross-sectional standardization, not a fixed-scale proxy). Names where
    trend_quality is undefined (insufficient history) fall back to
    momentum-only z-score, unweighted by trend."""
    def score_fn(eligible):
        momentum_raw = {sym: r["score"] for sym, (_, r) in eligible.items()}
        trend_raw = {}
        for sym, (close_window, _) in eligible.items():
            tq = trend_quality(close_window)
            if tq is not None:
                trend_raw[sym] = tq

        mom_z = _zscore(momentum_raw)
        trend_z = _zscore(trend_raw) if trend_raw else {}

        return {sym: mom_z[sym] + weight * trend_z.get(sym, 0.0) for sym in eligible}
    return score_fn


def make_tiebreak_score_fn(threshold_pct):
    """H2: names within threshold_pct of each other's momentum score get
    reordered by trend_quality. Monotonic transform: round momentum score
    DOWN to the nearest threshold_pct-wide bucket (ties within a bucket are
    then broken by the secondary sort), plus a small trend_quality tiebreak
    term that can't cross bucket boundaries (bounded to <half a bucket
    width) — so this can only ever reorder WITHIN a near-tie group, never
    override a real momentum-score difference bigger than the threshold."""
    def score_fn(eligible):
        out = {}
        for sym, (close_window, r) in eligible.items():
            base = r["score"]
            tq = trend_quality(close_window)
            if tq is None or base <= 0:
                out[sym] = base
                continue
            bucket_width = base * threshold_pct
            bucket = np.floor(base / bucket_width) * bucket_width if bucket_width > 0 else base
            tiebreak = (tq / 2.0) * bucket_width * 0.49  # stays within the bucket
            out[sym] = bucket + tiebreak
        return out
    return score_fn


CONFIGS = {
    "H1_w0.25": make_blend_score_fn(0.25),
    "H1_w0.50": make_blend_score_fn(0.50),
    "H1_w1.00": make_blend_score_fn(1.00),
    "H2_tie5pct": make_tiebreak_score_fn(0.05),
    "H2_tie10pct": make_tiebreak_score_fn(0.10),
}


# ---------- paired block-bootstrap (same method as research_statistical_hygiene.py) ----------

def paired_window_bootstrap(rows_a, rows_b, n_boot=N_BOOT, block_len=BLOCK_LEN, seed=SEED):
    """rows_a/rows_b: per-WINDOW annual_return arrays from walk_forward
    (paired by window index, same windows for both configs). Resamples
    window-blocks (not daily returns — walk_forward's unit is already one
    number per 3y window) since that's the series this decision rule is
    defined over."""
    a = np.asarray(rows_a, dtype=float)
    b = np.asarray(rows_b, dtype=float)
    n = len(a)
    diffs = b - a
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block_len))
    starts = np.arange(max(1, n - block_len + 1))
    boot_means = np.empty(n_boot)
    for i in range(n_boot):
        chosen = rng.choice(starts, size=n_blocks, replace=True)
        idx = np.concatenate([np.arange(s, min(s + block_len, n)) for s in chosen])
        idx = idx[idx < n][:n]
        boot_means[i] = diffs[idx].mean()
    return diffs, boot_means


def evaluate_config(name, score_fn, matrix, index, turnover, windows, baseline_rows):
    engine = functools.partial(run_backtest_laggards_only, score_fn=score_fn)
    rows = []
    for start, end in windows:
        result = run_window(matrix, index, turnover, start, end, engine=engine)
        rows.append(result)  # keep None to preserve window alignment with baseline

    valid_pairs = [(b, r) for b, r in zip(baseline_rows, rows) if b is not None and r is not None]
    if len(valid_pairs) < len(windows) * 0.8:
        print(f"\n{name}: too many windows failed to run ({len(valid_pairs)}/{len(windows)}) — skipping")
        return None

    base_annual = np.array([b[1] for b, _ in valid_pairs])
    cand_annual = np.array([r[1] for _, r in valid_pairs])
    base_dd = np.array([b[3] for b, _ in valid_pairs])
    cand_dd = np.array([r[3] for _, r in valid_pairs])

    diffs, boot_means = paired_window_bootstrap(base_annual, cand_annual)
    ci_lo, ci_hi = np.percentile(boot_means, 2.5), np.percentile(boot_means, 97.5)
    excludes_zero = ci_lo > 0 or ci_hi < 0
    wins = int((cand_annual > base_annual).sum())
    n = len(valid_pairs)
    dd_delta_mean = (cand_dd - base_dd).mean()  # positive = worse DD (dd is stored as positive magnitude)

    adopt = excludes_zero and ci_lo > 0 and wins >= 12 and dd_delta_mean <= 0.02

    print(f"\n{'-'*70}\n{name}\n{'-'*70}")
    print(f"  n windows compared: {n}/{len(windows)}")
    print(f"  mean annual_return delta: {diffs.mean():+.2%}   "
          f"bootstrap 95% CI [{ci_lo:+.2%}, {ci_hi:+.2%}]   "
          f"P(candidate better)={(boot_means > 0).mean():.1%}")
    print(f"  wins (candidate CAGR > baseline): {wins}/{n}")
    print(f"  mean max_dd delta: {dd_delta_mean:+.2%} (positive = worse)")
    print(f"  -> {'ADOPT (clears all 3 conditions)' if adopt else 'REJECT'}")

    return {"config": name, "mean_delta": diffs.mean(), "ci_lo": ci_lo, "ci_hi": ci_hi,
            "wins": wins, "n": n, "dd_delta": dd_delta_mean, "adopt": adopt}


def main():
    print("=" * 70)
    print("TREND-QUALITY FACTOR — pre-registered study (PREREG_trend_quality_factor.md)")
    print("=" * 70)

    matrix = load_price_matrix()
    index = load_index()
    turnover = load_turnover_matrix(matrix)
    windows = make_windows(matrix, window_years=3, step_months=3)
    print(f"{len(windows)} overlapping 3y windows\n")

    print("Running BASELINE (production run_backtest_laggards_only, score_fn=None)...")
    baseline_rows = [run_window(matrix, index, turnover, s, e) for s, e in windows]
    n_ok = sum(r is not None for r in baseline_rows)
    print(f"  baseline: {n_ok}/{len(windows)} windows produced a result")
    base_annual = [r[1] for r in baseline_rows if r is not None]
    print(f"  baseline mean annual_return: {np.mean(base_annual):+.2%}, "
          f"mean sharpe: {np.mean([r[2] for r in baseline_rows if r is not None]):.2f}")

    results = []
    for name, score_fn in CONFIGS.items():
        print(f"\nRunning {name}...")
        r = evaluate_config(name, score_fn, matrix, index, turnover, windows, baseline_rows)
        if r is not None:
            results.append(r)

    print(f"\n{'='*70}\nSUMMARY\n{'='*70}")
    adopted = [r for r in results if r["adopt"]]
    if not adopted:
        print("0/{} configs cleared the pre-registered bar. No adoption.".format(len(results)))
        print("Per the prereg doc: reject means reject, not 'try another weight'.")
    else:
        print(f"{len(adopted)}/{len(results)} config(s) cleared the bar:")
        for r in adopted:
            print(f"  {r['config']}: delta {r['mean_delta']:+.2%}, CI [{r['ci_lo']:+.2%},{r['ci_hi']:+.2%}], "
                  f"{r['wins']}/{r['n']} windows, DD delta {r['dd_delta']:+.2%}")


if __name__ == "__main__":
    main()
