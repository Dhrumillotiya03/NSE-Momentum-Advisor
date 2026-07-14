"""
Universe-wide intraday scanner — the DISCOVERY layer of the mini-quant.

Hedge-fund-style monitoring of the ENTIRE F&O-liquid universe (~200 names,
same point-in-time liquidity gate the strategy trades), every 15 minutes
during market hours, so unusual action surfaces without the user manually
screening stocks:

  JUMP     day change >= +4% vs previous close
  SURGE    cumulative volume today already > 1.5x the 20d average FULL-day
           volume (conviction behind the move)
  NEWHIGH  trading above the prior 52-week high (a momentum-native breakout)

THE QUANT FUSION (what makes this a shortlist, not a tip sheet): every
flagged name is scored with the strategy's own momentum filter
(core.compute_score) and compared to today's top-N score cutoff:

  [QUALIFIES]  passes the entry filter AND scores above the current top-N
               cutoff — the strategy itself would buy this name at the next
               rebalance. The highest-quality flag.
  [ELIGIBLE]   passes the entry filter but below the cutoff.
  [chase-risk] fails the entry filter — a raw jumper. Shown, but labeled:
               daily spikes mean-revert more often than they continue;
               buying these is chasing, not momentum.

ALERT-ONLY, like everything intraday: notify-send fires for QUALIFIES
flags; nothing here places trades or feeds exit_engine/paper_trader/
agent_sim. Every flag is appended to ../data/scanner_log.csv with its
score/rank context so flag QUALITY is measurable after a month or two —
if flagged names systematically outperform, an entry rule can be designed
and walk-forward-tested THEN (evidence first, automation second).

Quotes come from yfinance 15-minute bars, batched per 50 tickers (free,
~15-min delayed). Dedupe: one alert per (day, symbol, flag-type).

Run from scripts/ (normally via the stockai-intraday systemd service):
    python market_scanner.py
"""
import csv
import json
import os
from datetime import datetime

import numpy as np
import pandas as pd

import strategy_config as sc
import core

SCANNER_LOG = "../data/scanner_log.csv"
SEEN_PATH = "../data/scanner_seen.csv"
CUTOFF_CACHE = "../data/scanner_cutoff_cache.json"
PRICE_DIR = "../data/price_data/"

JUMP_PCT = 0.04
SURGE_X = 1.5
CHUNK = 50


def market_open_now():
    now = datetime.now()
    if now.weekday() > 4:
        return False
    hhmm = now.hour * 100 + now.minute
    return 915 <= hhmm <= 1545


def local_daily_stats(symbols):
    """Prev close, prior 52w high, 20d avg volume from the local CSVs."""
    stats = {}
    for sym in symbols:
        path = PRICE_DIR + f"{sym}.csv"
        if not os.path.exists(path):
            continue
        try:
            df = pd.read_csv(path, usecols=["Date", "Close", "High", "Volume"], low_memory=False)
        except ValueError:
            continue
        df = df[pd.to_datetime(df["Date"], errors="coerce").notna()]
        for c in ("Close", "High", "Volume"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["Close"]).sort_values("Date")
        if len(df) < 60:
            continue
        stats[sym] = {
            "prev_close": float(df["Close"].iloc[-1]),
            "high_52w": float(df["High"].tail(252).max()) if df["High"].notna().any()
                        else float(df["Close"].tail(252).max()),
            "avg_vol_20d": float(df["Volume"].tail(20).mean()) if df["Volume"].notna().any() else np.nan,
        }
    return stats


def batch_intraday(symbols):
    """Today's last price + cumulative volume per symbol via batched
    yfinance 15m bars."""
    import yfinance as yf
    out = {}
    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i:i + CHUNK]
        try:
            df = yf.download(" ".join(chunk), period="1d", interval="15m",
                             progress=False, threads=True, auto_adjust=True)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        closes = df["Close"] if "Close" in df else None
        vols = df["Volume"] if "Volume" in df else None
        if closes is None:
            continue
        if isinstance(closes, pd.Series):   # single-ticker chunk
            closes, vols = closes.to_frame(chunk[0]), vols.to_frame(chunk[0])
        for sym in closes.columns:
            c = closes[sym].dropna()
            if not len(c):
                continue
            v = vols[sym].dropna().sum() if (vols is not None and sym in vols) else np.nan
            out[sym] = {"last": float(c.iloc[-1]), "cum_vol": float(v)}
    return out


