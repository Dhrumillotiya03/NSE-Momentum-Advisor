"""
Post-tax / post-cost net-return calibration.

The hard monthly close means EVERY gain is short-term capital gains (20% in
India post-July-2024). This script converts the gross backtest equity curve
into a realistic net-of-tax path so deployment expectations are honest:

  - Realized P&L netted within each Indian financial year (Apr-Mar):
    losses offset gains in the same FY; a net FY loss carries forward
    against future years' gains (8y limit ignored — horizon is shorter).
  - Tax (20% of net positive FY gains after carry-forward) is deducted from
    capital at FY end, so the drag compounds properly.
  - Cost sensitivity: gross+net at 10 / 15 / 20 bps one-way (COST=0.001 is
    the Zerodha delivery cost stack, but excludes slippage).

Run from scripts/:  python research_net_returns.py
"""
import numpy as np
import pandas as pd

import strategy_config as sc
import backtest_portfolio as bp

STCG = 0.20


def rebalance_dates(matrix):
    dates = matrix.index
    return [dates[min(i + sc.HOLD, len(dates) - 1)]
            for i in range(sc.LOOKBACK + 21, len(dates) - sc.HOLD, sc.HOLD)]


def fy_of(ts):
    """Indian financial year label: FY ending Mar 31 of returned year."""
    return ts.year + 1 if ts.month >= 4 else ts.year


def apply_stcg(equity, dates):
    """Replay the period returns on a taxed capital base, netting realized
    P&L within each FY, applying loss carry-forward, deducting tax at FY end."""
    rets = equity[1:] / equity[:-1] - 1
    cap = float(equity[0])
    fy_pnl = 0.0
    carry = 0.0
    cur_fy = fy_of(dates[0])
    path = [cap]

    def settle(fy_pnl, carry, cap):
        if fy_pnl > 0:
            taxable = max(0.0, fy_pnl - carry)
            carry = max(0.0, carry - fy_pnl)
            tax = STCG * taxable
            cap -= tax
        else:
            carry += -fy_pnl
        return carry, cap

    for r, dt in zip(rets, dates[1:]):
        fy = fy_of(dt)
        if fy != cur_fy:
            carry, cap = settle(fy_pnl, carry, cap)
            fy_pnl = 0.0
            cur_fy = fy
        pnl = cap * r
        cap += pnl
        fy_pnl += pnl
        path.append(cap)

    carry, cap = settle(fy_pnl, carry, cap)  # final partial FY
    path[-1] = cap
    return np.array(path)


def cagr_of(path, n_periods):
    years = n_periods * sc.HOLD / 252
    return (path[-1] / path[0]) ** (1 / years) - 1


def main():
    matrix = bp.load_price_matrix()
    index = bp.load_index()
    turnover = bp.load_turnover_matrix(matrix)
    rdates = rebalance_dates(matrix)

    print(f"{'cost/side':>10s} {'grossCAGR':>10s} {'netCAGR':>9s} {'taxDrag':>8s} "
          f"{'grossFinal':>12s} {'netFinal':>12s}")
    for cost in [0.001, 0.0015, 0.002]:
        bp.COST = cost
        eq = bp.run_backtest(matrix, index, turnover)
        dates = rdates[:len(eq)]
        gross = cagr_of(eq, len(eq))
        taxed = apply_stcg(eq, dates)
        net = cagr_of(taxed, len(eq))
        print(f"{cost:>10.4f} {gross:>10.2%} {net:>9.2%} {gross - net:>8.2%} "
              f"₹{eq[-1]:>11,.0f} ₹{taxed[-1]:>11,.0f}")
    bp.COST = sc.COST


if __name__ == "__main__":
    main()
