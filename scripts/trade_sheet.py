"""
trade_sheet.py — one sheet, one row per watchlist stock, answering BOTH
"what levels would I trade this at" AND "should I be trading it at all".

WHY THIS EXISTS
---------------
The S/R daily sheet (sr_daily_log.csv) covers all 61 watchlist names but its
S1/R1 answer "how far might price go" — measured 2026-08/09, those levels do
NOT mark reversals (no better than a random price the same distance away) and
a high P(touch) means a NEAR level, not a strong one. Entry/stop/target come
from full_advisor's ATR construction instead. But full_advisor is built around
the strategy's own top-N pipeline, so getting levels for 60+ arbitrary names
meant running it per-stock. This closes that gap.

THE STATUS COLUMN IS THE POINT, NOT THE LEVELS
-----------------------------------------------
Printing Buy/Stop/Target for 61 names would, on its own, recreate exactly the
failure this repo already fixed once: full_advisor used to recommend stocks
the validated strategy would never buy (memory advisor-strategy-divergence-
2026-08 — zero overlap with the strategy's own top-4). The validated strategy
buys only REGIME_NAMES[regime] names (3-10), sector-capped, from the F&O-liquid
universe. So every row carries a STATUS saying whether the strategy actually
wants this name, and levels on a NOT-ELIGIBLE row are reference geometry, not
a recommendation.

  HELD          you own it — the actionable number is the STOP, see the
                held-positions section below the table
  TOP-N         in the strategy's current sector-capped top-N: a genuine
                buy candidate right now
  ELIGIBLE #k   passes the momentum + 50DMA gate, ranked #k of the gated
                universe, but did NOT make the top-N. Not a strategy buy.
  NOT ELIGIBLE  fails the momentum/50DMA gate — the strategy would not buy
                this at any price today
  NOT IN UNIV   outside the F&O-liquid gated universe (core.liquid_universe)
  STALE         CSV more than MAX_STALE_SESSIONS behind the index — levels
                suppressed rather than quoted off an old close (same guard
                full_advisor.compute_buy_calls applies)

LEVELS COME FROM ONE PLACE
---------------------------
full_advisor.get_trade_levels — the ATR construction (shallow pullback entry,
stop capped at the -18% catastrophic stop, target at resistance or an ATR
projection). support_resistance.get_trade_levels delegates to the same
function as of 2026-09-01, so there is exactly one construction in the repo
and this sheet cannot drift from what the advisor prints.

Top-N selection reuses backtest_portfolio.select_top_n_capped with
strategy_config's own REGIME_NAMES/MAX_PER_SECTOR — the same call exit_engine
makes, not a reimplementation.

NOT A MEASUREMENT RECORD. Overwritten each run. The advisory-call ledger
(advisor_calls_log.csv + call_report.py) is the scored record; do not build a
second competing one here.

Usage:
    python trade_sheet.py              # print + write ../data/trade_sheet.csv
    python trade_sheet.py --all        # include every gated-universe name
    python trade_sheet.py --csv-only
"""
import argparse
import os

import pandas as pd

import strategy_config as sc
import full_advisor as fa
from core import (load_stock, load_index, compute_score, compute_atr,
                  compute_rsi, market_regime, scan_universe, liquid_universe,
                  load_portfolio_state)
from support_resistance import get_levels
from sr_daily_logger import WATCHLIST

OUT_CSV = "../data/trade_sheet.csv"
COLUMNS = ["Symbol", "CMP", "Status", "Score", "Buy", "Stop", "Target",
           "RR", "S1", "R1", "RSI"]


def classify(sym, score_row, rank, top_n, held, gated):
    if sym in held:
        return "HELD"
    if sym in top_n:
        return "TOP-N"
    if sym not in gated:
        return "NOT IN UNIV"
    if score_row is None:
        return "NOT ELIGIBLE"
    return f"ELIGIBLE #{rank}" if rank else "ELIGIBLE"


