"""
Tests PREREG_residual_momentum.md — does ranking on MARKET-MODEL RESIDUAL
momentum beat ranking on total-return momentum?

Blitz, Huij & Martens (2011) report residual momentum earning ~2x the
risk-adjusted profit of total-return momentum, by stripping the time-varying
factor exposure that conventional momentum carries. Only the market factor is
buildable here (no point-in-time fundamentals — see
PREREG_trend_quality_factor.md), so this is the CAPM-residual version.

Uses the existing score_fn hook, which RE-RANKS an already-eligible pool —
so production's 50DMA + positive-momentum gate is untouched and any delta is
attributable to ranking alone.

The feasibility gate (corr 0.864 with the production score) argues this will
FAIL: trend-quality correlated 0.77 and was rejected as too collinear. The one
counter-argument is that at n=3/n=4 the top-of-book overlap is only 1/3 and
2/4, so the books genuinely differ where the strategy trades.

Usage (from scripts/):  python research_residual_momentum.py [--phases 0,5,10,15]
"""
import argparse, functools
import numpy as np
import pandas as pd

import strategy_config as sc
from backtest_portfolio import (load_price_matrix, load_index, load_turnover_matrix,
                                run_backtest_laggards_only)
from walk_forward import make_windows, run_window
from research_max_weight_cap import paired_window_bootstrap

EST_DAYS = 756      # ~3y, matching Blitz et al.'s estimation window
MIN_OBS  = 500
LB       = sc.LOOKBACK


def make_resid_scorer(index, mode, blend=0.5, tie=0.10):
    """Returns a score_fn. `index` is the Nifty close series."""
    mkt_ret = np.log(index).diff()

    def resid_scores(eligible):
        out = {}
        for sym, (close_window, _res) in eligible.items():
            c = close_window.dropna()
            if len(c) < EST_DAYS // 2:
                continue
            sr = np.log(c).diff()
            mr = mkt_ret.reindex(sr.index)
            df = pd.concat([sr, mr], axis=1).dropna()
            if len(df) < MIN_OBS:
                continue
            est = df.iloc[-EST_DAYS:]
            x = est.iloc[:, 1].to_numpy(); y = est.iloc[:, 0].to_numpy()
            vx = x.var()
            if vx <= 0:
                continue
            beta = np.cov(y, x, bias=True)[0, 1] / vx
            alpha = y.mean() - beta * x.mean()
            e = df.iloc[:, 0].to_numpy() - alpha - beta * df.iloc[:, 1].to_numpy()
            w = e[-LB:]
            if len(w) < LB:
                continue
            sd = w.std(ddof=1)
            if sd <= 0:
                continue
            out[sym] = w.sum() / (sd * np.sqrt(LB))
        return out

    def score_fn(eligible):
        base = {s: r["score"] for s, (_c, r) in eligible.items()}
        resid = resid_scores(eligible)
        common = [s for s in base if s in resid]
        if len(common) < 5:
            return base                                  # degrade to production
        if mode == "replace":
            # names without a residual score must not be silently promoted;
            # rank them below everything that has one.
            lo = min(resid.values()) - 1.0
            return {s: resid.get(s, lo) for s in base}
        rb = pd.Series({s: base[s] for s in common}).rank(pct=True)
        rr = pd.Series(resid).reindex(common).rank(pct=True)
        if mode == "blend":
            merged = (1 - blend) * rb + blend * rr
        elif mode == "tiebreak":
            # reorder only within near-ties of the production score
            span = rb.max() - rb.min() or 1.0
            merged = rb + tie * span * (rr - rr.mean()) * 0.999
        else:
            raise ValueError(mode)
        lo = merged.min() - 1.0
        return {s: float(merged.get(s, lo)) for s in base}

    return score_fn


def evaluate(name, score_fn, matrix, index, turnover, windows, baseline_rows, phase=0):
    eng = functools.partial(run_backtest_laggards_only, score_fn=score_fn, phase=phase)
    rows = [run_window(matrix, index, turnover, s, e, engine=eng) for s, e in windows]
    pairs = [(b, r) for b, r in zip(baseline_rows, rows) if b is not None and r is not None]
    if len(pairs) < len(windows) * 0.8:
        print(f"  {name}: too many windows failed ({len(pairs)}/{len(windows)})")
        return None
    ba = np.array([b[1] for b, _ in pairs]); ca = np.array([r[1] for _, r in pairs])
    bd = np.array([b[3] for b, _ in pairs]); cd = np.array([r[3] for _, r in pairs])
    diffs, boot = paired_window_bootstrap(ba, ca)
    lo, hi = np.percentile(boot, 2.5), np.percentile(boot, 97.5)
    wins = int((ca > ba).sum()); n = len(pairs)
    ddm = (cd - bd).mean()
    ok = (lo > 0) and (wins >= n * 12 / 19) and (ddm <= 0.02)
    print(f"  {name:<20} delta {diffs.mean():+7.2%}  CI [{lo:+.2%},{hi:+.2%}]  "
          f"wins {wins:>2}/{n}  meanDD {ddm:+.2%}  -> {'PASS' if ok else 'REJECT'}")
    return {"config": name, "delta": diffs.mean(), "ci": (lo, hi), "wins": wins,
            "n": n, "dd": ddm, "pass": ok}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phases", default="0", help="comma-separated rebalance phases")
    args = ap.parse_args()
    phases = [int(x) for x in args.phases.split(",")]

    matrix = load_price_matrix(); index = load_index()
    turnover = load_turnover_matrix(matrix)
    windows = make_windows(matrix, window_years=3, step_months=3)
    print("=" * 76)
    print("RESIDUAL MOMENTUM — pre-registered (PREREG_residual_momentum.md)")
    print("=" * 76)
    print(f"{len(windows)} windows; phases tested: {phases}\n")

    configs = {
        "resid_replace":     make_resid_scorer(index, "replace"),
        "resid_blend_50":    make_resid_scorer(index, "blend", blend=0.5),
        "resid_tiebreak_10": make_resid_scorer(index, "tiebreak", tie=0.10),
    }

    all_res = {}
    for p in phases:
        print(f"--- phase {p} ---")
        eng = functools.partial(run_backtest_laggards_only, phase=p)
        base = [run_window(matrix, index, turnover, s, e, engine=eng) for s, e in windows]
        bok = [r for r in base if r is not None]
        print(f"  baseline mean CAGR {np.mean([r[1] for r in bok]):+.2%}")
        for nm, fn in configs.items():
            r = evaluate(nm, fn, matrix, index, turnover, windows, base, phase=p)
            if r: all_res.setdefault(nm, []).append((p, r))
        print()

    print("=" * 76); print("SUMMARY"); print("=" * 76)
    for nm, rs in all_res.items():
        passes = [r["pass"] for _, r in rs]
        deltas = [r["delta"] for _, r in rs]
        print(f"  {nm:<20} passes {sum(passes)}/{len(passes)} phases   "
              f"deltas {['%+.2f%%' % (d*100) for d in deltas]}")
    if len(phases) > 1:
        print("\n  PHASE GATE: a config must keep a positive delta on EVERY phase.")
        print("  Timing luck is ~9pp of CAGR, so a 1-2pp win on one phase is noise.")
    else:
        print("\n  Single phase only — per the prereg, any PASS here must be re-run")
        print("  on >=3 more phases before it can be believed.")


if __name__ == "__main__":
    main()
