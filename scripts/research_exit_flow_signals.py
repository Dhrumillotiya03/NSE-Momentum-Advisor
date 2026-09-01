"""
Tests PREREG_exit_side_flow_signals.md — do delivery%-decay or futures-OI-
unwind, checked DAILY on already-held positions, add value as an early-
warning exit beyond the existing catastrophic/trail stop? Pre-registered
2026-08-10, BEFORE this script was run: 4 configs (H1 x 2 thresholds, H2 x 2
(N,M) pairs), fixed decision rule, PLUS a mandatory per-trigger false-
positive/true-positive breakdown (the check that caught the announcements
veto's failure mode) before trusting any aggregate pass.

Honest prior: three independent auxiliary-override mechanisms already failed
on this strategy (entry rank-blend x2, event-veto x1) — see the prereg doc.
This is ONE clean pass, not a search; 0/4 clearing the bar closes this
mechanism family, matching how the S/R improvement batch closed price-derived
refinement.

Usage:
    python research_exit_flow_signals.py
"""
import functools

import numpy as np
import pandas as pd

from backtest_portfolio import (load_price_matrix, load_index, load_turnover_matrix,
                                run_backtest_laggards_only, performance)
from walk_forward import make_windows, run_window

N_BOOT = 2000
BLOCK_LEN = 6
SEED = 42

DELIVERY_DIR = "../data/delivery_data/"
FO_DIR = "../data/fo_data/"


# ---------- signal loading (once, not per-check — the loop runs daily per held name) ----------

def load_delivery_series(symbols):
    """sym -> pd.Series(DelivPer, indexed by Date), for symbols with a file."""
    out = {}
    for sym in symbols:
        path = f"{DELIVERY_DIR}{sym}.csv"
        try:
            df = pd.read_csv(path, parse_dates=["Date"])
            df["DelivPer"] = pd.to_numeric(df["DelivPer"], errors="coerce")
            out[sym] = df.dropna(subset=["DelivPer"]).set_index("Date")["DelivPer"].sort_index()
        except FileNotFoundError:
            continue
    return out


def load_fo_oi_series(symbols):
    """sym -> pd.Series(FutOIChg, indexed by Date), for symbols with F&O data."""
    out = {}
    for sym in symbols:
        path = f"{FO_DIR}{sym}.csv"
        try:
            df = pd.read_csv(path, parse_dates=["Date"])
            df["FutOIChg"] = pd.to_numeric(df["FutOIChg"], errors="coerce")
            out[sym] = df.dropna(subset=["FutOIChg"]).set_index("Date")["FutOIChg"].sort_index()
        except FileNotFoundError:
            continue
    return out


# ---------- exit_signal_fn factories ----------
# Both need to know the ENTRY DATE per symbol to compute "decay since entry" —
# run_backtest_laggards_only's exit_signal_fn(symbol, date) contract doesn't
# pass entry date, so these track it via a small internal dict, updated by
# watching for the first call on any (symbol) not yet seen in the current
# holding episode. Reset when a symbol reappears after having been absent
# (a prior exit + later re-entry) by tracking the date sequence per symbol.

def make_delivery_signal_fn(deliv_series, decay_threshold, ma_window=10):
    entry_ma = {}   # sym -> the trailing MA of DelivPer as of entry
    last_seen_date = {}

    def signal(sym, date):
        series = deliv_series.get(sym)
        if series is None:
            return False
        window = series[series.index <= date].tail(ma_window)
        if len(window) < ma_window:
            return False
        current_ma = window.mean()

        # Detect a fresh holding episode: no record, or a gap since last check
        # bigger than a normal rebalance cadence implies this is a new entry.
        prev_date = last_seen_date.get(sym)
        is_new_episode = prev_date is None or (date - prev_date).days > 35
        last_seen_date[sym] = date
        if is_new_episode:
            entry_ma[sym] = current_ma
            return False

        baseline = entry_ma.get(sym)
        if baseline is None or baseline <= 0:
            return False
        return (current_ma / baseline) < decay_threshold

    return signal


def make_oi_unwind_signal_fn(oi_series, window_n, consecutive_m):
    below_zero_streak = {}
    last_seen_date = {}

    def signal(sym, date):
        series = oi_series.get(sym)
        if series is None:
            return False
        window = series[series.index <= date].tail(window_n)
        if len(window) < window_n:
            return False

        prev_date = last_seen_date.get(sym)
        is_new_episode = prev_date is None or (date - prev_date).days > 35
        last_seen_date[sym] = date
        if is_new_episode:
            below_zero_streak[sym] = 0
            return False

        n_day_sum = window.sum()
        if n_day_sum < 0:
            below_zero_streak[sym] = below_zero_streak.get(sym, 0) + 1
        else:
            below_zero_streak[sym] = 0
        return below_zero_streak[sym] >= consecutive_m

    return signal