def build(symbols=None):
    regime, _breadth = market_regime()
    n_names = sc.REGIME_NAMES[regime]
    gated = liquid_universe()

    # Same live top-N derivation exit_engine.py uses — sector-capped greedy,
    # not a plain ranked[:n] (a plain slice let live books breach the
    # 2-per-sector cap the backtest enforces).
    eligible = scan_universe()
    from backtest_portfolio import select_top_n_capped, load_sector_map
    scores_only = {s: r["score"] for s, r in eligible.items()}
    top_n = set(select_top_n_capped(scores_only, n_names, load_sector_map(),
                                    sc.MAX_PER_SECTOR))
    ranked = sorted(scores_only, key=scores_only.get, reverse=True)
    rank_of = {s: i + 1 for i, s in enumerate(ranked)}

    state = load_portfolio_state()
    held = set(state.get("positions", {}).keys())

    index = load_index()
    index_last = index.index[-1]

    if symbols is None:
        # TOP-N FIRST, and this is not cosmetic. The watchlist is the FIXED S/R
        # calibration panel (memory sr-daily-log-fixed-panel) — it is not the
        # strategy's universe and has no reason to contain what the strategy
        # currently wants. Measured 2026-09-02: the live BEAR top-4 (HFCL,
        # WELCORP, RADICO, ATHERENERG) were NONE of them on the watchlist. A
        # sheet built from the watchlist alone therefore shows 61 names and
        # nothing actually worth buying — which invites acting on the best
        # merely-ELIGIBLE row instead. Union them so the actionable names are
        # always present.
        symbols = list(dict.fromkeys(sorted(top_n) + sorted(held) + list(WATCHLIST)))

    rows = []
    for sym in symbols:
        df = load_stock(sym)
        if df is None or len(df) < 60:
            continue
        cur = float(df["Close"].iloc[-1])

        # Staleness guard, same rule as full_advisor.compute_buy_calls:
        # measured against the INDEX's last bar, not wall clock (the market
        # can legitimately be closed with zero staleness).
        sessions_behind = len(index.loc[df.index[-1]:index_last]) - 1
        stale = sessions_behind > fa.MAX_STALE_SESSIONS

        score_row = eligible.get(sym)
        status = classify(sym, score_row, rank_of.get(sym), top_n, held, gated)
        if stale:
            status = "STALE"

        row = {"Symbol": sym.replace(".NS", ""), "CMP": round(cur, 2),
               "Status": status,
               "Score": round(score_row["score"], 1) if score_row else None}

        if stale:
            # Levels suppressed rather than quoted off an old close.
            row.update({k: None for k in ["Buy", "Stop", "Target", "RR", "S1", "R1", "RSI"]})
            rows.append(row)
            continue

        rsi_v = float(compute_rsi(df["Close"]))
        atr = compute_atr(df)
        buy, stop, target, sup, res, rr, _s, _r = fa.get_trade_levels(df, atr)
        s_lv, r_lv, s_str, r_str = get_levels(df, symbol=sym, fast=True, cur=cur)
        row.update({
            "Buy": round(buy, 2), "Stop": round(stop, 2),
            "Target": round(target, 2), "RR": round(rr, 2),
            # A `~` marks a PROJECTED level: get_all_levels found no historical
            # pivot inside the reachable band and projected one from the
            # containment band instead (strength 0; pivot-derived levels are
            # >= 1). Measured 2026-09-02: 19/65 supports and 14/65 resistances
            # on this sheet are projections. They are arithmetic, not
            # structure, and the whole point of an S/R column is to say which
            # is which — same reason RSI carries its `!` flag below.
            "S1": (f"{s_lv:.2f}~" if s_str == 0 else f"{s_lv:.2f}") if s_lv else None,
            "R1": (f"{r_lv:.2f}~" if r_str == 0 else f"{r_lv:.2f}") if r_lv else None,
            # RSI_OVERBOUGHT is ADVISORY, not a filter (the hardcoded
            # RSI>75 hard-reject was removed from the advisor 2026-08-01) —
            # so flag it, never drop the row for it.
            "RSI": (f"{rsi_v:.1f}!" if rsi_v >= sc.RSI_OVERBOUGHT
                    else f"{rsi_v:.1f}"),
        })
        rows.append(row)

    df_out = pd.DataFrame(rows, columns=COLUMNS)

    # Order the sheet by what to act on first, not alphabetically.
    order = {"TOP-N": 0, "HELD": 1}
    df_out["_o"] = df_out["Status"].map(lambda s: order.get(s, 2))
    df_out["_r"] = df_out["Score"].fillna(-1e9)
    df_out = df_out.sort_values(["_o", "_r"], ascending=[True, False]).drop(columns=["_o", "_r"])
    return df_out, regime, n_names, state


