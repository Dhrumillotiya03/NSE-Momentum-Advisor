"""
Pricing the hard-monthly-close mandate (consultant item #4).

Current production rule (HARD CLOSE): at every 21-day rebalance, the WHOLE
book is sold — including names that still rank in the new top-N — and
immediately re-bought. This pays 2x COST on every name every month and
forces every gain to realize as STCG, regardless of whether the position
"logically" continued.

The user's actual mandate (clarified 2026-07-12, see memory
trading-mandate-constraints) forbids INTER-month holding drift — a position
silently carried for 2-3 months with no re-evaluation — NOT continuous
compounding of a name the strategy still wants. This script builds and
tests the variant that satisfies the real mandate without the forced churn:

  LAGGARDS-ONLY REBALANCE: at every 21-day rebalance, re-score the universe.
    - Names in the current book that are STILL in the new sector-capped
      top-N: HOLD (no sell, no re-buy, no cost, no realized gain).
    - Names in the current book that DROP OUT of the new top-N: SELL.
    - New names entering the top-N that weren't already held: BUY.
    - Weights of held-over positions are rebalanced to the new inverse-vol
      target (so sizing discipline isn't lost) — this still costs a partial
      trim/top-up, modeled at COST on the REBALANCED DELTA only, not full
      round-trip.
  This is STILL a full re-evaluation every 21 days (satisfies "no inter-month
  drift without review") — it just skips the pointless sell+rebuy of names
  that were going to be bought right back.

Tax modeling: STCG 20% on any position held <=365 days at exit, LTCG 12.5%
on >365 days (India, post-2024 rates) with no additional indexation. Applied
on a FIFO-lot basis per symbol, matching research_net_returns.py's method
where applicable — NEW here: laggards-only lets lots survive far longer, so
LTCG conversion is now actually possible (impossible under hard-close).

Run from scripts/:  python research_monthly_close_cost.py
"""
import numpy as np
import pandas as pd

import strategy_config as sc
import backtest_portfolio as bp
from walk_forward import make_windows

STCG_RATE = 0.20
LTCG_RATE = 0.125
LTCG_HOLDING_DAYS = 365


def eligible_scores_at(matrix, i, gated_symbols):
    scores, vols = {}, {}
    for sym in gated_symbols:
        col = matrix[sym]
        price_now = col.iloc[i]
        price_past = col.iloc[i - sc.LOOKBACK]
        if pd.isna(price_now) or pd.isna(price_past) or price_past == 0:
            continue
        ret = price_now / price_past - 1
        price_3m = col.iloc[i - 63]
        if pd.isna(price_3m) or price_3m == 0:
            continue
        ret_3m = price_now / price_3m - 1
        if ret <= 0 or ret_3m <= 0:
            continue
        ma50 = col.iloc[i - 50:i].mean()
        if pd.isna(ma50) or price_now < ma50:
            continue
        window = col.iloc[i - 63:i].pct_change(fill_method=None).dropna()
        if len(window) < 40:
            continue
        vol = window.std()
        if vol == 0 or np.isnan(vol):
            continue
        scores[sym] = ret / vol
        vols[sym] = vol
    return scores, vols


def target_weights(top, vols):
    inv = {s: 1.0 / vols[s] for s in top}
    tot = sum(inv.values())
    inv = {s: min(v / tot, sc.MAX_WEIGHT) * tot for s, v in inv.items()}
    tot = sum(inv.values())
    return {s: v / tot for s, v in inv.items()}


