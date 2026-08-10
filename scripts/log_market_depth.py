"""
log_market_depth.py — snapshot 5-level bid/ask market depth for the F&O-liquid
universe into ../data/market_depth/, one CSV per calendar day.

RESEARCH DATA ONLY. Not part of the trading pipeline; nothing downstream reads
this. Built for Study 4 of the "state of the art" research program (see memory
state-of-the-art-program-2026-08): calibrating research_slippage.py's
square-root impact model constant K against REAL observed spread/depth,
instead of the assumed value the memory documents as "not NSE-calibrated,
would launder an assumption into a validated number."

WHY THIS CAN'T BE BUILT TODAY AND CALIBRATED TOMORROW
-------------------------------------------------------
Kite's market depth is LIVE-ONLY — no historical endpoint exists (verified
2026-08-04, see memory kite-intraday-capability-2026-08: "depth is
snapshot-only"). data/intraday_data/ (3 years of 15-min bars) could be pulled
in one afternoon because Kite backfills price history; depth cannot be
backfilled at all. This script's only job is to start the clock: every session
it doesn't run is data that can never be recovered later. The actual
calibration analysis has to wait until enough sessions have accumulated
(weeks to months, not decided yet — depends on how much cross-sectional /
time variation the resulting panel shows once there's enough of it to look at).

WHY A SNAPSHOT, NOT A CONTINUOUS WEBSOCKET STREAM
-------------------------------------------------------
live_ticker.py already proves KiteTicker(MODE_FULL) delivers depth in every
tick — but that needs a persistent process with a live curses screen. For a
slippage-calibration DATASET, a periodic REST snapshot (kite.quote(), the same
batched call live_quotes.py already uses) is enough: the question is "what
does the book typically look like for this name," not tick-by-tick evolution.
Matches this repo's existing 15-min intraday timer (stockai-intraday), so this
runs as one more oneshot step on that same cadence rather than a new always-on
daemon.

WHAT'S LOGGED PER (date, time, symbol)
-------------------------------------------------------
best bid/ask price+quantity, the full 5-level depth arrays (for a fuller
book-shape picture later), total buy/sell quantity, last traded price. Spread
and top-of-book size are the two inputs research_slippage.py's model actually
needs; the rest is logged now since it costs nothing extra per snapshot and
cannot be recovered retroactively if it turns out useful.

Usage (matches the intraday timer's existing invocation style):
    python log_market_depth.py
"""
import csv
import json
import os
from datetime import datetime

import kite_auth
from core import liquid_universe
from intraday_watch import market_open_now

OUT_DIR = "../data/market_depth/"
FIELDS = [
    "date", "time", "symbol", "last_price",
    "best_bid_price", "best_bid_qty", "best_ask_price", "best_ask_qty",
    "total_buy_qty", "total_sell_qty",
    "depth_json",  # full 5-level bid/ask, json-encoded — see module docstring
]


def _to_kite_symbol(symbol):
    return f"NSE:{symbol[:-3]}" if symbol.upper().endswith(".NS") else f"NSE:{symbol}"


def snapshot():
    if not market_open_now():
        print("Market closed — skipping (depth is only meaningful live).")
        return

    kite = kite_auth.get_kite_client()
    if kite is None:
        print("No cached Kite access token — cannot log depth without it "
              "(this data has no free-feed fallback, unlike live_quotes.py).")
        return

    symbols = sorted(liquid_universe())
    if not symbols:
        print("liquid_universe() returned nothing — skipping.")
        return

    kite_syms = [_to_kite_symbol(s) for s in symbols]
    try:
        quotes = kite.quote(kite_syms)
    except Exception as e:
        print(f"kite.quote() failed: {e}")
        return

    now = datetime.now()
    rows = []
    for sym, ksym in zip(symbols, kite_syms):
        q = quotes.get(ksym)
        if not q:
            continue
        depth = q.get("depth") or {}
        buys = depth.get("buy") or []
        sells = depth.get("sell") or []
        best_bid = buys[0] if buys else {}
        best_ask = sells[0] if sells else {}
        rows.append({
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "symbol": sym,
            "last_price": q.get("last_price"),
            "best_bid_price": best_bid.get("price"),
            "best_bid_qty": best_bid.get("quantity"),
            "best_ask_price": best_ask.get("price"),
            "best_ask_qty": best_ask.get("quantity"),
            "total_buy_qty": q.get("total_buy_quantity") or q.get("buy_quantity"),
            "total_sell_qty": q.get("total_sell_quantity") or q.get("sell_quantity"),
            "depth_json": json.dumps({"buy": buys, "sell": sells}),
        })

    if not rows:
        print("No rows resolved from the quote response — nothing written.")
        return

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"depth_{now.strftime('%Y-%m-%d')}.csv")
    file_exists = os.path.exists(out_path)
    with open(out_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)

    print(f"Logged depth for {len(rows)}/{len(symbols)} symbols -> {out_path}")


if __name__ == "__main__":
    snapshot()
