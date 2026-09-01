"""
Tests PREREG_max_weight_cap.md — MAX_WEIGHT=0.20 is arithmetically infeasible
at the production n (3 in SIDEWAYS, 4 in BEAR), so clip-then-renormalize
returns exactly 1/n and the cap silently does nothing except force equal
weight. Measured: 63.5% of rebalances are fully clipped, and CONVICTION_TILT
is therefore INERT in 73% of them.

Three pre-registered configs, fixed before this script was run:
  cap_0.35   — enough headroom that the adopted conviction tilt can act at
               n=3/n=4 (raw conviction spread is ~0.10)
  cap_0.30   — the milder version, to check the effect is monotonic in
               headroom rather than one lucky point
  absolute   — the cap means what it says: clip at 0.20, leave the
               un-allocable remainder (40% at n=3) in cash

Decision rule (pre-registered, and STRICTER than the repo standard by one
condition): bootstrap 95% CI excludes zero AND >=12/N windows win AND mean
max DD does not worsen >2pp AND **worst-case window DD does not worsen at
all**. Condition 4 is a hard gate here because cap_0.30/0.35 are explicitly
concentration-INCREASING changes and mean DD can improve while the tail rots.

Do not add configs or relax the rule after seeing results. 0/3 clearing is a
complete outcome — it makes this a documentation fix, not a search for a
fourth config.

Usage (from scripts/):
    python research_max_weight_cap.py
"""
import functools

import numpy as np

from backtest_portfolio import (load_price_matrix, load_index, load_turnover_matrix,
                                run_backtest_laggards_only)
from walk_forward import make_windows, run_window

N_BOOT = 2000
BLOCK_LEN = 6
SEED = 42

CONFIGS = {
    "cap_0.35":     dict(max_weight=0.35, cap_mode="renormalize"),
    "cap_0.30":     dict(max_weight=0.30, cap_mode="renormalize"),
    "absolute_0.20": dict(max_weight=0.20, cap_mode="absolute"),
}


def paired_window_bootstrap(rows_a, rows_b, n_boot=N_BOOT, block_len=BLOCK_LEN, seed=SEED):
    """Identical to research_conviction_sizing.py's — same harness, same seed,
    so the two studies' CIs are directly comparable."""
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


def evaluate(name, kwargs, matrix, index, turnover, windows, baseline_rows):
    engine = functools.partial(run_backtest_laggards_only, **kwargs)
    rows = [run_window(matrix, index, turnover, s, e, engine=engine) for s, e in windows]

    pairs = [(b, r) for b, r in zip(baseline_rows, rows) if b is not None and r is not None]
    if len(pairs) < len(windows) * 0.8:
        print(f"\n{name}: too many windows failed ({len(pairs)}/{len(windows)}) — skipping")
        return None

    base_annual = np.array([b[1] for b, _ in pairs])
    cand_annual = np.array([r[1] for _, r in pairs])
    base_dd = np.array([b[3] for b, _ in pairs])
    cand_dd = np.array([r[3] for _, r in pairs])

    diffs, boot = paired_window_bootstrap(base_annual, cand_annual)
    ci_lo, ci_hi = np.percentile(boot, 2.5), np.percentile(boot, 97.5)
    wins = int((cand_annual > base_annual).sum())
    n = len(pairs)
    dd_mean_delta = (cand_dd - base_dd).mean()
    dd_worst_delta = cand_dd.max() - base_dd.max()

    c1 = ci_lo > 0
    c2 = wins >= (n * 12 / 19)
    c3 = dd_mean_delta <= 0.02
    c4 = dd_worst_delta <= 0.0
    adopt = c1 and c2 and c3 and c4

    print(f"\n{'-'*72}\n{name}\n{'-'*72}")
    print(f"  n windows compared: {n}/{len(windows)}")
    print(f"  mean annual_return delta: {diffs.mean():+.2%}   "
          f"95% CI [{ci_lo:+.2%}, {ci_hi:+.2%}]   P(better)={(boot > 0).mean():.1%}")
    print(f"  wins: {wins}/{n}")
    print(f"  mean max_dd delta:  {dd_mean_delta:+.2%} (positive = worse)")
    print(f"  worst-case dd:      baseline {base_dd.max():.2%} -> candidate "
          f"{cand_dd.max():.2%}  ({dd_worst_delta:+.2%})")
    print(f"  conditions: CI>0 {c1} | wins {c2} | meanDD {c3} | worstDD {c4}")
    print(f"  -> {'ADOPT' if adopt else 'REJECT'}")

    return {"config": name, "delta": diffs.mean(), "ci": (ci_lo, ci_hi), "wins": wins,
            "n": n, "dd_mean": dd_mean_delta, "dd_worst": dd_worst_delta, "adopt": adopt,
            "per_window": (cand_annual - base_annual)}


