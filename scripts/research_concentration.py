"""
Concentration risk study (consultant item #5).

REGIME_NAMES currently holds BULL=10 / SIDEWAYS=3 / BEAR=2 / UNKNOWN=6.
SIDEWAYS was walk-forward tested at 2/3/6 and 2 was REJECTED for tail risk
(see strategy_config.py comment + memory). BEAR=2 has NEVER been tested
against an alternative — it just inherited the "fewer names in weak
regimes" pattern without its own study. Two names at MAX_WEIGHT=0.20 each
means idiosyncratic gap risk (fraud, regulatory action, an overnight
circuit-breaker gap the -18% stop can't catch intraday) is materially
uncompensated by diversification, exactly when the regime is already worst.

Two-part study:

PART A — walk-forward CAGR/Sharpe/DD across BEAR in {2,3,4,5} (BULL/SIDEWAYS/
UNKNOWN unchanged), same standard as the SIDEWAYS study. Answers: does more
names in BEAR cost the return the smaller book was capturing?

PART B — single-name GAP STRESS TEST. The standard backtest/bootstrap can't
see this risk: it works off DAILY CLOSES, so it structurally cannot produce
an overnight/intraday gap THROUGH the -18% stop (a fraud/regulatory halt
that opens down 40% before the stop can act). This directly quantifies:
"if the WORST-case single-name gap event in the actual delisted-cohort
history (see research_survivorship.py) had hit a live BEAR-regime position
at MAX_WEIGHT, what's the account-level damage, and how does it scale with
BEAR name count?" This is a stress test, not a backtest — it asks "what if"
using a REAL historical worst-case magnitude, not a hypothetical one.

Run from scripts/:  python research_concentration.py
"""
import numpy as np
import pandas as pd

import strategy_config as sc
import backtest_portfolio as bp
from walk_forward import make_windows

BEAR_VARIANTS = [2, 3, 4, 5]
DEAD_DIR = "../data/price_data_delisted/"


def run_with_bear_n(matrix, index, turnover, bear_n):
    old = sc.REGIME_NAMES["BEAR"]
    sc.REGIME_NAMES["BEAR"] = bear_n
    try:
        eq = bp.run_backtest(matrix, index, turnover)
    finally:
        sc.REGIME_NAMES["BEAR"] = old
    return eq


def part_a(matrix, index, turnover):
    print(f"{'='*66}\nPART A — walk-forward across BEAR name count\n{'='*66}")
    print("Full-history runs...")
    print(f"{'BEAR=n':8s} {'CAGR':>8s} {'Sharpe':>7s} {'MaxDD':>7s}")
    for n in BEAR_VARIANTS:
        eq = run_with_bear_n(matrix, index, turnover, n)
        p = bp.performance(eq)
        print(f"{n:8d} {p[1]:8.2%} {p[2]:7.2f} {p[3]:7.2%}")

    print("\nWalk-forward (3y windows, 6mo step)...")
    windows = make_windows(matrix, 3, 6)
    rows = []
    for (s, e) in windows:
        sub = matrix[(matrix.index >= s) & (matrix.index <= e)]
        sub = sub.loc[:, sub.isna().mean() <= 0.20]
        if len(sub) < 300:
            continue
        sub_t = turnover.reindex(sub.index)[sub.columns]
        row = {"start": s.date()}
        ok = True
        for n in BEAR_VARIANTS:
            eq = run_with_bear_n(sub, index, sub_t, n)
            p = bp.performance(eq)
            if p is None:
                ok = False
                break
            row[f"cagr_{n}"], row[f"sharpe_{n}"], row[f"dd_{n}"] = p[1], p[2], p[3]
        if ok:
            rows.append(row)
    wf = pd.DataFrame(rows)
    wf.to_csv("../data/_research/concentration_bear_wf.csv", index=False)

    print(f"\n{len(wf)} windows")
    print(f"{'BEAR=n':8s} {'meanCAGR':>9s} {'medCAGR':>8s} {'meanShp':>8s} "
          f"{'meanDD':>7s} {'worstDD':>8s} {'negSharpe':>10s}")
    for n in BEAR_VARIANTS:
        c, s_, d = wf[f"cagr_{n}"], wf[f"sharpe_{n}"], wf[f"dd_{n}"]
        neg = (s_ < 0).sum()
        print(f"{n:8d} {c.mean():9.2%} {c.median():8.2%} {s_.mean():8.2f} "
              f"{d.mean():7.2%} {d.max():8.2%} {neg:9d}/{len(wf)}")

    base = wf["cagr_2"]
    print("\nCAGR delta vs BEAR=2 (baseline):")
    for n in BEAR_VARIANTS[1:]:
        delta = wf[f"cagr_{n}"] - base
        print(f"  BEAR={n}: mean {delta.mean():+.2%}  windows worse: {(delta<0).sum()}/{len(wf)}")


