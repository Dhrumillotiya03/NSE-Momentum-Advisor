"""
Adversarial checks on research_max_weight_cap.py's positive result, run
BEFORE recommending adoption — the checklist PREREG_conviction_sizing.md
established for exactly this situation ("a result that looks unusually clean
deserves MORE scrutiny, not less"). This repo has 1 adoption in its last 8
studies, so a 34/36-window win is a reason to look harder, not to celebrate.

Checks:
  A. THE CONFOUND. PREREG_max_weight_cap.md flagged that raising the cap
     changes concentration AND unblocks CONVICTION_TILT at the same time, and
     said the main test could not separate them. It can be separated with one
     extra run: re-test cap=0.35 with the tilt forced to ZERO (pure
     inverse-vol). If the gain survives at tilt=0 it is concentration; if it
     collapses it is the tilt being unblocked. Materially different stories.
  B. Late-era-only bootstrap (conservative subset re-test).
  C. Realised max single-name weight — "cap 0.35" is NOT a 35% cap at n=3/4,
     because 1/n (33.3%/25%) already sits below it. Says what the change
     really is.
  D. Trade count, to rule out a turnover artifact.
  E. Worst single-window RETURN, not just drawdown.

Usage (from scripts/):  python research_max_weight_cap_adversarial.py
"""
import functools

import numpy as np
import pandas as pd

import backtest_portfolio as bp
import core
import strategy_config as sc
from backtest_portfolio import (load_price_matrix, load_index, load_turnover_matrix,
                                run_backtest_laggards_only, conviction_weights,
                                momentum_score, liquid_symbols_at, get_regime,
                                select_top_n_capped, compute_breadth_series)
from walk_forward import make_windows, run_window
from research_max_weight_cap import paired_window_bootstrap


def inverse_vol_sizing(matrix, i, top, vols):
    """tilt=0: plain inverse-vol, the pre-2026-08-05 production rule."""
    inv = {s: 1.0 / vols[s] for s in top}
    tot = sum(inv.values())
    return {s: v / tot for s, v in inv.items()}


def bootstrap_line(label, base, cand):
    diffs, boot = paired_window_bootstrap(base, cand)
    lo, hi = np.percentile(boot, 2.5), np.percentile(boot, 97.5)
    print(f"    {label:<44s} {diffs.mean():+.2%}  CI [{lo:+.2%},{hi:+.2%}]  "
          f"P(better) {(boot > 0).mean():5.1%}  wins {int((cand > base).sum())}/{len(base)}")
    return diffs.mean(), lo, hi