def topn_cutoff_today():
    """Today's top-N minimum score (cached per day — scan_universe over the
    whole gated universe is the expensive step)."""
    today = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(CUTOFF_CACHE):
        with open(CUTOFF_CACHE) as f:
            cache = json.load(f)
        if cache.get("date") == today:
            return cache["cutoff"], cache["regime"], cache["n"]
    regime, _b = core.market_regime()
    n = sc.REGIME_NAMES[regime]
    results = core.scan_universe()
    ranked = sorted((r["score"] for r in results.values()), reverse=True)
    cutoff = ranked[n - 1] if len(ranked) >= n else (ranked[-1] if ranked else 0.0)
    with open(CUTOFF_CACHE, "w") as f:
        json.dump({"date": today, "cutoff": cutoff, "regime": regime, "n": n}, f)
    return cutoff, regime, n


def load_seen():
    if not os.path.exists(SEEN_PATH):
        return set()
    with open(SEEN_PATH) as f:
        return {(r["date"], r["symbol"], r["type"]) for r in csv.DictReader(f)}


def mark_seen(rows):
    new = not os.path.exists(SEEN_PATH)
    with open(SEEN_PATH, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "symbol", "type"])
        if new:
            w.writeheader()
        for r in rows:
            w.writerow(r)


def notify(title, body):
    import subprocess
    try:
        subprocess.run(["notify-send", title, body], timeout=5)
    except Exception:
        pass


def main():
    if not market_open_now():
        print("[scanner] market closed — nothing to do")
        return

    universe = sorted(core.liquid_universe())
    stats = local_daily_stats(universe)
    live = batch_intraday([s for s in universe if s in stats])
    if not live:
        print("[scanner] no intraday data returned (yfinance issue?) — skipping")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    now_hm = datetime.now().strftime("%H:%M")
    seen = load_seen()
    flags = []

    for sym, q in live.items():
        st = stats[sym]
        chg = q["last"] / st["prev_close"] - 1
        types = []
        if chg >= JUMP_PCT:
            types.append("JUMP")
        if (chg >= 0.01   # up-move only: heavy volume on a down day is
                          # distribution, not a "potential winner" — and the
                          # held-book downside is intraday_watch's job
                and not np.isnan(q["cum_vol"]) and not np.isnan(st["avg_vol_20d"])
                and st["avg_vol_20d"] > 0 and q["cum_vol"] > SURGE_X * st["avg_vol_20d"]):
            types.append("SURGE")
        if q["last"] > st["high_52w"]:
            types.append("NEWHIGH")
        new_types = [t for t in types if (today, sym, t) not in seen]
        if new_types:
            flags.append((sym, chg, q, st, new_types))

    if not flags:
        print(f"[scanner] {now_hm}: {len(live)} names scanned, nothing unusual")
        return

    cutoff, regime, n = topn_cutoff_today()
    rows, new_seen, qualifies = [], [], []
    for sym, chg, q, st, types in sorted(flags, key=lambda x: -x[1]):
        df = core.load_stock(sym)
        r = core.compute_score(df) if df is not None else None
        if r is None:
            tag, score = "chase-risk", None
        elif r["score"] >= cutoff:
            tag, score = "QUALIFIES", r["score"]
        else:
            tag, score = "ELIGIBLE", r["score"]

        line = (f"{sym.replace('.NS', ''):12s} {chg:+6.1%} today  ₹{q['last']:.2f}  "
                f"[{','.join(types)}]  [{tag}"
                + (f" score {score:.1f} vs top-{n} cutoff {cutoff:.1f}" if score else "") + "]")
        print(f"[scanner] {line}")
        if tag == "QUALIFIES":
            qualifies.append(line)
        for t in types:
            new_seen.append({"date": today, "symbol": sym, "type": t})
        rows.append({"date": today, "time": now_hm, "symbol": sym,
                     "day_change": round(chg, 4), "price": round(q["last"], 2),
                     "flags": "+".join(types), "verdict": tag,
                     "score": round(score, 2) if score else "",
                     "topn_cutoff": round(cutoff, 2), "regime": regime})

    mark_seen(new_seen)
    new_file = not os.path.exists(SCANNER_LOG)
    with open(SCANNER_LOG, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "time", "symbol", "day_change", "price",
                                          "flags", "verdict", "score", "topn_cutoff", "regime"])
        if new_file:
            w.writeheader()
        for row in rows:
            w.writerow(row)

    if qualifies:
        notify("stock_ai SCANNER — strategy-grade movers",
               "\n".join(q[:100] for q in qualifies[:4]))
    print(f"[scanner] {len(rows)} flag(s) logged"
          + (f", {len(qualifies)} QUALIFIES (notified)" if qualifies else ""))