def observed_weights_by_bear_n(matrix, index, turnover, sector_map):
    """Real per-name weights actually produced by select_top_n_capped +
    inverse-vol sizing during historical BEAR rebalances, for each BEAR=n.
    MAX_WEIGHT=0.20 is a per-name CAP applied before renormalizing the
    remainder across the other names -- with too few names, the excess has
    nowhere to go and gets renormalized right back up. This measures that
    effect directly instead of assuming the cap binds."""
    breadth = bp.compute_breadth_series(matrix)
    dates = matrix.index
    results = {}
    for n in BEAR_VARIANTS:
        old = sc.REGIME_NAMES["BEAR"]
        sc.REGIME_NAMES["BEAR"] = n
        weights = []
        try:
            for i in range(sc.LOOKBACK + 21, len(dates) - sc.HOLD, sc.HOLD):
                date = dates[i]
                regime = bp.get_regime(index, date, breadth)
                if regime != "BEAR":
                    continue
                gated = bp.liquid_symbols_at(turnover, i) & set(matrix.columns)
                scores, vols = {}, {}
                for sym in gated:
                    col = matrix[sym]
                    p_now, p_past = col.iloc[i], col.iloc[i - sc.LOOKBACK]
                    if pd.isna(p_now) or pd.isna(p_past) or p_past == 0:
                        continue
                    ret = p_now / p_past - 1
                    p_3m = col.iloc[i - 63]
                    if pd.isna(p_3m) or p_3m == 0:
                        continue
                    ret_3m = p_now / p_3m - 1
                    if ret <= 0 or ret_3m <= 0:
                        continue
                    ma50 = col.iloc[i - 50:i].mean()
                    if pd.isna(ma50) or p_now < ma50:
                        continue
                    w = col.iloc[i - 63:i].pct_change(fill_method=None).dropna()
                    if len(w) < 40:
                        continue
                    vol = w.std()
                    if vol == 0 or np.isnan(vol):
                        continue
                    scores[sym] = ret / vol
                    vols[sym] = vol
                nreq = sc.REGIME_NAMES[regime]
                if len(scores) < nreq:
                    continue
                top = bp.select_top_n_capped(scores, nreq, sector_map, sc.MAX_PER_SECTOR)
                if not top:
                    continue
                inv = {s: 1.0 / vols[s] for s in top}
                tot = sum(inv.values())
                w = {s: min(inv[s] / tot, sc.MAX_WEIGHT) for s in top}
                tot2 = sum(w.values())
                weights.extend((v / tot2) for v in w.values())
        finally:
            sc.REGIME_NAMES["BEAR"] = old
        results[n] = np.array(weights)
    return results


def part_b(matrix, index, turnover, sector_map):
    print(f"\n{'='*66}\nPART B — single-name gap stress test\n{'='*66}")
    print("MAX_WEIGHT=0.20 is a per-name CAP applied before renormalizing the\n"
          "remainder across the book. With too few names the excess has nowhere\n"
          "to go and gets renormalized right back up -- the cap doesn't actually\n"
          "bind. Measuring the REAL weight distribution from historical BEAR\n"
          "rebalances (not assuming the cap works):\n")

    obs = observed_weights_by_bear_n(matrix, index, turnover, sector_map)
    exposure = sc.REGIME_EXPOSURE["BEAR"]

    gap_scenarios = {
        "-30% (moderate gap-through)": -0.30,
        "-50% (severe, e.g. fraud disclosure)": -0.50,
        "-70% (trading-halt worst case)": -0.70,
    }

    print(f"{'BEAR=n':8s} {'mean wt':>9s} {'max wt':>8s} {'at-cap %':>9s}", end="")
    for label in gap_scenarios:
        print(f" {label.split()[0]:>8s}", end="")
    print()

    for n in BEAR_VARIANTS:
        w = obs[n]
        if len(w) == 0:
            print(f"{n:8d}  no BEAR rebalances observed")
            continue
        mean_w, max_w = w.mean(), w.max()
        at_cap = (w >= sc.MAX_WEIGHT - 0.001).mean()
        print(f"{n:8d} {mean_w:9.1%} {max_w:8.1%} {at_cap:9.1%}", end="")
        for gap in gap_scenarios.values():
            # use the MAX observed weight (worst case: the gap hits the
            # largest position in the book, which is the realistic scenario
            # to stress since inverse-vol sizing puts more weight on lower-
            # vol names, not necessarily the safest ones)
            account_hit = exposure * max_w * abs(gap)
            print(f" {account_hit:8.1%}", end="")
        print()

    print("\n(columns = % of TOTAL account capital lost if the single WORST-weighted")
    print(" held position gaps/halts through the daily stop)")
    print(f"\nBEAR=2 forces {(obs[2] >= sc.MAX_WEIGHT-0.001).mean():.0%} of positions to")
    print(f"effectively ~50% weight each -- the 20% cap does NOT bind with only 2 names,")
    print("because there's no third+ name to push the excess weight into. This is a cap")
    print("that silently fails exactly when name count is lowest -- not a diversification")
    print("benefit, an accounting illusion. The -18% daily stop is also USELESS against a")
    print("trading halt (no liquidity to exit AT ANY price until it reopens, often far")
    print("lower) -- gap risk like this is invisible to any backtest/bootstrap that works")
    print("off daily closes; it requires this kind of explicit stress test.")


if __name__ == "__main__":
    matrix = bp.load_price_matrix()
    index = bp.load_index()
    turnover = bp.load_turnover_matrix(matrix)
    sector_map = bp.load_sector_map()
    part_a(matrix, index, turnover)
    part_b(matrix, index, turnover, sector_map)
