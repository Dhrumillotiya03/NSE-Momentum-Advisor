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
import subprocess
from datetime import datetime

import kite_auth
from core import liquid_universe
from intraday_watch import market_open_now

OUT_DIR = "../data/market_depth/"
ALERT_STAMP = "../data/market_depth_alert.json"
FIELDS = [
    "date", "time", "symbol", "last_price",
    "best_bid_price", "best_bid_qty", "best_ask_price", "best_ask_qty",
    "total_buy_qty", "total_sell_qty",
    "depth_json",  # full 5-level bid/ask, json-encoded — see module docstring
]


def _to_kite_symbol(symbol):
    return f"NSE:{symbol[:-3]}" if symbol.upper().endswith(".NS") else f"NSE:{symbol}"


def alert_collection_failed(reason):
    """Notify ONCE PER DAY that depth collection is failing.

    Depth is the one stream in this repo that CANNOT be backfilled — Kite
    exposes no historical depth endpoint (memory
    kite-intraday-capability-2026-08), so a session missed is a session lost
    permanently. Every other Kite consumer degrades silently to yfinance or
    the last close and is none the worse for it; this one just stops
    collecting, and the only symptom was a line in the systemd journal that
    nobody reads. Measured cost of that silence: on 2026-08-10 the access
    token was expired for all 6 intraday firings and the whole session was
    lost without any visible signal.

    Deduped per day via a stamp file — the timer fires every 15 minutes and
    a notification storm would train the operator to ignore it. The fix is
    always the same one manual step (`python kite_auth.py refresh`), so
    saying it once is enough.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        if os.path.exists(ALERT_STAMP):
            with open(ALERT_STAMP) as f:
                if json.load(f).get("date") == today:
                    return
    except Exception:
        pass
    try:
        subprocess.run(
            ["notify-send", "-u", "critical", "stock_ai: market depth NOT collecting",
             f"{reason}\nRun: python kite_auth.py refresh\n"
             f"Depth cannot be backfilled — today's session is lost without it."],
            timeout=5)
    except Exception:
        pass
    try:
        os.makedirs(os.path.dirname(ALERT_STAMP), exist_ok=True)
        with open(ALERT_STAMP, "w") as f:
            json.dump({"date": today, "reason": reason}, f)
    except Exception:
        pass


def snapshot():
    if not market_open_now():
        print("Market closed — skipping (depth is only meaningful live).")
        return

    kite = kite_auth.get_kite_client()
    if kite is None:
        print("No cached Kite access token — cannot log depth without it "
              "(this data has no free-feed fallback, unlike live_quotes.py).")
        alert_collection_failed("No cached Kite access token.")
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
        # An expired/invalid token is the common case and is FIXABLE by the
        # operator; a transient network blip is not worth alerting on.
        msg = str(e).lower()
        if "token" in msg or "api_key" in msg or "session" in msg:
            alert_collection_failed(f"Kite auth rejected: {e}")
        return

    now = datetime.now()
    rows = []
    empty_books = 0
    for sym, ksym in zip(symbols, kite_syms):
        q = quotes.get(ksym)
        if not q:
            continue
        depth = q.get("depth") or {}
        buys = depth.get("buy") or []
        sells = depth.get("sell") or []
        best_bid = buys[0] if buys else {}
        best_ask = sells[0] if sells else {}
        # SKIP EMPTY BOOKS. After the 15:30 close Kite keeps answering
        # quote() but returns an all-zero book ({price:0, quantity:0,
        # orders:0} at every level). Those rows are not "stale depth", they
        # are NO depth — and this file's entire purpose is calibrating
        # spread/impact, so a zero spread and zero size would poison the very
        # constant it exists to measure. Measured on 2026-08-14: 0% empty at
        # 15:00 and 15:16, 85% at 15:30:33, 100% after. Filtering on the BOOK
        # rather than on the clock also covers halts, pre-open and illiquid
        # names for free — the same reasoning as fix_stale_bar.py preferring
        # a measured OHLC ratio over an inferred plateau.
        if not buys or not sells:
            empty_books += 1
            continue
        if not best_bid.get("price") or not best_ask.get("price"):
            empty_books += 1
            continue
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
        print(f"No live order book in the quote response "
              f"({empty_books}/{len(symbols)} empty) — nothing written. "
              f"Normal outside 09:15-15:30; depth is only meaningful live.")
        return
    if empty_books:
        print(f"  skipped {empty_books}/{len(symbols)} symbols with an empty book")

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"depth_{now.strftime('%Y-%m-%d')}.csv")
    file_exists = os.path.exists(out_path)
    with open(out_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)

    print(f"Logged depth for {len(rows)}/{len(symbols)} symbols -> {out_path}")


def coverage():
    """How much depth data actually exists — the honest collection rate.

    Study 4's premise is "start the clock now, analyse later", which quietly
    assumes the clock is ticking. It is only ticking on days the machine is
    ON during market hours AND the Kite token is fresh. This prints what was
    really captured so the calibration is designed against the data that
    exists rather than the data the schedule implies.
    """
    import glob
    files = sorted(glob.glob(os.path.join(OUT_DIR, "depth_*.csv")))
    if not files:
        print("No depth data collected yet.")
        return
    print(f"MARKET-DEPTH COVERAGE — {len(files)} session file(s)")
    print(f"  {'date':>12s} {'snapshots':>10s} {'symbols':>8s} {'window':>15s}")
    total = 0
    for p in files:
        try:
            import pandas as pd
            d = pd.read_csv(p, usecols=["date", "time", "symbol"])
        except Exception as e:
            print(f"  {os.path.basename(p)}: unreadable ({e})")
            continue
        snaps = d["time"].nunique()
        total += snaps
        # Symbols-per-snapshot, not distinct symbols in the file: a snapshot
        # taken near the close can be mostly empty books (filtered out), and
        # a file-level count would hide that as a full session.
        per = d.groupby("time")["symbol"].nunique()
        rng = (f"{per.min()}-{per.max()}" if per.min() != per.max()
               else str(per.min()))
        print(f"  {d['date'].iloc[0]:>12s} {snaps:10d} {rng:>8s} "
              f"{d['time'].min()[:5]}-{d['time'].max()[:5]:>9s}")
    # A full NSE session at a 15-min cadence is ~25 snapshots (09:15-15:30).
    print(f"\n  {total} snapshot(s) total; a full session is ~25.")
    print("  Depth has NO historical endpoint — missed sessions are lost")
    print("  permanently. If this is accumulating slowly, the slippage")
    print("  calibration (Study 4) is further off than the calendar suggests.")


if __name__ == "__main__":
    import sys
    if "coverage" in sys.argv[1:]:
        coverage()
    else:
        snapshot()