def run_hard_close(matrix, index, turnover, sector_map, collect_tax=None):
    """Reimplementation of the production rule, but tracking lot-level tax
    events (production backtest doesn't need tax detail; this does)."""
    dates = matrix.index
    breadth = bp.compute_breadth_series(matrix)
    capital = float(bp.INITIAL_CAPITAL)
    equity = []
    for i in range(sc.LOOKBACK + 21, len(dates) - sc.HOLD, sc.HOLD):
        date = dates[i]
        regime = bp.get_regime(index, date, breadth)
        gated = bp.liquid_symbols_at(turnover, i) & set(matrix.columns)
        scores, vols = eligible_scores_at(matrix, i, gated)
        n = sc.REGIME_NAMES[regime]
        exp = sc.REGIME_EXPOSURE[regime]
        if len(scores) < n:
            equity.append(capital)
            continue
        top = bp.select_top_n_capped(scores, n, sector_map, sc.MAX_PER_SECTOR)
        if not top:
            equity.append(capital)
            continue
        w = target_weights(top, vols)
        invested = capital * exp
        new_capital = capital - invested
        for s in top:
            r = bp.simulate_position_exit(matrix, s, i, matrix[s].iloc[i], sc.HOLD)
            r -= 2 * sc.COST  # full round-trip every month, always
            new_capital += invested * w[s] * (1 + r)
            if collect_tax is not None:
                gain = invested * w[s] * r
                collect_tax.append({"date": date, "sym": s, "gain": gain,
                                    "hold_days": sc.HOLD, "term": "STCG"})
        capital = new_capital
        equity.append(capital)
    return np.array(equity)


def run_laggards_only(matrix, index, turnover, sector_map, collect_tax=None):
    """Names still in top-N carry over (no sell/no cost/no tax event).
    Dropped names sell (full round-trip cost+tax on their held gain since
    ENTRY, not since last rebalance). New names buy in fresh."""
    dates = matrix.index
    breadth = bp.compute_breadth_series(matrix)
    capital = float(bp.INITIAL_CAPITAL)
    equity = []
    # holdings: sym -> {"entry_idx", "entry_price", "shares_value" (rupees at cost)}
    book = {}

    for i in range(sc.LOOKBACK + 21, len(dates) - sc.HOLD, sc.HOLD):
        date = dates[i]
        regime = bp.get_regime(index, date, breadth)
        gated = bp.liquid_symbols_at(turnover, i) & set(matrix.columns)
        scores, vols = eligible_scores_at(matrix, i, gated)
        n = sc.REGIME_NAMES[regime]
        exp = sc.REGIME_EXPOSURE[regime]

        # ---- mark existing book to today's price first ----
        book_value = 0.0
        for s, pos in list(book.items()):
            px = matrix[s].iloc[i]
            if pd.isna(px):
                px = pos["last_price"]
            pos["cur_value"] = pos["shares"] * px
            pos["last_price"] = px
            book_value += pos["cur_value"]

        if len(scores) < n:
            # can't form a valid new book: hold everything as-is, no trades
            equity.append(capital + book_value)
            continue

        top = set(bp.select_top_n_capped(scores, n, sector_map, sc.MAX_PER_SECTOR))
        if not top:
            equity.append(capital + book_value)
            continue

        # ---- SELL: catastrophic stop (checked over the coming HOLD window
        # for names being carried, same as production) is applied at hold-
        # simulation time below; here handle drop-outs + explicit re-eval ----
        held_syms = set(book)
        drop = held_syms - top
        keep = held_syms & top
        new_names = top - held_syms

        for s in drop:
            pos = book.pop(s)
            proceeds = pos["cur_value"] * (1 - sc.COST)
            gain = proceeds - pos["shares"] * pos["entry_price"]
            capital += proceeds
            if collect_tax is not None:
                hold_days = (date - pos["entry_date"]).days
                term = "LTCG" if hold_days > LTCG_HOLDING_DAYS else "STCG"
                collect_tax.append({"date": date, "sym": s, "gain": gain,
                                    "hold_days": hold_days, "term": term})

        # ---- target rupee value per name in the NEW top-N ----
        w = target_weights(list(top), vols)
        total_equity = capital + sum(book[s]["cur_value"] for s in keep)
        invested_target = total_equity * exp
        cash_target = total_equity - invested_target

        # kept names: rebalance to new target weight (buy/sell the delta,
        # cost on the delta only — not a full round trip)
        for s in keep:
            pos = book[s]
            target_val = invested_target * w[s]
            delta = target_val - pos["cur_value"]
            trade_cost = abs(delta) * sc.COST
            capital -= delta + trade_cost
            if delta > 0:
                # buying more: blend entry price for tax-lot purposes (simplify:
                # weighted avg entry, matches record_fill.py's own convention)
                new_shares = pos["shares"] + delta / pos["last_price"]
                pos["entry_price"] = (pos["shares"] * pos["entry_price"] + delta) / new_shares
                pos["shares"] = new_shares
            else:
                sold_val = -delta
                gain = sold_val * (pos["last_price"] - pos["entry_price"]) / pos["last_price"]
                if collect_tax is not None:
                    hold_days = (date - pos["entry_date"]).days
                    term = "LTCG" if hold_days > LTCG_HOLDING_DAYS else "STCG"
                    collect_tax.append({"date": date, "sym": s, "gain": gain,
                                        "hold_days": hold_days, "term": term})
                pos["shares"] -= sold_val / pos["last_price"]
            pos["cur_value"] = pos["shares"] * pos["last_price"]

        # new names: fresh buys
        for s in new_names:
            target_val = invested_target * w[s]
            px = matrix[s].iloc[i]
            if pd.isna(px) or px <= 0:
                continue
            cost = target_val * (1 + sc.COST)
            capital -= cost
            book[s] = {"entry_idx": i, "entry_price": px, "entry_date": date,
                       "shares": target_val / px, "last_price": px,
                       "cur_value": target_val}

        # ---- simulate the HOLD window: catastrophic stop can still fire on
        # any currently-held name (kept or new) ----
        for s in list(book):
            pos = book[s]
            entry_ref = pos["entry_price"]
            col = matrix[s]
            stopped = False
            for off in range(1, sc.HOLD + 1):
                idx = i + off
                if idx >= len(dates):
                    break
                p = col.iloc[idx]
                if pd.isna(p):
                    continue
                if p < entry_ref * sc.CATASTROPHIC_STOP:
                    proceeds = pos["shares"] * p * (1 - sc.COST)
                    gain = proceeds - pos["shares"] * pos["entry_price"]
                    capital += proceeds
                    if collect_tax is not None:
                        hold_days = (dates[idx] - pos["entry_date"]).days
                        term = "LTCG" if hold_days > LTCG_HOLDING_DAYS else "STCG"
                        collect_tax.append({"date": dates[idx], "sym": s, "gain": gain,
                                            "hold_days": hold_days, "term": term})
                    del book[s]
                    stopped = True
                    break
            if not stopped:
                # mark to price at end of hold window for equity curve purposes
                final_idx = min(i + sc.HOLD, len(dates) - 1)
                fp = col.iloc[final_idx]
                if not pd.isna(fp):
                    pos["last_price"] = fp
                    pos["cur_value"] = pos["shares"] * fp

        total_mtm = capital + sum(p["cur_value"] for p in book.values())
        equity.append(total_mtm)

    return np.array(equity)


