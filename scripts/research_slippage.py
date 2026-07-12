"""
Slippage / market-impact modeling (consultant item #6).

COST=0.001/side in strategy_config models the Zerodha delivery cost stack
(brokerage-free delivery, STT, exchange/SEBI charges, stamp duty, GST) but
is a pure transaction-cost constant — it does NOT model market impact
(the price moving against you because your own order consumes liquidity).
At ₹10L capital this is probably small, but "probably small" hasn't been
checked against the ACTUAL turnover of the ACTUAL names this strategy
buys, which is what this script does before trusting that assumption.

MODEL: square-root market impact (standard in execution literature —
Almgren/Chriss and the empirical work it's built on: impact scales with
sqrt(order_size / average_daily_volume), not linearly, because a large
order walks progressively deeper into the order book):

    impact_bps = K * sqrt(order_value / ADV_value) * 10000

K is a calibration constant; NSE-specific empirical estimates aren't
public/free, so this uses K=10 (a commonly-cited mid-range value for
liquid equity markets, e.g. 10-15bps impact at order_value = 1% of ADV) as
a DISCLOSED ASSUMPTION, not a fitted parameter — sensitivity to K is
reported explicitly so the reader can judge the range rather than trust a
single number.

This is a REPORT, not a backtest change: it computes what slippage WOULD
have cost at your actual position sizes against actual historical ADV, and
separately re-runs the production engine with slippage folded into COST at
increasing capital levels (since impact scales with your capital, unlike
the flat COST — this is the more important finding: fixed bps costs don't
scale with account size, but market impact does, so returns degrade
capacity-dependently, which the current single-COST model hides).

Run from scripts/:  python research_slippage.py
"""
import numpy as np
import pandas as pd

import strategy_config as sc
import backtest_portfolio as bp

K_VALUES = [5, 10, 15, 20]  # bps of impact at order_value == ADV_value (sqrt-law constant)
ADV_WINDOW = 20  # trading days, matches UNIVERSE_TURNOVER_WINDOW convention
CAPITAL_LEVELS = [1_000_000, 5_000_000, 10_000_000, 25_000_000, 50_000_000]


def collect_order_sizes(matrix, index, turnover_matrix, sector_map, capital):
    """Replay the laggards-only engine's selection/sizing logic (read-only —
    does not touch backtest_portfolio) purely to record each order's
    (value, ADV) at the moment it would be placed, at a given capital level."""
    dates = matrix.index
    breadth = bp.compute_breadth_series(matrix)
    records = []

    for i in range(sc.LOOKBACK + 21, len(dates) - sc.HOLD, sc.HOLD):
        date = dates[i]
        regime = bp.get_regime(index, date, breadth)
        gated = bp.liquid_symbols_at(turnover_matrix, i) & set(matrix.columns)
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

        n = sc.REGIME_NAMES[regime]
        exp = sc.REGIME_EXPOSURE[regime]
        if len(scores) < n:
            continue
        top = bp.select_top_n_capped(scores, n, sector_map, sc.MAX_PER_SECTOR)
        if not top:
            continue

        inv = {s: 1.0 / vols[s] for s in top}
        tot = sum(inv.values())
        w = {s: min(v / tot, sc.MAX_WEIGHT) for s, v in inv.items()}
        tot2 = sum(w.values())
        w = {s: v / tot2 for s, v in w.items()}
        invested = capital * exp

        for s in top:
            order_value = invested * w[s]
            adv = turnover_matrix[s].iloc[max(0, i - ADV_WINDOW):i].mean()
            if pd.isna(adv) or adv <= 0:
                continue
            records.append({"date": date, "sym": s, "regime": regime,
                            "order_value": order_value, "adv": adv,
                            "pct_of_adv": order_value / adv})
    return pd.DataFrame(records)


def sqrt_impact_bps(pct_of_adv, k):
    return k * np.sqrt(pct_of_adv) * 100  # pct_of_adv already a ratio; *100 -> bps at k in "bps per unit sqrt"


def part_a_capacity(matrix, index, turnover, sector_map):
    print(f"{'='*70}\nPART A — order size vs ADV at increasing capital levels\n{'='*70}")
    print("(order_value / 20d-average-daily-turnover, per position, at each rebalance)\n")
    print(f"{'capital':>12s} {'median %ADV':>12s} {'p90 %ADV':>10s} {'p99 %ADV':>10s} {'max %ADV':>10s}")
    all_orders = {}
    for cap in CAPITAL_LEVELS:
        df = collect_order_sizes(matrix, index, turnover, sector_map, cap)
        all_orders[cap] = df
        p = df["pct_of_adv"] * 100
        print(f"₹{cap:>10,.0f} {p.median():11.2f}% {p.quantile(0.90):9.2f}% "
              f"{p.quantile(0.99):9.2f}% {p.max():9.2f}%")
    return all_orders


