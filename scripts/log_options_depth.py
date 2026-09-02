"""
log_options_depth.py — Gate B of the options-range-selling feasibility check
(PREREG_options_range_selling.md, plan at
~/.claude/plans/my-desire-i-plan-lazy-tiger.md): snapshot live option bid-ask
depth for the F&O-liquid universe into ../data/options_depth/, one CSV/day.

WHY THIS EXISTS, AND WHY NOW
-------------------------------------------------------
Gate A (research_vrp_gate.py) asks whether the option market's implied move
overstates the realised move — a GROSS edge, off settlement prices, that says
nothing about whether that edge survives real transaction costs. A short
strangle's economics are premium collected minus loss-when-breached minus
COSTS, and the cost here is the option bid-ask spread, which is typically far
wider (as a % of premium) than the equity spread this repo already measures
in log_market_depth.py. Gate B measures that spread directly, per name, at
the strike distances a range-selling strategy would actually use.

WHY COLLECTION STARTS NOW, IN PARALLEL WITH GATE A/PHASE 1
-------------------------------------------------------
Same reasoning as log_market_depth.py, which this file's structure mirrors
almost line for line: Kite's option depth is LIVE-ONLY (kite.instruments
("NFO") returns only currently-listed contracts; there is no historical
option depth endpoint at all), so a day this doesn't run is a day of spread
data lost permanently. Waiting for Gate A's full result before starting this
would waste exactly the kind of unrecoverable time that lesson exists to
prevent.

WHAT'S LOGGED, AND WHY THIS STRIKE LADDER SPECIFICALLY
-------------------------------------------------------
Phase 1 (the real terminal-price forecast) doesn't exist yet, so this cannot
snapshot "the strikes the band implies" literally. Instead it snapshots a
small representative ladder — ATM and +-5%/+-10% OTM, both CE and PE, at the
FRONT-MONTH expiry — deliberately wide enough to bracket whatever a real
forecast turns out to recommend, so Phase 2 can later interpolate/select from
already-collected data rather than starting a new collection stream. This is
the same "use a small representative ladder because the exact grid matters
less than not losing more days" trade research_depth_feasibility.py's ORDER_
GRID made for the equity slippage question.

Universe is core.liquid_universe() — ALL F&O-liquid names, not a pre-filtered
subset (the user's explicit instruction: option liquidity is a per-name LABEL
on the output, never a universe cut).

STATUS 2026-09-02: BUILT, BRIEFLY WIRED, THEN UNWIRED — KEPT AS A HANDLE.
-------------------------------------------------------
Gate B existed to net real option spreads against Gate A's edge. Gates A and
A2 then closed the whole options-range-selling program (see the RESULT blocks
in PREREG_options_range_selling.md): the gross mean edge is statistically
indistinguishable from zero and goes negative under a generous cost haircut,
so there is no edge left for spreads to erode and Gate B is moot. This was
removed from stockai-intraday.service the same day to avoid pointless Kite
API load; the equity depth collector (log_market_depth.py, Study 4 — still
live and on the critical path) is untouched.

The file is kept, not deleted, matching this repo's convention for closed
research lines (trail_stop, risk_parity_weights, exit_signal_fn are all kept
as handles). If option-spread data is ever wanted for a DIFFERENT question,
this works as-is — re-add one ExecStart line. Do NOT re-wire it to re-open
the closed range-selling question; AMENDMENT 1 forecloses that.

Usage (standalone; no longer on any timer):
    python log_options_depth.py
    python log_options_depth.py coverage
"""
import csv
import json
import os
import subprocess
import time
from datetime import datetime

import kite_auth
from core import liquid_universe
from intraday_watch import market_open_now
from live_quotes import get_quotes

OUT_DIR = "../data/options_depth/"
ALERT_STAMP = "../data/options_depth_alert.json"
STRIKE_OFFSETS = [0.0, 0.05, 0.10]   # ATM, +-5%, +-10% — see module docstring
QUOTE_CHUNK = 200                     # matches the equity depth logger's batch size
REQUEST_DELAY = 0.34                  # ~3/sec, matches update_prices_kite.py's pacing
FIELDS = [
    "date", "time", "symbol", "spot", "expiry", "strike", "option_type",
    "target_offset_pct", "lot_size", "last_price",
    "best_bid_price", "best_bid_qty", "best_ask_price", "best_ask_qty",
    "spread_pct_of_mid", "depth_json",
]


