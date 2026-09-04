"""
TRANCHED (OVERLAPPING) REBALANCING — does staggering the rebalance calendar
across N sleeves remove the 11.19pp of timing luck, and at what cost?

Pre-registered in PREREG_tranching.md. Read the decision rule there BEFORE the
numbers below: this is a VARIANCE-reduction claim, not an alpha claim, so it
is deliberately NOT judged by "mean CAGR delta with a CI excluding zero" —
tranched mean return is the average of its sleeves by construction, and that
bar would reject an arithmetically correct change.

Construction (Jegadeesh-Titman overlapping portfolios): capital is split into
N equal sleeves, sleeve k rebalancing on grid phase (offset + k*HOLD/N). Each
sleeve is self-contained — its own cash, its own book, its own monthly
rotation — so total wealth is exactly the sum of the sleeve curves. Every
sleeve still holds for one month; only the arbitrary choice of WHICH day is
diversified away.

Usage (from scripts/):
    python research_tranching.py
    python research_tranching.py --wf     # also the walk-forward view
"""
import argparse
import numpy as np
import pandas as pd

import strategy_config as sc
from backtest_portfolio import (load_price_matrix, load_index,
                                load_turnover_matrix, run_backtest_laggards_only)

HOLD = sc.HOLD
NS = [1, 2, 3, 4, 7, 21]


def phase_curves(matrix, index, turnover):
    """Daily equity curve + post-rebalance composition for every phase."""
    curves, books = {}, {}
    for p in range(HOLD):
        dm, bl = [], []
        run_backtest_laggards_only(matrix, index, turnover, phase=p,
                                   daily_marks=dm, book_log=bl)
        curves[p] = pd.Series({d: v for d, v in dm}).sort_index()
        books[p] = bl
        print(f"    phase {p:>2} done", end="\r", flush=True)
    print(" " * 30, end="\r")
    return curves, books


def metrics(curve):
    """CAGR / Sharpe / maxDD from a DAILY curve. Drawdown is computed on the
    curve itself — never as a mean of per-sleeve drawdowns, which would
    understate the aggregate (adversarial check 3)."""
    v = curve.values.astype(float)
    yrs = len(v) / 252.0
    cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    r = v[1:] / v[:-1] - 1
    vol = r.std(ddof=1) * np.sqrt(252)
    sharpe = cagr / vol if vol > 0 else 0.0
    dd = float(np.max((np.maximum.accumulate(v) - v) / np.maximum.accumulate(v)))
    return cagr, sharpe, dd, vol


def phase_set(offset, n):
    return [(offset + int(round(k * HOLD / n))) % HOLD for k in range(n)]


def combine(curves, phases):
    """Total wealth of equal sleeves = the mean of their curves, on the common
    date axis (each sleeve is self-contained, so wealth is additive)."""
    df = pd.concat([curves[p] for p in phases], axis=1).dropna()
    return df.mean(axis=1)


def aggregate_concentration(books, phases):
    """C1 — how concentrated is the AGGREGATE book once sleeves are combined?

    Momentum is persistent, so sleeves 21/N sessions apart may pick the SAME
    names, in which case combining them diversifies nothing and only the
    variance benefit survives. Measured, not assumed.

    A sleeve's composition must be carried forward as a WHOLE DICT to the next
    of ITS OWN rebalance dates — never per-column ffill, which resurrects a
    name the sleeve has already sold and inflates the name count without limit.
    """
    per_sleeve = [dict(books[p]) for p in phases]
    sleeve_dates = [sorted(d) for d in per_sleeve]
    all_dates = sorted(set().union(*[set(d) for d in sleeve_dates]))
    # only score dates on which EVERY sleeve is already live
    start = max(d[0] for d in sleeve_dates)
    n_names, max_w = [], []
    for dt in all_dates:
        if dt < start:
            continue
        agg = {}
        for comp, dts in zip(per_sleeve, sleeve_dates):
            prev = [x for x in dts if x <= dt]
            if not prev:
                continue
            for sym, wt in comp[prev[-1]].items():
                agg[sym] = agg.get(sym, 0.0) + wt / len(phases)
        if not agg:
            continue
        n_names.append(len(agg))
        max_w.append(max(agg.values()))
    return float(np.median(n_names)), float(np.median(max_w)), float(np.max(max_w))