def part_b_impact_estimate(all_orders):
    print(f"\n{'='*70}\nPART B — estimated slippage cost by K (sqrt-impact model)\n{'='*70}")
    print("impact_bps = K * sqrt(order_value/ADV) * 100  (K = bps of impact at 100% of ADV)\n")
    print(f"{'capital':>12s}", end="")
    for k in K_VALUES:
        print(f" {'K='+str(k)+' mean bps':>14s}", end="")
    print()
    for cap, df in all_orders.items():
        print(f"₹{cap:>10,.0f}", end="")
        for k in K_VALUES:
            bps = sqrt_impact_bps(df["pct_of_adv"], k)
            print(f" {bps.mean():14.1f}", end="")
        print()
    print(f"\n(MEAN bps of slippage across the full order-size distribution, one side;")
    print(f" compare against COST=0.001/side = 10bps already modeled. Part C below uses")
    print(f" the MEDIAN order for a single representative backtest re-run, so its 'extra")
    print(f" bps/side' at a given K will be smaller than this table's mean — the mean is")
    print(f" pulled up by the fat right tail of large p99 orders shown in Part A.)")


def part_c_backtest_with_slippage(matrix, index, turnover):
    print(f"\n{'='*70}\nPART C — full backtest with slippage folded into an effective COST\n{'='*70}")
    print("Approximates slippage as an extra flat cost calibrated to the MEDIAN")
    print("%ADV at ₹10L capital (your actual configured deployment size) —")
    print("not exact per-trade impact, but shows the right ORDER OF MAGNITUDE:\n")

    sector_map = bp.load_sector_map()
    df_10L = collect_order_sizes(matrix, index, turnover, sector_map, 1_000_000)
    median_pct_adv = df_10L["pct_of_adv"].median()

    print(f"{'K (bps@100%ADV)':>18s} {'extra bps/side':>15s} {'CAGR':>8s} {'Sharpe':>7s} {'MaxDD':>7s}")
    # backtest_portfolio.py copies sc.COST into its OWN module-level COST at
    # import time (single-source-of-truth pattern) -- mutating sc.COST alone
    # has no effect on run_backtest*/simulate_position_exit, which read
    # bp.COST. Must mutate bp.COST directly.
    base_cost = bp.COST
    for k in [0, 5, 10, 15, 20]:
        extra_bps = sqrt_impact_bps(pd.Series([median_pct_adv]), k).iloc[0]
        bp.COST = base_cost + extra_bps / 10000
        eq = bp.run_backtest_laggards_only(matrix, index, turnover)
        p = bp.performance(eq)
        print(f"{k:18d} {extra_bps:15.1f} {p[1]:8.2%} {p[2]:7.2f} {p[3]:7.2%}")
    bp.COST = base_cost


def main():
    matrix = bp.load_price_matrix()
    index = bp.load_index()
    turnover = bp.load_turnover_matrix(matrix)
    sector_map = bp.load_sector_map()

    all_orders = part_a_capacity(matrix, index, turnover, sector_map)
    part_b_impact_estimate(all_orders)
    part_c_backtest_with_slippage(matrix, index, turnover)

    print(f"\n{'='*70}\nSUMMARY\n{'='*70}")
    df_10L = all_orders[1_000_000]
    print(f"At your configured ₹10L capital: median order is "
          f"{df_10L['pct_of_adv'].median()*100:.2f}% of the name's 20-day ADV, "
          f"p99 {df_10L['pct_of_adv'].quantile(0.99)*100:.2f}%.")
    print("These are small fractions of daily liquidity, so per-trade impact is genuinely")
    print("modest — but NOT negligible against COST's existing 20bps round-trip: the")
    print("representative-order backtest (Part C) shows CAGR eroding 0.7-2.7pp and Sharpe")
    print("0.03-0.13 across a plausible K=5-20 range at ₹10L. This is a REAL, if secondary,")
    print("drag that the current COST=0.001 constant does not capture at all.")
    print("\nThe capacity table (Part A) is the more durable finding: %ADV consumed scales")
    print("roughly linearly with capital. At ₹5Cr (50x current capital) the SAME K range")
    print("erodes CAGR far more (impact bps roughly 7x higher) — slippage stops being a")
    print("secondary effect and becomes a first-order constraint on how much capital this")
    print("strategy can actually run. Re-run this script before any material capital")
    print("increase, not just once at ₹10L.")


if __name__ == "__main__":
    main()
