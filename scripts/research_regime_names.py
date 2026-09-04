"""
REGIME_NAMES (portfolio breadth) re-test — pre-registered in
PREREG_regime_names.md. Read the decision rule there first.

Stage 1 (--screen) is a full-panel phase-averaged SCREEN and cannot adopt
anything. Stage 2 (default) is the walk-forward decision, phase-gated.
"""
import argparse, functools
import numpy as np
import pandas as pd

import strategy_config as sc
from backtest_portfolio import (load_price_matrix, load_index,
                                load_turnover_matrix, run_backtest_laggards_only)
from walk_forward import make_windows, run_window

CONFIGS = {
    "baseline": {"BULL": 10, "SIDEWAYS": 3, "BEAR": 4, "UNKNOWN": 6},
    "A":        {"BULL": 10, "SIDEWAYS": 5, "BEAR": 6, "UNKNOWN": 6},
    "B":        {"BULL": 10, "SIDEWAYS": 6, "BEAR": 8, "UNKNOWN": 6},
    "C":        {"BULL": 10, "SIDEWAYS": 8, "BEAR": 10, "UNKNOWN": 6},
    "D":        {"BULL": 15, "SIDEWAYS": 5, "BEAR": 6, "UNKNOWN": 6},
    "E":        {"BULL": 10, "SIDEWAYS": 5, "BEAR": 5, "UNKNOWN": 6},
}
PHASES = [0, 5, 10, 15]
BLOCK_LEN, N_BOOT, SEED = 6, 2000, 42


class breadth:
    """Temporarily swap REGIME_NAMES. The engine reads it from strategy_config
    at rebalance time, so this is the only way to vary it without threading a
    new parameter through four call sites — always restored."""
    def __init__(self, cfg): self.cfg = cfg
    def __enter__(self):
        self.old = sc.REGIME_NAMES
        sc.REGIME_NAMES = dict(self.cfg)
    def __exit__(self, *a): sc.REGIME_NAMES = self.old


def daily_metrics(matrix, index, turnover, phase):
    dm, bl = [], []
    run_backtest_laggards_only(matrix, index, turnover, phase=phase,
                               daily_marks=dm, book_log=bl)
    v = pd.Series({d: x for d, x in dm}).sort_index().values.astype(float)
    yrs = len(v) / 252.0
    cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    r = v[1:] / v[:-1] - 1
    vol = r.std(ddof=1) * np.sqrt(252)
    peak = np.maximum.accumulate(v)
    dd = float(np.max((peak - v) / peak))
    mw = np.median([max(c.values()) for _, c in bl if c])
    return cagr, (cagr / vol if vol else 0.0), dd, float(mw)


def screen(matrix, index, turnover):
    print("STAGE 1 — SCREEN (full panel, phase-averaged; CANNOT ADOPT)\n")
    print(f"  {'cfg':>9} {'CAGR':>9} {'Sharpe':>8} {'maxDD(daily)':>13} {'medMaxWt':>9}")
    out = {}
    for name, cfg in CONFIGS.items():
        with breadth(cfg):
            rs = [daily_metrics(matrix, index, turnover, p) for p in PHASES]
        a = np.array(rs)
        out[name] = a.mean(axis=0)
        c, s, d, w = out[name]
        print(f"  {name:>9} {c:>+9.2%} {s:>8.2f} {d:>13.2%} {w:>9.1%}", flush=True)
    b = out["baseline"]
    print(f"\n  deltas vs baseline (screen only — not a decision):")
    for name in CONFIGS:
        if name == "baseline":
            continue
        c, s, d, w = out[name]
        print(f"    {name:>3}  CAGR {c-b[0]:>+7.2%}   Sharpe {s-b[1]:>+6.2f}   "
              f"maxDD {d-b[2]:>+7.2%}   maxWt {w-b[3]:>+6.1%}")
    return out


def boot_ci(deltas):
    rng = np.random.default_rng(SEED)
    d = np.asarray(deltas); n = len(d)
    nb = int(np.ceil(n / BLOCK_LEN))
    means = []
    for _ in range(N_BOOT):
        idx = np.concatenate([np.arange(s, s + BLOCK_LEN)
                              for s in rng.integers(0, n, nb)]) % n
        means.append(d[idx[:n]].mean())
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def decide(matrix, index, turnover, cands):
    windows = make_windows(matrix, window_years=3, step_months=3)
    print(f"\nSTAGE 2 — DECISION ({len(windows)} windows x {len(PHASES)} phases)\n")
    for name in cands:
        print(f"  --- config {name}  {CONFIGS[name]}")
        passes = 0
        for p in PHASES:
            eng = functools.partial(run_backtest_laggards_only, phase=p)
            with breadth(CONFIGS["baseline"]):
                base = [run_window(matrix, index, turnover, s, e, engine=eng)
                        for s, e in windows]
            with breadth(CONFIGS[name]):
                cand = [run_window(matrix, index, turnover, s, e, engine=eng)
                        for s, e in windows]
            pairs = [(b, c) for b, c in zip(base, cand) if b and c]
            d = np.array([c[1] - b[1] for b, c in pairs])
            ddd = np.array([c[3] - b[3] for b, c in pairs])
            lo, hi = boot_ci(d)
            wins = int((d > 0).sum()); n = len(d)
            ret_arm = lo > 0 and wins >= (2 * n) // 3 and ddd.mean() <= 0.02
            ok = ret_arm
            passes += int(ok)
            print(f"    phase {p:>2}  dCAGR {d.mean():>+7.2%}  CI [{lo:>+6.2%},{hi:>+6.2%}]  "
                  f"wins {wins:>2}/{n}  dMeanDD {ddd.mean():>+6.2%}  "
                  f"{'PASS' if ok else 'fail'}", flush=True)
        print(f"    => {name}: {passes}/{len(PHASES)} phases "
              f"{'ADOPTABLE (return arm)' if passes >= 3 else 'not adoptable on the return arm'}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--screen", action="store_true")
    ap.add_argument("--configs", default="A,B,C,D,E")
    a = ap.parse_args()
    matrix = load_price_matrix(); index = load_index()
    turnover = load_turnover_matrix(matrix)
    print("=" * 74); print("REGIME_NAMES RE-TEST — PREREG_regime_names.md"); print("=" * 74)
    screen(matrix, index, turnover)
    if not a.screen:
        decide(matrix, index, turnover, [c for c in a.configs.split(",") if c])


if __name__ == "__main__":
    main()
