"""
Post-tax / post-cost net-return calibration.

Uses the PRODUCTION engine (run_backtest_laggards_only, adopted 2026-07-12).
This script's tax model treats every period's mark-to-market move as a
realized STCG event, which is exact for the legacy hard-close engine (it
really does realize everything every 21 days) but is an OVERSTATEMENT of
tax drag under laggards-only, which has FEWER realize events (see
research_monthly_close_cost.py — 613 vs 738 over the full history) because
carried positions aren't sold and rebought. Treat this script's net-CAGR
as a CONSERVATIVE (slightly too pessimistic) estimate under laggards_only;
research_monthly_close_cost.py has the precise lot-level tax accounting.

  - Realized P&L netted within each Indian financial year (Apr-Mar):
    losses offset gains in the same FY; a net FY loss carries forward
    against future years' gains (8y limit ignored — horizon is shorter).
  - Tax (20% of net positive FY gains after carry-forward) is deducted from
    capital at FY end, so the drag compounds properly.
  - Cost sensitivity: gross+net at 10 / 15 / 20 bps one-way (COST=0.001 is
    the Zerodha delivery cost stack, but excludes slippage).

EXTENDED 2026-07-13 for the three-sleeve production config (75% momentum /
15% GOLDBEES / 10% MON100 + idle-cash yield):
  - momentum sleeve: STCG 20% on FY-netted realized gains (existing logic,
    conservative under laggards-only as above). The sleeve's return now
    includes the idle-cash liquid-ETF yield, which is really taxed at slab
    (~30%) not 20% — small share of the sleeve return, drag slightly
    UNDERSTATED there; the two conservatisms partially offset.
  - gold sleeve (GOLDBEES): taxed at 12.5% on FY gains. Real rule: LTCG
    12.5% only >12m, slab on short-term trims — but the sleeve is
    buy-and-hold with small monthly trims and most gains stay UNREALIZED
    (deferral), so 12.5%/yr-on-all-gains is a disclosed conservative
    approximation.
  - intl sleeve (MON100): same treatment at 12.5% (real rule: LTCG >24m,
    slab below — same small-trims/deferral argument, disclosed).
  - sleeves are taxed independently then blended — cross-sleeve loss
    offset is ignored (conservative).

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


def apply_stcg(equity, dates, rate=STCG):
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
            tax = rate * taxable
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

    print("MOMENTUM SLEEVE ONLY (STCG 20%, FY netting + carry-forward):")
    print(f"{'cost/side':>10s} {'grossCAGR':>10s} {'netCAGR':>9s} {'taxDrag':>8s} "
          f"{'grossFinal':>12s} {'netFinal':>12s}")
    for cost in [0.001, 0.0015, 0.002]:
        bp.COST = cost
        eq = bp.run_backtest_laggards_only(matrix, index, turnover)
        dates = rdates[:len(eq)]
        gross = cagr_of(eq, len(eq))
        taxed = apply_stcg(eq, dates)
        net = cagr_of(taxed, len(eq))
        print(f"{cost:>10.4f} {gross:>10.2%} {net:>9.2%} {gross - net:>8.2%} "
              f"₹{eq[-1]:>11,.0f} ₹{taxed[-1]:>11,.0f}")
    bp.COST = sc.COST

    # ---- three-sleeve production config (75/15/10, adopted 2026-07-13) ----
    print(f"\nPRODUCTION 3-SLEEVE ({1 - sc.GOLD_ALLOC - sc.INTL_ALLOC:.0%} momentum @20% STCG / "
          f"{sc.GOLD_ALLOC:.0%} gold @12.5% / {sc.INTL_ALLOC:.0%} intl @12.5%, "
          f"sleeves taxed independently):")
    eq_m = bp.run_backtest_laggards_only(matrix, index, turnover)
    dates = rdates[:len(eq_m)]
    r_g = bp.load_etf_period_returns(f"../data/etf_data/{sc.GOLD_SYMBOL}.csv", matrix)
    r_i = bp.load_etf_period_returns(f"../data/etf_data/{sc.INTL_SYMBOL}.csv", matrix)
    n = min(len(eq_m) - 1, len(r_g), len(r_i))

    weights = [1 - sc.GOLD_ALLOC - sc.INTL_ALLOC, sc.GOLD_ALLOC, sc.INTL_ALLOC]
    sleeve_eq = [eq_m[:n + 1],
                 np.concatenate([[1.0], np.cumprod(1 + r_g[:n])]),
                 np.concatenate([[1.0], np.cumprod(1 + r_i[:n])])]
    rates = [STCG, 0.125, 0.125]

    def blended_cagr(paths):
        rets = [p[1:] / p[:-1] - 1 for p in paths]
        r = sum(w * x for w, x in zip(weights, rets))
        turn = sum(w * np.abs(x - r) for w, x in zip(weights, rets))
        r = r - turn * sc.COST
        return cagr_of(np.concatenate([[1.0], np.cumprod(1 + r)]), n)

    gross = blended_cagr(sleeve_eq)
    taxed_paths = [apply_stcg(p, dates[:n + 1], rate=rt) for p, rt in zip(sleeve_eq, rates)]
    net = blended_cagr(taxed_paths)
    print(f"  gross CAGR {gross:.2%}  ->  net CAGR {net:.2%}   (tax drag {gross - net:.2%})")
    print(f"  (momentum-sleeve tax model conservative under laggards-only;")
    print(f"   sleeve deferral benefit ignored — real net likely slightly better)")


if __name__ == "__main__":
    main()