# ---------- decision-rule harness (same pattern as the other studies) ----------

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


def evaluate_config(name, exit_signal_fn, matrix, index, turnover, windows, baseline_rows):
    engine = functools.partial(run_backtest_laggards_only, exit_signal_fn=exit_signal_fn)
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
    wins = int((cand_annual > base_annual).sum())
    n = len(valid_pairs)
    dd_delta_mean = (cand_dd - base_dd).mean()

    adopt = ci_lo > 0 and wins >= (n * 12 / 19) and dd_delta_mean <= 0.02

    print(f"\n{'-'*70}\n{name}\n{'-'*70}")
    print(f"  n windows compared: {n}/{len(windows)}")
    print(f"  mean annual_return delta: {diffs.mean():+.2%}   "
          f"bootstrap 95% CI [{ci_lo:+.2%}, {ci_hi:+.2%}]   "
          f"P(candidate better)={(boot_means > 0).mean():.1%}")
    print(f"  wins (candidate CAGR > baseline): {wins}/{n}")
    print(f"  mean max_dd delta: {dd_delta_mean:+.2%} (positive = worse)")
    print(f"  -> {'PASSES aggregate bar (still needs per-trigger check)' if adopt else 'REJECT'}")

    return {"config": name, "mean_delta": diffs.mean(), "ci_lo": ci_lo, "ci_hi": ci_hi,
            "wins": wins, "n": n, "dd_delta": dd_delta_mean, "adopt": adopt}


def main():
    print("=" * 70)
    print("EXIT-SIDE DELIVERY%/OI SIGNALS — pre-registered study")
    print("(PREREG_exit_side_flow_signals.md)")
    print("=" * 70)

    matrix = load_price_matrix()
    index = load_index()
    turnover = load_turnover_matrix(matrix)
    windows = make_windows(matrix, window_years=3, step_months=3)
    print(f"{len(windows)} overlapping 3y windows\n")

    symbols = list(matrix.columns)
    print("Loading delivery%% and F&O OI series...")
    deliv_series = load_delivery_series(symbols)
    oi_series = load_fo_oi_series(symbols)
    print(f"  delivery data: {len(deliv_series)}/{len(symbols)} symbols")
    print(f"  F&O OI data:   {len(oi_series)}/{len(symbols)} symbols")

    print("\nRunning BASELINE (production run_backtest_laggards_only, exit_signal_fn=None)...")
    # engine made explicit 2026-09-01 — run_window used to default to the
    # LEGACY hard-close engine, so this baseline was not the production one.
    # This study REJECTED its candidates, and the confound favoured them, so
    # the rejection holds a fortiori. See walk_forward.run_window.
    baseline_rows = [run_window(matrix, index, turnover, s, e,
                                engine=run_backtest_laggards_only)
                     for s, e in windows]
    n_ok = sum(r is not None for r in baseline_rows)
    print(f"  baseline: {n_ok}/{len(windows)} windows produced a result")
    base_annual = [r[1] for r in baseline_rows if r is not None]
    print(f"  baseline mean annual_return: {np.mean(base_annual):+.2%}")

    configs = {
        "H1_deliv_decay_0.70": make_delivery_signal_fn(deliv_series, 0.70),
        "H1_deliv_decay_0.50": make_delivery_signal_fn(deliv_series, 0.50),
        "H2_oi_unwind_5d3": make_oi_unwind_signal_fn(oi_series, 5, 3),
        "H2_oi_unwind_10d5": make_oi_unwind_signal_fn(oi_series, 10, 5),
    }

    results = []
    for name, fn in configs.items():
        print(f"\nRunning {name}...")
        r = evaluate_config(name, fn, matrix, index, turnover, windows, baseline_rows)
        if r is not None:
            results.append(r)

    print(f"\n{'='*70}\nSUMMARY\n{'='*70}")
    passing = [r for r in results if r["adopt"]]
    if not passing:
        print(f"0/{len(results)} configs cleared the aggregate bar. No adoption.")
        print("Per the prereg doc: this closes the exit-side flow-signal mechanism family.")
    else:
        print(f"{len(passing)}/{len(results)} config(s) passed the AGGREGATE bar "
              f"(still need the per-trigger false-positive check before adoption):")
        for r in passing:
            print(f"  {r['config']}: delta {r['mean_delta']:+.2%}, "
                  f"CI [{r['ci_lo']:+.2%},{r['ci_hi']:+.2%}], "
                  f"{r['wins']}/{r['n']} windows, DD delta {r['dd_delta']:+.2%}")


if __name__ == "__main__":
    main()
