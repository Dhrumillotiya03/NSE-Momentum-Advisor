"""
Tests PREREG_conviction_sizing.md — does tilting position size toward
momentum-score MAGNITUDE (not just inverse-vol) improve the walk-forward
distribution? Pre-registered 2026-08-05, BEFORE this script was run: 3 tilt
levels (0.25/0.5/0.75), fixed decision rule matching every other study in
this repo (bootstrap CI excludes zero AND >=12/N windows win AND max DD
doesn't worsen >2pp mean). Do not add configs or change the rule after
seeing results.

Redirected from the originally-planned "covariance-aware sizing" study —
that was already built and rejected (risk_parity_weights, memory
risk-parity-sizing-rejected-2026-08). See the prereg doc for why this is a
different, untested question.

Usage:
    python research_conviction_sizing.py
"""
import functools

import numpy as np

from core import momentum_score
from backtest_portfolio import (load_price_matrix, load_index, load_turnover_matrix,
                                run_backtest_laggards_only, conviction_weights, performance)
from walk_forward import make_windows, run_window

N_BOOT = 2000
BLOCK_LEN = 6
SEED = 42


def make_sizing_fn(tilt):
    """Wraps conviction_weights into the sizing_fn(matrix, i, top, vols)
    contract. Recomputes momentum_score for `top` only (a handful of names,
    not the whole universe) — negligible cost, and keeps the sizing_fn
    signature unchanged rather than threading scores through the engine."""
    def sizing_fn(matrix, i, top, vols):
        scores = {}
        for sym in top:
            r = momentum_score(matrix[sym].iloc[:i + 1])
            scores[sym] = r["score"] if r is not None else vols[sym]  # should never miss: top is already-eligible
        return conviction_weights(scores, vols, list(top), tilt)
    return sizing_fn


CONFIGS = {
    "tilt_0.25": make_sizing_fn(0.25),
    "tilt_0.50": make_sizing_fn(0.50),
    "tilt_0.75": make_sizing_fn(0.75),
}


def paired_window_bootstrap(rows_a, rows_b, n_boot=N_BOOT, block_len=BLOCK_LEN, seed=SEED):
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


def evaluate_config(name, sizing_fn, matrix, index, turnover, windows, baseline_rows):
    engine = functools.partial(run_backtest_laggards_only, sizing_fn=sizing_fn)
    rows = [run_window(matrix, index, turnover, s, e, engine=engine) for s, e in windows]

    valid_pairs = [(b, r) for b, r in zip(baseline_rows, rows) if b is not None and r is not None]
    if len(valid_pairs) < len(windows) * 0.8:
        print(f"\n{name}: too many windows failed ({len(valid_pairs)}/{len(windows)}) — skipping")
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
    dd_delta_mean = (cand_dd - base_dd).mean()

    adopt = excludes_zero and ci_lo > 0 and wins >= (n * 12 / 19) and dd_delta_mean <= 0.02

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
    print("CONVICTION-WEIGHTED SIZING — pre-registered study (PREREG_conviction_sizing.md)")
    print("=" * 70)

    matrix = load_price_matrix()
    index = load_index()
    turnover = load_turnover_matrix(matrix)
    windows = make_windows(matrix, window_years=3, step_months=3)
    print(f"{len(windows)} overlapping 3y windows\n")

    print("Running BASELINE (production run_backtest_laggards_only, sizing_fn=None)...")
    # ENGINE MUST BE EXPLICIT. This line read `run_window(matrix, index,
    # turnover, s, e)` until 2026-09-01, and run_window defaulted to the LEGACY
    # hard-close engine — so this "baseline" was NOT the production engine the
    # print above claims, and every candidate (which did pass
    # run_backtest_laggards_only) got a free ~1pp CAGR head start. Corrected,
    # tilt=0.50 is +1.85% [+0.54%,+3.17%] 26/36, not the +2.89% [+1.33%,+4.33%]
    # 33/36 recorded in PREREG_conviction_sizing.md. Still clears the bar; the
    # adoption stands at a smaller effect. See walk_forward.run_window.
    baseline_rows = [run_window(matrix, index, turnover, s, e,
                                engine=run_backtest_laggards_only)
                     for s, e in windows]
    n_ok = sum(r is not None for r in baseline_rows)
    print(f"  baseline: {n_ok}/{len(windows)} windows produced a result")
    base_annual = [r[1] for r in baseline_rows if r is not None]
    print(f"  baseline mean annual_return: {np.mean(base_annual):+.2%}, "
          f"mean sharpe: {np.mean([r[2] for r in baseline_rows if r is not None]):.2f}")

    results = []
    for name, sizing_fn in CONFIGS.items():
        print(f"\nRunning {name}...")
        r = evaluate_config(name, sizing_fn, matrix, index, turnover, windows, baseline_rows)
        if r is not None:
            results.append(r)

    print(f"\n{'='*70}\nSUMMARY\n{'='*70}")
    adopted = [r for r in results if r["adopt"]]
    if not adopted:
        print(f"0/{len(results)} configs cleared the pre-registered bar. No adoption.")
    else:
        print(f"{len(adopted)}/{len(results)} config(s) cleared the bar:")
        for r in adopted:
            print(f"  {r['config']}: delta {r['mean_delta']:+.2%}, CI [{r['ci_lo']:+.2%},{r['ci_hi']:+.2%}], "
                  f"{r['wins']}/{r['n']} windows, DD delta {r['dd_delta']:+.2%}")


if __name__ == "__main__":
    main()
