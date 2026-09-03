"""
REBALANCE TIMING LUCK — how much of this strategy's measured performance is
the calendar rather than the signal?

WHY THIS EXISTS. Every study in this repo compares two configurations on ONE
rebalance grid and argues over 1-3pp of CAGR. But the grid's PHASE is
arbitrary: the engine steps a fixed HOLD-day grid from a fixed start, while
production rotates on the last Tuesday — not even the same phase. Nobody had
measured what the phase itself is worth.

It is worth more than every effect the research programme has adopted or
rejected. That makes it the largest known unmeasured term in every number
this repo quotes, so it is measured here and reported alongside them.

Newfound Research's model (blog.thinknewfound.com/2018/01/quantifying-timing-luck)
gives timing-luck volatility as S*sqrt(T*f*2*(1-corr)) — rising with turnover
and portfolio volatility, falling with rebalance frequency. Monthly,
high-turnover, 3-4 names is the worst case on all three axes, so a large
number here is expected rather than surprising.

Usage (from scripts/):
    python research_timing_luck.py            # full panel, every phase
    python research_timing_luck.py --wf       # phase-averaged walk-forward
"""
import argparse
import numpy as np

import strategy_config as sc
from backtest_portfolio import (load_price_matrix, load_index, load_turnover_matrix,
                                run_backtest_laggards_only, performance)
from walk_forward import make_windows, run_window
import functools

HOLD = sc.HOLD


def full_panel(matrix, index, turnover):
    print(f"FULL PANEL — the identical strategy on all {HOLD} rebalance phases\n")
    print(f"  {'phase':>5} {'CAGR':>9} {'Sharpe':>8} {'maxDD':>9}")
    rows = []
    for p in range(HOLD):
        eq = run_backtest_laggards_only(matrix, index, turnover, phase=p)
        _, cagr, sharpe, dd = performance(eq)[:4]
        rows.append((p, cagr, sharpe, dd))
        print(f"  {p:>5} {cagr:>+9.2%} {sharpe:>8.2f} {dd:>9.2%}", flush=True)

    a = np.array([r[1] for r in rows]); s = np.array([r[2] for r in rows])
    d = np.array([r[3] for r in rows])
    print(f"\n  CAGR    mean {a.mean():+.2%}  sd {a.std(ddof=1):.2%}  "
          f"min {a.min():+.2%}  max {a.max():+.2%}  SPREAD {a.max()-a.min():.2%}")
    print(f"  Sharpe  mean {s.mean():.2f}  sd {s.std(ddof=1):.2f}  "
          f"min {s.min():.2f}  max {s.max():.2f}  SPREAD {s.max()-s.min():.2f}")
    print(f"  maxDD   mean {d.mean():.2%}  sd {d.std(ddof=1):.2%}  "
          f"min {d.min():.2%}  max {d.max():.2%}  SPREAD {d.max()-d.min():.2%}")
    print(f"\n  Phase 0 is the number this repo has always quoted: {a[0]:+.2%}")
    print(f"  Its percentile among phases: {(a < a[0]).mean():.0%}")
    print(f"\n  CONTEXT — effects this repo has argued over, same units:")
    print(f"    conviction sizing  +1.85pp (ADOPTED)   cap 0.35  +0.59pp (rejected)")
    print(f"    sectors.json fix   +0.72pp             trend-quality +1.14pp (rejected)")
    print(f"    timing luck        {(a.max()-a.min())*100:.2f}pp  <- larger than all of them")
    return rows


def walk_forward_phases(matrix, index, turnover, phases):
    """The measurement fix: report the walk-forward across phases, so a quoted
    number is a distribution rather than one arbitrary calendar draw."""
    windows = make_windows(matrix, window_years=3, step_months=6)
    print(f"\nPHASE-AVERAGED WALK-FORWARD — {len(windows)} windows x {len(phases)} phases\n")
    print(f"  {'phase':>5} {'meanCAGR':>10} {'meanSharpe':>11} {'meanDD':>9} {'worstDD':>9} {'neg':>5}")
    out = []
    for p in phases:
        eng = functools.partial(run_backtest_laggards_only, phase=p)
        rows = [run_window(matrix, index, turnover, s, e, engine=eng) for s, e in windows]
        ok = [r for r in rows if r is not None]
        c = np.array([r[1] for r in ok]); sh = np.array([r[2] for r in ok])
        dd = np.array([r[3] for r in ok])
        out.append((p, c.mean(), sh.mean(), dd.mean(), dd.max(), int((c < 0).sum())))
        print(f"  {p:>5} {c.mean():>+10.2%} {sh.mean():>11.2f} {dd.mean():>9.2%} "
              f"{dd.max():>9.2%} {int((c<0).sum()):>5}", flush=True)
    m = np.array([r[1] for r in out])
    print(f"\n  mean CAGR across phases: {m.mean():+.2%}  sd {m.std(ddof=1):.2%}  "
          f"range [{m.min():+.2%}, {m.max():+.2%}]")
    print(f"  QUOTE THE MEAN AND THE SD, not a single phase.")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wf", action="store_true", help="also run the phase-averaged walk-forward")
    ap.add_argument("--wf-phases", type=int, default=7,
                    help="how many evenly-spaced phases for the walk-forward (default 7)")
    args = ap.parse_args()

    matrix = load_price_matrix(); index = load_index()
    turnover = load_turnover_matrix(matrix)
    print("=" * 68); print("REBALANCE TIMING LUCK"); print("=" * 68)
    full_panel(matrix, index, turnover)
    if args.wf:
        step = max(1, HOLD // args.wf_phases)
        walk_forward_phases(matrix, index, turnover, list(range(0, HOLD, step)))


if __name__ == "__main__":
    main()