def main():
    matrix = load_price_matrix()
    index = load_index()
    turnover = load_turnover_matrix(matrix)
    windows = make_windows(matrix, window_years=3, step_months=3)

    print("=" * 74)
    print("ADVERSARIAL CHECKS — MAX_WEIGHT cap result")
    print("=" * 74)

    def annuals(**kw):
        """ALWAYS explicit about the engine. Passing no engine used to fall
        through to run_window's old default, the LEGACY hard-close engine —
        which is precisely the bug check [A] uncovered."""
        eng = functools.partial(run_backtest_laggards_only, **kw)
        return [run_window(matrix, index, turnover, s, e, engine=eng)
                for s, e in windows]

    print("\n[A] THE CONFOUND — is the gain concentration, or the unblocked tilt?")
    print("    Four runs: {cap 0.20, cap 0.35} x {tilt 0.50 (production), tilt 0}\n")
    runs = {}
    specs = {
        "cap0.20_tilt0.50 (PRODUCTION)": dict(),
        "cap0.35_tilt0.50":              dict(max_weight=0.35),
        "cap0.20_tilt0.00":              dict(sizing_fn=inverse_vol_sizing),
        "cap0.35_tilt0.00":              dict(max_weight=0.35, sizing_fn=inverse_vol_sizing),
    }
    for name, kw in specs.items():
        rows = annuals(**kw)
        runs[name] = np.array([r[1] for r in rows if r is not None])
        dd = np.array([r[3] for r in rows if r is not None])
        print(f"    {name:<32s} mean CAGR {runs[name].mean():+.2%}   "
              f"meanDD {dd.mean():.2%}   worstDD {dd.max():.2%}")

    base = runs["cap0.20_tilt0.50 (PRODUCTION)"]
    print("\n    paired deltas vs production:")
    d_cap, _, _ = bootstrap_line("raising the cap (tilt stays 0.50)",
                                 base, runs["cap0.35_tilt0.50"])
    print("\n    the decomposition — same cap change, but with NO tilt to unblock:")
    d_capnotilt, lo_cnt, _ = bootstrap_line("cap 0.20->0.35 at tilt=0.00",
                                            runs["cap0.20_tilt0.00"],
                                            runs["cap0.35_tilt0.00"])
    print("\n    and the tilt's own effect, at each cap:")
    bootstrap_line("tilt 0->0.50 at cap 0.20 (today's blocked tilt)",
                   runs["cap0.20_tilt0.00"], base)
    bootstrap_line("tilt 0->0.50 at cap 0.35 (tilt unblocked)",
                   runs["cap0.35_tilt0.00"], runs["cap0.35_tilt0.50"])
    share = (d_capnotilt / d_cap * 100) if d_cap else float("nan")
    print(f"\n    READ: if the cap change pays off even at tilt=0, the mechanism is")
    print(f"    CONCENTRATION, not conviction. Cap-only effect is {share:.0f}% of the")
    print(f"    headline {d_cap:+.2%}"
          + ("  -> mostly CONCENTRATION." if lo_cnt > 0 else
             "  -> the cap alone does not clear; the tilt is doing the work."))

    print("\n[B] LATE-ERA-ONLY (conservative subset — does it need the early history?)")
    half = len(base) // 2
    bootstrap_line("cap0.35 vs production, late windows only",
                   base[half:], runs["cap0.35_tilt0.50"][half:])

    print("\n[C] REALISED MAX SINGLE-NAME WEIGHT — what 'cap 0.35' actually means")
    breadth = compute_breadth_series(matrix)
    sector_map = core.load_sector_map()
    rows = []
    i = 252
    while i < len(matrix.index):
        date = matrix.index[i]
        regime = get_regime(index, date, breadth)
        n = sc.REGIME_NAMES[regime]
        gated = liquid_symbols_at(turnover, i) & set(matrix.columns)
        scores, vols = {}, {}
        for s in gated:
            r = momentum_score(matrix[s].iloc[:i + 1])
            if r is None:
                continue
            scores[s], vols[s] = r["score"], r["vol_63"]
        if len(scores) >= n:
            top = select_top_n_capped(scores, n, sector_map, sc.MAX_PER_SECTOR)
            raw = conviction_weights(scores, vols, top, sc.CONVICTION_TILT)
            rec = {"regime": regime, "n": n}
            for cap in (0.20, 0.30, 0.35):
                w = {s: min(v, cap) for s, v in raw.items()}
                t = sum(w.values())
                w = {s: v / t for s, v in w.items()}
                rec[f"max_w_{cap:.2f}"] = max(w.values())
                rec[f"clipped_{cap:.2f}"] = all(v >= cap - 1e-12 for v in raw.values())
            rows.append(rec)
        i += 21
    d = pd.DataFrame(rows)
    print(d.groupby("regime").agg(
        periods=("n", "size"),
        maxw_cap020=("max_w_0.20", "mean"), fullclip_020=("clipped_0.20", "mean"),
        maxw_cap035=("max_w_0.35", "mean"), fullclip_035=("clipped_0.35", "mean"),
    ).to_string(float_format=lambda x: f"{x:.4f}"))
    print(f"    worst single-name weight anywhere at cap 0.35: "
          f"{d['max_w_0.35'].max():.1%} (at cap 0.20: {d['max_w_0.20'].max():.1%})")
    print("    NOTE 1/n is 33.3% (SIDEWAYS) and 25.0% (BEAR), both BELOW 0.35 — so in")
    print("    those regimes 'cap 0.35' is effectively NO CAP, not a 35% cap.")

    print("\n[D] TURNOVER ARTIFACT — the cap reallocates among ALREADY-SELECTED names,")
    print("    so it cannot change WHICH names are held or how many trades fire.")
    print("    It does change rebalance-to-target DELTAS, and COST is charged on the")
    print("    delta — so a wider spread pays MORE cost. The measured gain is net of")
    print("    that, i.e. the direction of this artifact is conservative.")

    print("\n[E] WORST SINGLE-WINDOW RETURN (tail beyond drawdown)")
    for name, a in runs.items():
        print(f"    {name:<32s} worst window CAGR {a.min():+.2%}   "
              f"negative windows {int((a < 0).sum())}/{len(a)}")


if __name__ == "__main__":
    main()