def turnover_rate(books, phases):
    """C2 — rupee turnover as a fraction of sleeve capital per rebalance.
    Tranching must SPLIT the same rotation across more dates, not create
    extra round-trips; a materially higher rate here falsifies that."""
    rates = []
    for p in phases:
        prev = {}
        for _, comp in books[p]:
            keys = set(prev) | set(comp)
            rates.append(sum(abs(comp.get(k, 0.0) - prev.get(k, 0.0)) for k in keys))
            prev = comp
    return float(np.mean(rates))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wf", action="store_true")
    args = ap.parse_args()

    matrix = load_price_matrix(); index = load_index()
    turnover = load_turnover_matrix(matrix)
    print("=" * 78)
    print("TRANCHED REBALANCING — PREREG_tranching.md")
    print("=" * 78)
    print(f"Panel {matrix.index[0].date()} -> {matrix.index[-1].date()}  "
          f"({matrix.shape[1]} stocks)\n")
    print("  building all 21 phase curves...")
    curves, books = phase_curves(matrix, index, turnover)

    # adversarial check 2 — the N=1 arm must reproduce the published spread
    solo = np.array([metrics(curves[p])[0] for p in range(HOLD)])
    print(f"  CHECK: single-sleeve CAGR spread {solo.min():+.2%}..{solo.max():+.2%} "
          f"(research_timing_luck.py published 21.94%..33.13%)\n")

    print(f"  {'N':>3} {'meanCAGR':>10} {'sdCAGR':>8} {'spread':>8} "
          f"{'meanSharpe':>11} {'meanDD':>8} {'worstDD':>8} {'names':>7} "
          f"{'maxwt':>7} {'turn':>7}")
    rows = {}
    for n in NS:
        cs, ss, ds, vs = [], [], [], []
        for off in range(HOLD):
            ph = phase_set(off, n)
            c, s, d, v = metrics(combine(curves, ph))
            cs.append(c); ss.append(s); ds.append(d); vs.append(v)
        cs = np.array(cs); ds = np.array(ds)
        nn, mw, mwx = aggregate_concentration(books, phase_set(0, n))
        tr = turnover_rate(books, phase_set(0, n))
        rows[n] = dict(mean=cs.mean(), sd=cs.std(ddof=1), spread=cs.max() - cs.min(),
                       sharpe=np.mean(ss), dd=ds.mean(), worstdd=ds.max(),
                       names=nn, maxw=mw, maxwx=mwx, turn=tr, vol=np.mean(vs))
        r = rows[n]
        print(f"  {n:>3} {r['mean']:>+10.2%} {r['sd']:>8.2%} {r['spread']:>8.2%} "
              f"{r['sharpe']:>11.2f} {r['dd']:>8.2%} {r['worstdd']:>8.2%} "
              f"{r['names']:>7.1f} {r['maxw']:>7.1%} {r['turn']:>7.2f}", flush=True)

    base = rows[1]
    print(f"\n{'='*78}\nDECISION RULE (frozen in PREREG_tranching.md)\n{'='*78}")
    for n in NS[1:]:
        r = rows[n]
        r1 = r["mean"] >= base["mean"] - 0.005
        r2 = r["sd"] <= base["sd"] * 0.60
        r3 = (r["dd"] <= base["dd"] + 0.010) and (r["worstdd"] < base["worstdd"])
        c2 = r["turn"] <= base["turn"] * 1.10
        ok = r1 and r2 and r3 and c2
        print(f"  N={n:<3} R1 return {'PASS' if r1 else 'FAIL'} "
              f"({r['mean']-base['mean']:+.2%})   "
              f"R2 dispersion {'PASS' if r2 else 'FAIL'} "
              f"(sd {base['sd']:.2%}->{r['sd']:.2%}, {r['sd']/base['sd']-1:+.0%})   "
              f"R3 risk {'PASS' if r3 else 'FAIL'} "
              f"(worstDD {base['worstdd']:.1%}->{r['worstdd']:.1%})   "
              f"C2 turnover {'PASS' if c2 else 'FAIL'}   "
              f"=> {'ADOPTABLE' if ok else 'no'}")
    print(f"\n  C1 concentration (reported, not gating): single sleeve holds a median "
          f"{base['names']:.1f} names at {base['maxw']:.1%} max weight; "
          f"worst ever {base['maxwx']:.1%}")
    for n in NS[1:]:
        r = rows[n]
        claim = "CLAIMED" if r["names"] >= base["names"] * 1.5 else "NOT claimed"
        print(f"    N={n:<3} median {r['names']:>4.1f} names, max weight "
              f"{r['maxw']:>5.1%} (worst {r['maxwx']:.1%})  -> benefit {claim}")
    print(f"\n  Adversarial check 4 — is the Sharpe gain vol reduction or return gain?")
    for n in NS:
        r = rows[n]
        print(f"    N={n:<3} CAGR {r['mean']:+.2%}  vol {r['vol']:.2%}  "
              f"Sharpe {r['sharpe']:.2f}")


if __name__ == "__main__":
    main()