def main():
    print("=" * 72)
    print("MAX_WEIGHT CAP — pre-registered study (PREREG_max_weight_cap.md)")
    print("=" * 72)

    matrix = load_price_matrix()
    index = load_index()
    turnover = load_turnover_matrix(matrix)
    windows = make_windows(matrix, window_years=3, step_months=3)
    print(f"{len(windows)} overlapping 3y windows (3y/3mo — same grid as "
          f"PREREG_conviction_sizing.md)\n")

    print("Running BASELINE (production: MAX_WEIGHT=0.20, clip-then-renormalize)...")
    baseline_rows = [run_window(matrix, index, turnover, s, e,
                                engine=run_backtest_laggards_only)
                     for s, e in windows]
    ok = [r for r in baseline_rows if r is not None]
    print(f"  baseline: {len(ok)}/{len(windows)} windows")
    print(f"  baseline mean annual_return {np.mean([r[1] for r in ok]):+.2%}, "
          f"mean sharpe {np.mean([r[2] for r in ok]):.2f}, "
          f"mean maxDD {np.mean([r[3] for r in ok]):.2%}, "
          f"worst-window maxDD {max(r[3] for r in ok):.2%}")

    results = []
    for name, kwargs in CONFIGS.items():
        print(f"\nRunning {name} ({kwargs})...")
        r = evaluate(name, kwargs, matrix, index, turnover, windows, baseline_rows)
        if r:
            results.append(r)

    print(f"\n{'='*72}\nSUMMARY\n{'='*72}")
    adopted = [r for r in results if r["adopt"]]
    if not adopted:
        print(f"0/{len(results)} configs cleared the pre-registered bar. NO ADOPTION.")
        print("Per the prereg, the outcome is a DOCUMENTATION fix: production keeps")
        print("clip-then-renormalize, and recommend.py / strategy_config.py stop")
        print("claiming a 20% single-name cap that does not exist at n=3 or n=4.")
    else:
        print(f"{len(adopted)}/{len(results)} cleared the bar — run the adversarial")
        print("checklist from PREREG_conviction_sizing.md before believing it:")
        for r in adopted:
            pw = r["per_window"]
            print(f"  {r['config']}: {r['delta']:+.2%}, CI [{r['ci'][0]:+.2%},{r['ci'][1]:+.2%}], "
                  f"{r['wins']}/{r['n']} windows, meanDD {r['dd_mean']:+.2%}, "
                  f"worstDD {r['dd_worst']:+.2%}")
            print(f"     per-window: {int((pw > 0).sum())} positive / "
                  f"{int((pw <= 0).sum())} negative, median {np.median(pw):+.2%}, "
                  f"best {pw.max():+.2%}, worst {pw.min():+.2%}")
            half = len(pw) // 2
            print(f"     era split: early windows {pw[:half].mean():+.2%} vs "
                  f"late windows {pw[half:].mean():+.2%}")


if __name__ == "__main__":
    main()
