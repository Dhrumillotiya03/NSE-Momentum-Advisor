"""
Capacity curve — what market impact costs at the capital you'll ACTUALLY run.

WHY THIS EXISTS, given research_slippage.py already ran (2026-07-12).
That study answered "is slippage material at ₹10L?" and its headline
("median order is 0.01% of ADV — small") is quoted throughout CLAUDE.md.
But ₹10L is a CONFIG VALUE (config.yaml `capital`), not a decision: the real
Zerodha book is ₹68L, and the planned momentum carve-out is ₹10-20L. Its own
memory (slippage-2026-07) says "re-run before scaling capital", and nothing
had, because the assumed figure and the actual one live in different files.
This prints the whole curve so the number is read off the capital you pick
rather than inherited from a default.

WHAT IS DIFFERENT FROM research_slippage.py's PART C
1. Parameterised over capital instead of hardcoding ₹10L.
2. Aggregates impact as mean(k*sqrt(%ADV)) over REAL orders, not
   k*sqrt(median %ADV). Impact is concave in size and the order-size
   distribution is right-skewed (p99 is ~40x the median), so collapsing to
   the median first understates the drag. The two differ by enough to matter
   at the top of the range.

THE ONE NUMBER THIS CANNOT SUPPLY IS K. The square-root impact constant is
NOT NSE-calibrated — it is the whole point of the market-depth logger
(log_market_depth.py, Study 4), which as of 2026-08-14 has ~3 snapshots of
real data. So this reports a RANGE (K=5/10/20, the span the literature puts
on liquid single-stock equities) and the answer must be read as a band, not
a point. Folding any single K into production COST would launder an
assumption into a validated number — deliberately NOT done here, same
decision as the original study.

Read-only: runs backtests in-process with a temporarily raised cost and
restores it. Writes nothing, changes no production parameter.

Run from scripts/:  python research_slippage_capacity.py
"""
import numpy as np
import pandas as pd

import backtest_portfolio as bp
import research_slippage as rs
import strategy_config as sc

# The carve-out the user actually plans (₹10-20L), bracketed by smaller and
# much larger levels so the shape of the curve — and where it turns — is
# visible rather than implied.
CAPITAL_LEVELS = [500_000, 1_000_000, 1_500_000, 2_000_000, 3_000_000,
                  5_000_000, 6_800_000, 10_000_000, 20_000_000]
K_VALUES = [5, 10, 20]
BASE_CAPITAL = 1_000_000    # order sizes are collected once here, then scaled


def scaled_pct_adv(base_orders, capital):
    """%ADV at `capital`, scaled from the base collection.

    Exact, not an approximation: an order's value is
    capital * regime_exposure * weight, and ADV does not depend on capital,
    so pct_of_adv is strictly LINEAR in capital. Verified empirically —
    collecting at ₹68L directly gives a median of 0.054% vs 0.008% at ₹10L,
    a ratio of 6.75 against a capital ratio of 6.8. Scaling therefore saves
    one full selection replay per capital level with no loss of fidelity.
    """
    return base_orders["pct_of_adv"].values * (capital / BASE_CAPITAL)


def mean_impact_bps(pct_adv, k):
    """Mean per-order one-side impact in bps under the square-root model.

    Averaged over ORDERS rather than applied to a summary statistic: impact
    is concave in size, so k*sqrt(mean) != mean(k*sqrt(.)), and the order
    distribution is skewed enough that the choice changes the answer.
    """
    return float(np.mean(k * np.sqrt(pct_adv) * 100))