def tax_summary(tax_events, label):
    df = pd.DataFrame(tax_events)
    if df.empty:
        print(f"  {label}: no tax events")
        return 0.0, 0.0
    stcg_gain = df[df["term"] == "STCG"]["gain"].clip(lower=0).sum()
    ltcg_gain = df[df["term"] == "LTCG"]["gain"].clip(lower=0).sum()
    tax = stcg_gain * STCG_RATE + max(0, ltcg_gain - 125000) * LTCG_RATE
    n_ltcg = (df["term"] == "LTCG").sum()
    print(f"  {label}: {len(df)} realize events ({n_ltcg} LTCG, {len(df) - n_ltcg} STCG), "
          f"est. tax ₹{tax:,.0f}")
    return stcg_gain, ltcg_gain


def main():
    matrix = bp.load_price_matrix()
    index = bp.load_index()
    turnover = bp.load_turnover_matrix(matrix)
    sector_map = bp.load_sector_map()

    print("Running HARD CLOSE (production rule)...")
    tax_hc = []
    eq_hc = bp.run_backtest(matrix, index, turnover)  # validated engine, no tax detail
    _ = run_hard_close(matrix, index, turnover, sector_map, collect_tax=tax_hc)  # tax-detail twin
    p_hc = bp.performance(eq_hc)

    print("Running LAGGARDS-ONLY...")
    tax_lo = []
    eq_lo = run_laggards_only(matrix, index, turnover, sector_map, collect_tax=tax_lo)
    p_lo = bp.performance(eq_lo)

    print(f"\n{'='*66}\nFULL HISTORY — GROSS (pre-tax)\n{'='*66}")
    print(f"{'variant':16s} {'CAGR':>8s} {'Sharpe':>7s} {'MaxDD':>7s}")
    print(f"{'hard close':16s} {p_hc[1]:8.2%} {p_hc[2]:7.2f} {p_hc[3]:7.2%}")
    print(f"{'laggards-only':16s} {p_lo[1]:8.2%} {p_lo[2]:7.2f} {p_lo[3]:7.2%}")

    print(f"\n{'='*66}\nTAX / TRADE FRICTION\n{'='*66}")
    years = p_hc[5]
    stcg_hc, ltcg_hc = tax_summary(tax_hc, "hard close  ")
    stcg_lo, ltcg_lo = tax_summary(tax_lo, "laggards-only")

    def net_cagr(gross_equity, stcg_gain, ltcg_gain, yrs):
        tax = stcg_gain * STCG_RATE + max(0, ltcg_gain - 125000) * LTCG_RATE
        net_final = gross_equity[-1] - tax
        return (net_final / gross_equity[0]) ** (1 / yrs) - 1, net_final

    net_hc, final_hc = net_cagr(eq_hc, stcg_hc, ltcg_hc, years)
    net_lo, final_lo = net_cagr(eq_lo, stcg_lo, ltcg_lo, years)
    print(f"\n{'variant':16s} {'grossCAGR':>10s} {'netCAGR':>9s} {'netFinal':>14s}")
    print(f"{'hard close':16s} {p_hc[1]:10.2%} {net_hc:9.2%} ₹{final_hc:13,.0f}")
    print(f"{'laggards-only':16s} {p_lo[1]:10.2%} {net_lo:9.2%} ₹{final_lo:13,.0f}")
    print(f"\nNet CAGR delta (laggards-only - hard close): {net_lo - net_hc:+.2%}")

    print(f"\n{'='*66}\nWALK-FORWARD (3y windows, 6mo step)\n{'='*66}")
    windows = make_windows(matrix, 3, 6)
    rows = []
    for (s, e) in windows:
        sub = matrix[(matrix.index >= s) & (matrix.index <= e)]
        sub = sub.loc[:, sub.isna().mean() <= 0.20]
        if len(sub) < 300:
            continue
        sub_t = turnover.reindex(sub.index)[sub.columns]
        eq_hc_w = bp.run_backtest(sub, index, sub_t)
        eq_lo_w = run_laggards_only(sub, index, sub_t, sector_map)
        p1, p2 = bp.performance(eq_hc_w), bp.performance(eq_lo_w)
        if p1 is None or p2 is None:
            continue
        rows.append({"start": s.date(), "hc_cagr": p1[1], "hc_sharpe": p1[2],
                     "lo_cagr": p2[1], "lo_sharpe": p2[2]})
    wf = pd.DataFrame(rows)
    wf.to_csv("../data/_research/monthly_close_wf.csv", index=False)
    d = wf["lo_cagr"] - wf["hc_cagr"]
    print(f"{len(wf)} windows")
    print(f"hard close   : mean CAGR {wf['hc_cagr'].mean():7.2%}  mean Sharpe {wf['hc_sharpe'].mean():5.2f}")
    print(f"laggards-only: mean CAGR {wf['lo_cagr'].mean():7.2%}  mean Sharpe {wf['lo_sharpe'].mean():5.2f}")
    print(f"CAGR delta (laggards - hard): mean {d.mean():+.2%}  min {d.min():+.2%}  "
          f"max {d.max():+.2%}  windows worse: {(d < 0).sum()}/{len(wf)}")


if __name__ == "__main__":
    main()