def report():
    """Shadow evaluation of every logged flag: forward return from the flag
    price to 5 sessions later and to the latest close, vs Nifty over the
    same window — grouped by verdict. This is how the scanner earns (or
    loses) a future role as an entry signal: if QUALIFIES flags
    systematically outperform and chase-risk flags don't, that's the
    evidence base for designing a walk-forward-testable rule. The flags
    NEVER feed the agent-sim's trader — the sim tests the strategy; this
    report tests the scanner, separately."""
    if not os.path.exists(SCANNER_LOG):
        print("No scanner flags logged yet.")
        return
    log = pd.read_csv(SCANNER_LOG)
    log = log.drop_duplicates(subset=["date", "symbol"], keep="first")

    idx = pd.read_csv("../data/index_data/nifty50.csv", low_memory=False)
    idx["Date"] = pd.to_datetime(idx["Date"], errors="coerce")
    idx["Close"] = pd.to_numeric(idx["Close"], errors="coerce")
    idx = idx.dropna(subset=["Date", "Close"]).sort_values("Date").set_index("Date")["Close"]

    rows = []
    for _, f in log.iterrows():
        path = PRICE_DIR + f["symbol"] + ".csv"
        if not os.path.exists(path):
            continue
        px = pd.read_csv(path, usecols=["Date", "Close"], low_memory=False)
        px["Date"] = pd.to_datetime(px["Date"], errors="coerce")
        px["Close"] = pd.to_numeric(px["Close"], errors="coerce")
        px = px.dropna().sort_values("Date").set_index("Date")["Close"]
        after = px[px.index > pd.Timestamp(f["date"])]
        if not len(after):
            continue
        r5 = after.iloc[min(4, len(after) - 1)] / f["price"] - 1
        r_now = after.iloc[-1] / f["price"] - 1
        ni = idx[idx.index > pd.Timestamp(f["date"])]
        nifty_now = (ni.iloc[-1] / ni.iloc[0] - 1) if len(ni) > 1 else 0.0
        rows.append({"verdict": f["verdict"], "symbol": f["symbol"], "date": f["date"],
                     "flag_px": f["price"], "r5": r5, "r_now": r_now,
                     "alpha_now": r_now - nifty_now, "n_days": len(after)})

    if not rows:
        print("Flags logged but no forward sessions yet — re-run in a few days.")
        return
    df = pd.DataFrame(rows)
    print(f"SCANNER SHADOW EVALUATION — {len(df)} unique flags, "
          f"{log['date'].min()} -> {log['date'].max()}")
    for verdict in ["QUALIFIES", "ELIGIBLE", "chase-risk"]:
        g = df[df["verdict"] == verdict]
        if not len(g):
            continue
        print(f"\n  {verdict} ({len(g)} flags): mean +5d {g['r5'].mean():+.1%} | "
              f"mean since flag {g['r_now'].mean():+.1%} | mean alpha vs Nifty "
              f"{g['alpha_now'].mean():+.1%} | positive-alpha {(g['alpha_now'] > 0).mean():.0%}")
        for _, r in g.sort_values("alpha_now", ascending=False).head(5).iterrows():
            print(f"    {r['date']} {r['symbol'].replace('.NS', ''):12s} "
                  f"flag ₹{r['flag_px']:.2f} -> now {r['r_now']:+.1%} "
                  f"(alpha {r['alpha_now']:+.1%}, {r['n_days']}d)")
    print("\n  (Evaluation matures with time — judge at month-end, not day 3. Promotion")
    print("   to an entry rule requires walk-forward evidence, not this table alone.)")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        report()
    else:
        main()