def _to_kite_symbol(symbol):
    return symbol[:-3] if symbol.upper().endswith(".NS") else symbol.upper()


def alert_collection_failed(reason):
    """Same once-per-day dedupe pattern as log_market_depth.py's, but its OWN
    stamp file — an equity-depth failure and an options-depth failure are
    independent events and must not suppress each other's notification."""
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
            ["notify-send", "-u", "critical", "stock_ai: options depth NOT collecting",
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


def front_month_chain(inst_df, name):
    """This underlying's option rows for the nearest expiry strictly in the
    future, from a kite.instruments("NFO") dump — read from the live
    instrument list, never a hardcoded expiry-day rule (same principle as
    research_vrp_gate.front_month_options)."""
    g = inst_df[(inst_df["name"] == name) & (inst_df["instrument_type"].isin(["CE", "PE"]))]
    if g.empty:
        return None, None
    today = datetime.now().date()
    fut = g[g["expiry"] > today]
    if fut.empty:
        return None, None
    expiry = fut["expiry"].min()
    return fut[fut["expiry"] == expiry], expiry


def pick_targets(chain, spot):
    """For each (offset, side) in the strike ladder, the tradingsymbol whose
    strike is nearest the offset target — one row per ladder point actually
    resolvable in this chain (a thin chain may not have every strike)."""
    strikes = sorted(chain["strike"].unique())
    if not strikes:
        return []
    picks = []
    for off in STRIKE_OFFSETS:
        for side, otype in (("call", "CE"), ("put", "PE")):
            target = spot * (1 + off) if side == "call" else spot * (1 - off)
            nearest = min(strikes, key=lambda k: abs(k - target))
            row = chain[(chain["strike"] == nearest) & (chain["instrument_type"] == otype)]
            if not row.empty:
                picks.append((off, otype, nearest, row.iloc[0]["tradingsymbol"],
                             row.iloc[0]["lot_size"]))
    return picks


def snapshot():
    if not market_open_now():
        print("Market closed — skipping (depth is only meaningful live).")
        return

    kite = kite_auth.get_kite_client()
    if kite is None:
        print("No cached Kite access token — cannot log options depth.")
        alert_collection_failed("No cached Kite access token.")
        return

    symbols = sorted(liquid_universe())
    if not symbols:
        print("liquid_universe() returned nothing — skipping.")
        return

    try:
        import pandas as pd
        inst_df = pd.DataFrame(kite.instruments("NFO"))
        inst_df["expiry"] = pd.to_datetime(inst_df["expiry"], errors="coerce").dt.date
    except Exception as e:
        print(f"kite.instruments('NFO') failed: {e}")
        msg = str(e).lower()
        if "token" in msg or "api_key" in msg or "session" in msg:
            alert_collection_failed(f"Kite auth rejected: {e}")
        return

    spots = get_quotes(symbols)   # {symbol: (price, stale)}

    # Build the full target list across all symbols before quoting, so quotes
    # can be chunked at QUOTE_CHUNK regardless of per-symbol chain size.
    targets = []   # (symbol, spot, expiry, off, otype, strike, tsym, lot)
    skipped_no_chain, skipped_no_spot = 0, 0
    for sym in symbols:
        base = _to_kite_symbol(sym)
        price, stale = spots.get(sym, (None, True))
        if price is None or price <= 0:
            skipped_no_spot += 1
            continue
        chain, expiry = front_month_chain(inst_df, base)
        if chain is None:
            skipped_no_chain += 1
            continue
        for off, otype, strike, tsym, lot in pick_targets(chain, price):
            targets.append((sym, price, expiry, off, otype, strike, tsym, lot))

    if not targets:
        print(f"No option targets resolved (no_chain={skipped_no_chain}, "
              f"no_spot={skipped_no_spot}) — nothing written.")
        return

    now = datetime.now()
    rows = []
    empty_books = 0
    for i in range(0, len(targets), QUOTE_CHUNK):
        if i > 0:
            time.sleep(REQUEST_DELAY)
        chunk = targets[i:i + QUOTE_CHUNK]
        ksyms = [f"NFO:{t[6]}" for t in chunk]
        try:
            quotes = kite.quote(ksyms)
        except Exception as e:
            print(f"  chunk {i//QUOTE_CHUNK}: kite.quote() failed: {e}")
            continue
        for (sym, spot, expiry, off, otype, strike, tsym, lot) in chunk:
            q = quotes.get(f"NFO:{tsym}")
            if not q:
                continue
            depth = q.get("depth") or {}
            buys = depth.get("buy") or []
            sells = depth.get("sell") or []
            best_bid = buys[0] if buys else {}
            best_ask = sells[0] if sells else {}
            # Same "skip empty/one-sided books" rule as log_market_depth.py —
            # a zero-size or one-sided quote is not usable spread data, and
            # options go one-sided far more often than equities (thin OTM
            # strikes with no resting interest on one side).
            bp, ap = best_bid.get("price"), best_ask.get("price")
            if not bp or not ap or ap <= bp:
                empty_books += 1
                continue
            mid = (bp + ap) / 2.0
            rows.append({
                "date": now.strftime("%Y-%m-%d"), "time": now.strftime("%H:%M:%S"),
                "symbol": sym, "spot": spot, "expiry": expiry.strftime("%Y-%m-%d"),
                "strike": strike, "option_type": otype,
                "target_offset_pct": off, "lot_size": lot,
                "last_price": q.get("last_price"),
                "best_bid_price": bp, "best_bid_qty": best_bid.get("quantity"),
                "best_ask_price": ap, "best_ask_qty": best_ask.get("quantity"),
                "spread_pct_of_mid": round((ap - bp) / mid * 100, 4) if mid else None,
                "depth_json": json.dumps({"buy": buys, "sell": sells}),
            })

    if not rows:
        print(f"No usable option books this pass ({empty_books} empty/one-sided, "
              f"no_chain={skipped_no_chain}, no_spot={skipped_no_spot}) — nothing written.")
        return

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"options_depth_{now.strftime('%Y-%m-%d')}.csv")
    file_exists = os.path.exists(out_path)
    with open(out_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)

    print(f"Logged {len(rows)} option quotes ({len(set(r['symbol'] for r in rows))} "
          f"symbols) -> {out_path}  [{empty_books} empty/one-sided skipped, "
          f"{skipped_no_chain} no-chain, {skipped_no_spot} no-spot]")


