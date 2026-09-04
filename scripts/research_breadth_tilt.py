"""
Breadth x conviction-tilt — PREREG_regime_names.md AMENDMENT 1.

At the production n (3-4 names) MAX_WEIGHT=0.20 is arithmetically infeasible,
so weights are exactly equal and CONVICTION_TILT does nothing. Widening the
book makes the cap feasible and the tilt live for the first time. Question:
does a WIDE book with a HARD tilt keep the return that concentration is buying
while shedding the single-name tail risk?
"""
import argparse, functools
import numpy as np, pandas as pd
import strategy_config as sc
from backtest_portfolio import (load_price_matrix, load_index,
                                load_turnover_matrix, run_backtest_laggards_only)
from walk_forward import make_windows, run_window

PHASES = [0, 5, 10, 15]
BLOCK_LEN, N_BOOT, SEED = 6, 2000, 42
BREADTH = {"prod": {"BULL":10,"SIDEWAYS":3,"BEAR":4,"UNKNOWN":6},
           "A":    {"BULL":10,"SIDEWAYS":5,"BEAR":6,"UNKNOWN":6},
           "C":    {"BULL":10,"SIDEWAYS":8,"BEAR":10,"UNKNOWN":6}}
ARMS = [("C", 0.50), ("C", 0.75), ("C", 1.00), ("A", 0.75)]


class cfg:
    def __init__(s, b, t): s.b, s.t = BREADTH[b], t
    def __enter__(s):
        s.ob, s.ot = sc.REGIME_NAMES, sc.CONVICTION_TILT
        sc.REGIME_NAMES, sc.CONVICTION_TILT = dict(s.b), s.t
    def __exit__(s, *a):
        sc.REGIME_NAMES, sc.CONVICTION_TILT = s.ob, s.ot


def dmetrics(m, ix, tv, phase):
    dm, bl = [], []
    run_backtest_laggards_only(m, ix, tv, phase=phase, daily_marks=dm, book_log=bl)
    v = pd.Series({d: x for d, x in dm}).sort_index().values.astype(float)
    yrs = len(v)/252.0; cagr = (v[-1]/v[0])**(1/yrs)-1
    r = v[1:]/v[:-1]-1; vol = r.std(ddof=1)*np.sqrt(252)
    pk = np.maximum.accumulate(v); dd = float(np.max((pk-v)/pk))
    mw = float(np.median([max(c.values()) for _, c in bl if c]))
    p99 = float(np.percentile([max(c.values()) for _, c in bl if c], 99))
    return cagr, cagr/vol if vol else 0, dd, mw, p99


def boot(d):
    rng = np.random.default_rng(SEED); d = np.asarray(d); n = len(d)
    nb = int(np.ceil(n/BLOCK_LEN)); out = []
    for _ in range(N_BOOT):
        i = np.concatenate([np.arange(s, s+BLOCK_LEN) for s in rng.integers(0, n, nb)]) % n
        out.append(d[i[:n]].mean())
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--screen", action="store_true")
    a = ap.parse_args()
    m = load_price_matrix(); ix = load_index(); tv = load_turnover_matrix(m)
    print("="*76); print("BREADTH x TILT — PREREG_regime_names.md AMENDMENT 1"); print("="*76)
    print(f"\nSCREEN (full panel, mean of phases {PHASES})\n")
    print(f"  {'arm':>12} {'CAGR':>9} {'Sharpe':>8} {'maxDD':>8} {'medMaxWt':>9} {'p99MaxWt':>9}")
    res = {}
    for name, t in [("prod", 0.50)] + ARMS:
        with cfg(name, t):
            r = np.array([dmetrics(m, ix, tv, p) for p in PHASES]).mean(axis=0)
        res[(name, t)] = r
        print(f"  {name+' tilt'+f'{t:.2f}':>12} {r[0]:>+9.2%} {r[1]:>8.2f} {r[2]:>8.2%} "
              f"{r[3]:>9.1%} {r[4]:>9.1%}", flush=True)
    b = res[("prod", 0.50)]
    print(f"\n  deltas vs production:")
    for k in ARMS:
        r = res[k]
        print(f"    {k[0]} tilt {k[1]:.2f}   CAGR {r[0]-b[0]:>+7.2%}  Sharpe {r[1]-b[1]:>+6.2f}  "
              f"maxDD {r[2]-b[2]:>+7.2%}  p99MaxWt {r[4]-b[4]:>+6.1%}")
    if a.screen:
        return
    windows = make_windows(m, window_years=3, step_months=3)
    print(f"\nDECISION — walk-forward, {len(windows)} windows x {len(PHASES)} phases\n")
    for name, t in ARMS:
        print(f"  --- {name} tilt {t:.2f}")
        npass = 0
        for p in PHASES:
            eng = functools.partial(run_backtest_laggards_only, phase=p)
            with cfg("prod", 0.50):
                bs = [run_window(m, ix, tv, s, e, engine=eng) for s, e in windows]
            with cfg(name, t):
                cs = [run_window(m, ix, tv, s, e, engine=eng) for s, e in windows]
            pr = [(x, y) for x, y in zip(bs, cs) if x and y]
            d = np.array([y[1]-x[1] for x, y in pr]); dd = np.array([y[3]-x[3] for x, y in pr])
            lo, hi = boot(d); w = int((d > 0).sum()); n = len(d)
            ok = lo > 0 and w >= (2*n)//3 and dd.mean() <= 0.02
            npass += ok
            print(f"    phase {p:>2}  dCAGR {d.mean():>+7.2%}  CI [{lo:>+6.2%},{hi:>+6.2%}]  "
                  f"wins {w:>2}/{n}  dMeanDD {dd.mean():>+6.2%}  {'PASS' if ok else 'fail'}",
                  flush=True)
        print(f"    => {npass}/{len(PHASES)} phases\n")


if __name__ == "__main__":
    main()