def print_sheet(df, regime, n_names, state):
    print(f"\n{'='*104}")
    print(f"  TRADE SHEET — regime {regime} (strategy holds {n_names} names) "
          f"| {len(df)} stocks")
    print(f"{'='*104}")
    show = df.fillna("—")
    widths = {c: max(len(c), *(len(str(v)) for v in show[c])) for c in COLUMNS}
    gap = "  "
    print(gap.join(c.ljust(widths[c]) for c in COLUMNS))
    print(gap.join("─" * widths[c] for c in COLUMNS))
    for _, r in show.iterrows():
        print(gap.join(str(r[c]).ljust(widths[c]) for c in COLUMNS))
    print(f"{'='*104}")

    n_top = (df.Status == "TOP-N").sum()
    n_held = (df.Status == "HELD").sum()
    n_elig = df.Status.astype(str).str.startswith("ELIGIBLE").sum()
    n_not = (df.Status == "NOT ELIGIBLE").sum()
    n_stale = (df.Status == "STALE").sum()
    print(f"  TOP-N {n_top}   HELD {n_held}   ELIGIBLE {n_elig}   "
          f"NOT ELIGIBLE {n_not}   STALE {n_stale}")
    print()
    print("  ONLY 'TOP-N' ROWS ARE STRATEGY BUYS TODAY. The validated strategy")
    print(f"  holds {n_names} names in {regime}, sector-capped at "
          f"{sc.MAX_PER_SECTOR}/sector — an ELIGIBLE row passes the momentum")
    print("  gate but did NOT make the cut, and NOT ELIGIBLE fails it outright.")
    print("  Levels on those rows are reference geometry, not recommendations:")
    print("  trading all of them is the exact divergence this repo fixed once")
    print("  already (advisor had ZERO overlap with the strategy's own top-4).")
    print()
    print("  Buy = shallow ATR pullback entry   Stop = ATR, capped at the -18%")
    print("  catastrophic stop   Target = resistance or ATR projection.")
    print("  S1/R1 answer 'how far might it move' — measured NOT to mark")
    print("  reversals, so never use them as the trigger. A `~` means the")
    print("  level is PROJECTED from the containment band, not a real pivot:")
    print("  no historical structure sits within reach on that side.")
    print("  These are the same levels live_ticker.py shows (one construction,")
    print("  core.sr_levels) — it just anchors them to a live quote, so its")
    print("  distances move through the session while these are close-based.")

    # Held positions: the actionable number is the exit, not a fresh entry.
    positions = state.get("positions", {})
    if positions:
        print(f"\n{'-'*104}")
        print("  HELD POSITIONS — the number that matters here is the STOP")
        print(f"{'-'*104}")
        print(f"  {'Symbol':<14}{'Qty':>8}{'Avg entry':>12}{'CMP':>12}"
              f"{'P&L %':>10}{'-18% stop':>12}{'Note':>28}")
        for sym, pos in positions.items():
            d = load_stock(sym)
            if d is None:
                continue
            cur = float(d["Close"].iloc[-1])
            # portfolio_state.json's schema is `entry_price` (record_fill.py is
            # its only writer); the avg_* fallbacks are belt-and-braces only.
            avg = float(pos.get("entry_price") or pos.get("avg_price")
                        or pos.get("avg_entry") or 0)
            qty = pos.get("qty", 0)
            if avg <= 0:
                continue
            pnl = (cur / avg - 1) * 100
            stop = avg * sc.CATASTROPHIC_STOP
            note = pos.get("note", "") or ""
            flag = "STOP BREACHED" if cur < stop else ""
            if not flag:
                try:
                    import profit_watch
                    pf = profit_watch.profit_signals(sym, avg, d, cur=cur)
                    if pf:
                        flag = "PROFIT? " + "/".join(sorted({x["trigger"].split("_")[0] for x in pf}))
                except Exception:
                    pass
            print(f"  {sym.replace('.NS',''):<14}{qty:>8}{avg:>12.2f}{cur:>12.2f}"
                  f"{pnl:>9.1f}%{stop:>12.2f}{(flag or note)[:27]:>28}")
        print("  PROFIT? = a discretionary early-exit flag (profit_watch, display only,")
        print("  NOT a rule — every price-based intra-month exit tested here was rejected)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="every gated-universe name, not just the watchlist")
    ap.add_argument("--csv-only", action="store_true")
    args = ap.parse_args()

    syms = sorted(liquid_universe()) if args.all else None
    df, regime, n_names, state = build(syms)

    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    df.to_csv(OUT_CSV, index=False)
    if not args.csv_only:
        print_sheet(df, regime, n_names, state)
    print(f"\n  -> {OUT_CSV} ({len(df)} rows)")


if __name__ == "__main__":
    main()