def coverage():
    """Same purpose as log_market_depth.coverage(): the honest collection
    rate, not the schedule's implied one."""
    import glob
    files = sorted(glob.glob(os.path.join(OUT_DIR, "options_depth_*.csv")))
    if not files:
        print("No options depth data collected yet.")
        return
    import pandas as pd
    print(f"OPTIONS-DEPTH COVERAGE — {len(files)} session file(s)")
    print(f"  {'date':>12s} {'snapshots':>10s} {'symbols':>8s} {'window':>15s}")
    total = 0
    for p in files:
        try:
            d = pd.read_csv(p, usecols=["date", "time", "symbol"])
        except Exception as e:
            print(f"  {os.path.basename(p)}: unreadable ({e})")
            continue
        snaps = d["time"].nunique()
        total += snaps
        per = d.groupby("time")["symbol"].nunique()
        rng = (f"{per.min()}-{per.max()}" if per.min() != per.max() else str(per.min()))
        print(f"  {d['date'].iloc[0]:>12s} {snaps:10d} {rng:>8s} "
              f"{d['time'].min()[:5]}-{d['time'].max()[:5]:>9s}")
    print(f"\n  {total} snapshot(s) total; a full session is ~25.")
    print("  Options depth has NO historical endpoint — missed sessions are")
    print("  lost permanently, same as log_market_depth.py's equity feed.")


if __name__ == "__main__":
    import sys
    if "coverage" in sys.argv[1:]:
        coverage()
    else:
        snapshot()