def main():
    matrix = bp.load_price_matrix()
    index = bp.load_index()
    turnover = bp.load_turnover_matrix(matrix)
    sector_map = bp.load_sector_map()

    print("CAPACITY CURVE — market impact vs deployed capital")
    print(f"  universe gate: top {sc.UNIVERSE_TOP_N} by trailing turnover; "
          f"production COST = {bp.COST*10000:.0f} bps/side")
    print("  K is NOT NSE-calibrated (see module docstring) — read the K=5..20")
    print("  span as a band. Narrowing it is what the depth logger is for.\n")

    print("collecting order sizes (one selection replay)...", flush=True)
    base_orders = rs.collect_order_sizes(matrix, index, turnover,
                                         sector_map, BASE_CAPITAL)
    print(f"  {len(base_orders):,} historical orders\n")

    # ---- Part 1: order size vs available liquidity ----
    print("=" * 78)
    print("PART 1 — order size as % of 20d ADV")
    print("=" * 78)
    print(f"{'capital':>12s} {'median':>9s} {'p90':>9s} {'p99':>9s} {'max':>9s}")
    pct_by_cap = {}
    for cap in CAPITAL_LEVELS:
        p = scaled_pct_adv(base_orders, cap)
        pct_by_cap[cap] = p
        # pct_of_adv is a RATIO (0.0008 = 0.08% of ADV); x100 to display as a
        # percentage. mean_impact_bps consumes the RATIO, matching
        # research_slippage.sqrt_impact_bps — do not "fix" one to match the
        # other.
        d = p * 100
        print(f"{'Rs'+format(cap,','):>12s} {np.median(d):8.4f}% "
              f"{np.percentile(d,90):8.4f}% {np.percentile(d,99):8.4f}% "
              f"{d.max():8.4f}%")

    # ---- Part 2: modelled impact ----
    print("\n" + "=" * 78)
    print("PART 2 — mean one-side impact (bps), square-root model")
    print("=" * 78)
    print(f"{'capital':>12s}" + "".join(f"{'K='+str(k):>12s}" for k in K_VALUES)
          + f"{'  vs COST':>12s}")
    impact = {}
    for cap in CAPITAL_LEVELS:
        row = [mean_impact_bps(pct_by_cap[cap], k) for k in K_VALUES]
        impact[cap] = dict(zip(K_VALUES, row))
        worst_mult = row[-1] / (bp.COST * 10000)
        print(f"{'Rs'+format(cap,','):>12s}"
              + "".join(f"{v:11.1f} " for v in row)
              + f"{worst_mult:10.1f}x")
    print("  'vs COST' = K=20 impact as a multiple of the modelled "
          f"{bp.COST*10000:.0f} bps/side commission.")

    # ---- Part 3: what it does to returns ----
    print("\n" + "=" * 78)
    print("PART 3 — backtest with impact folded into an effective COST")
    print("=" * 78)
    base_cost = bp.COST
    try:
        eq = bp.run_backtest_laggards_only(matrix, index, turnover)
        b = bp.performance(eq)
        base_cagr, base_sharpe, base_dd = b[1], b[2], b[3]
        print(f"  baseline (impact excluded): CAGR {base_cagr:.2%}  "
              f"Sharpe {base_sharpe:.2f}  MaxDD {base_dd:.2%}\n")

        print(f"{'capital':>12s} {'K':>4s} {'extra bps':>10s} {'CAGR':>8s} "
              f"{'dCAGR':>8s} {'Sharpe':>7s} {'MaxDD':>8s}")
        results = {}
        for cap in CAPITAL_LEVELS:
            for k in K_VALUES:
                extra = impact[cap][k]
                bp.COST = base_cost + extra / 10000
                eq = bp.run_backtest_laggards_only(matrix, index, turnover)
                p = bp.performance(eq)
                results[(cap, k)] = p[1]
                print(f"{'Rs'+format(cap,','):>12s} {k:4d} {extra:10.1f} "
                      f"{p[1]:8.2%} {p[1]-base_cagr:+8.2%} {p[2]:7.2f} {p[3]:8.2%}")
    finally:
        bp.COST = base_cost   # never leave production cost mutated

    # ---- Part 4: where it starts to bite ----
    print("\n" + "=" * 78)
    print("PART 4 — capacity read")
    print("=" * 78)
    for k in K_VALUES:
        print(f"  K={k}:")
        for cap in CAPITAL_LEVELS:
            drag = base_cagr - results[(cap, k)]
            flag = ""
            if drag >= 0.05:
                flag = "  <-- severe (>5pp of CAGR)"
            elif drag >= 0.02:
                flag = "  <-- material (>2pp)"
            print(f"      Rs{cap:>10,}: -{drag:.2%} CAGR{flag}")
    print("\n  Read the K=5 row as the optimistic case and K=20 as the")
    print("  pessimistic one. The true value is unknown until the depth data")
    print("  calibrates it; until then treat the SPREAD as genuine uncertainty,")
    print("  not as a range to pick a convenient number from.")
    print("\n  Tax is a SEPARATE and larger drag at this cadence — monthly")
    print("  closes make nearly everything short-term. Run research_net_returns.py")
    print("  for the post-STCG figure; the two stack.")


if __name__ == "__main__":
    main()
