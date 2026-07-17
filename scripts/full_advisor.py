import json
import os
import sys

import pandas as pd
import numpy as np

import yaml
from core import load_stock, market_regime as _core_market_regime, compute_atr, compute_rsi, SECTOR_FILE, liquid_universe
from support_resistance import get_levels, strength_label

# Advisory-call ledger: every BUY recommendation this advisor emits is
# appended here (deduped per data-date+symbol) so call quality is
# MEASURABLE — python call_report.py scores fills/targets/stops forward.
# Rows are stamped with the DATA date (each symbol's last completed candle),
# not the run date, per the partial-candle convention (see CLAUDE.md).
CALLS_LOG = "../data/advisor_calls_log.csv"
CALL_COLUMNS = ["date", "symbol", "sector", "regime", "rank", "alpha",
                "price", "buy_at", "target", "stop", "rr", "s_str", "r_str"]
with open("../config.yaml") as f:
    cfg = yaml.safe_load(f)
CAPITAL = cfg["capital"]
RISK_PER_TRADE = cfg["risk"]["risk_per_trade"]


# ---------- MARKET REGIME ----------
#
# Canonical regime formula now lives in core.py (the breadth-gated version,
# same one recommend.py / backtest_portfolio.py / confidence_table.py use).
# This wrapper keeps full_advisor.py's existing 3-value return type (no
# separate HIGH_RISK bucket, no drawdown gate on BULL/BEAR) so the rest of
# this file's control flow is unchanged.

def market_regime():
    regime, _breadth = _core_market_regime()
    return regime


# ---------- DATA LOAD ----------

def load_sectors():
    with open(SECTOR_FILE, "r") as f:
        return json.load(f)


def compute_return(df, days):
    if len(df) < days + 1:
        return np.nan
    return df["Close"].iloc[-1] / df["Close"].iloc[-days-1] - 1


# ---------- SECTOR SCORES ----------

def sector_scores(sectors):
    scores = {}

    for sector, symbols in sectors.items():
        vals = []

        for sym in symbols:
            df = load_stock(sym)
            if df is None:
                continue

            r1 = compute_return(df, 21)
            r3 = compute_return(df, 63)

            if not np.isnan(r1) and not np.isnan(r3):
                vals.append(0.6*r1 + 0.4*r3)

        if vals:
            scores[sector] = np.mean(vals)

    return scores


# ---------- STOCK ALPHA ----------
#
# Formula = core.compute_score's validated scorer (ret_126 / vol_63), gated on
# 3m and 6m returns both positive and price above 50DMA. Walk-forward tested
# across 18 overlapping 3y windows (2015-2026) against the previous 5-factor
# blend: higher/less-variable Sharpe, never negative, better worst-case
# drawdown. The previous blend had higher median raw return but a fatter
# left tail (worse worst-case DD, 2 negative-Sharpe windows) — not worth it
# for risk-adjusted, capital-preservation-first goals.

def compute_alpha(df):
    from core import compute_score
    r = compute_score(df)
    return r["score"] if r is not None else None


def compute_supertrend(df, period=10, multiplier=3):
    high  = df["High"]
    low   = df["Low"]
    close = df["Close"]

    tr = pd.concat([
        high - low,
        abs(high - close.shift()),
        abs(low  - close.shift())
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()

    hl_avg = (high + low) / 2
    upper  = hl_avg + multiplier * atr
    lower  = hl_avg - multiplier * atr

    direction = pd.Series(index=df.index, dtype=float)

    for i in range(1, len(df)):
        if close.iloc[i] > upper.iloc[i - 1]:
            direction.iloc[i] = 1
        elif close.iloc[i] < lower.iloc[i - 1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i - 1]

    supertrend_line = lower.copy()
    supertrend_line[direction == -1] = upper[direction == -1]

    return direction.iloc[-1], supertrend_line.iloc[-1]

def get_trade_levels(df, atr_stop=None):
    current = float(df["Close"].iloc[-1])
    support, resistance, s_str, r_str = get_levels(df)
    stop   = round(support * 0.97, 2)
    risk   = current - stop
    reward = resistance - current
    rr     = round(reward / risk, 2) if risk > 0 else 0
    return current, stop, resistance, support, resistance, rr, s_str, r_str

def position_size(price, atr):
    stop_dist = 2 * atr
    risk_amt = CAPITAL * RISK_PER_TRADE

    shares = int(risk_amt / stop_dist)
    value = shares * price
    stop = price - stop_dist

    return shares, value, stop

# ---------- BUY SCAN ----------

def compute_buy_calls():
    """The advisor's full buy pipeline (regime -> top-3 sectors -> gated
    momentum + S/R levels). Returns (regime, top_sectors, buy_list) where
    buy_list is a list of dicts sorted by alpha descending."""
    regime = market_regime()
    sectors = load_sectors()

    # F&O liquidity gate (see strategy_config.py's Universe gate comment /
    # memory fno-universe-migration) — restrict both sector scoring and the
    # buy scan to names liquid enough to have listed F&O, so recommendations
    # match what's actually tradeable/hedgeable.
    gated = liquid_universe()
    sectors = {sec: [s for s in syms if s in gated] for sec, syms in sectors.items()}
    sectors = {sec: syms for sec, syms in sectors.items() if syms}

    sec_scores = sector_scores(sectors)
    ranked_sec = sorted(sec_scores.items(), key=lambda x: x[1], reverse=True)
    top_sectors = [s[0] for s in ranked_sec[:3]]

    buy_list = []
    for sector, symbols in sectors.items():
        if sector not in top_sectors:
            continue

        for sym in symbols:
            df = load_stock(sym)
            if df is None:
                continue

            alpha = compute_alpha(df)
            if alpha is None or alpha <= 0:
                continue
            if compute_rsi(df["Close"]) > 75:
                continue

            price = df["Close"].iloc[-1]
            atr = compute_atr(df)
            if np.isnan(atr) or atr == 0:
                continue

            shares, value, stop_atr = position_size(price, atr)
            entry, stop, target, support, resistance, rr, s_str, r_str = get_trade_levels(df, atr)

            if rr < 1.0:
                continue
            if s_str < 2:
                continue

            # skip if buy zone is more than 6% away — too far to wait
            dist_to_support = (price - support) / price
            if dist_to_support > 0.06:
                continue

            buy_list.append({
                "symbol": sym, "sector": sector, "alpha": alpha,
                "price": float(price), "shares": shares, "value": value,
                "buy_at": support, "target": resistance, "stop": stop,
                "rr": rr, "s_str": s_str, "r_str": r_str,
                "date": str(df.index[-1].date()),
            })

    buy_list.sort(key=lambda x: x["alpha"], reverse=True)
    return regime, top_sectors, buy_list


def log_calls(regime, buy_list, top_n=8):
    """Append today's top-N calls to the ledger, deduped per (date, symbol)."""
    calls = buy_list[:top_n]
    if not calls:
        return 0
    existing = set()
    if os.path.exists(CALLS_LOG):
        try:
            prev = pd.read_csv(CALLS_LOG, usecols=["date", "symbol"])
            existing = set(zip(prev["date"].astype(str), prev["symbol"]))
        except Exception:
            pass
    rows = []
    for rank, c in enumerate(calls, 1):
        if (c["date"], c["symbol"]) in existing:
            continue
        rows.append({"date": c["date"], "symbol": c["symbol"],
                     "sector": c["sector"], "regime": regime, "rank": rank,
                     "alpha": round(c["alpha"], 4), "price": round(c["price"], 2),
                     "buy_at": round(c["buy_at"], 2), "target": round(c["target"], 2),
                     "stop": round(c["stop"], 2), "rr": c["rr"],
                     "s_str": c["s_str"], "r_str": c["r_str"]})
    if rows:
        pd.DataFrame(rows, columns=CALL_COLUMNS).to_csv(
            CALLS_LOG, mode="a", header=not os.path.exists(CALLS_LOG), index=False)
    return len(rows)


# ---------- MAIN REPORT ----------

def main():
    quiet = "--log" in sys.argv    # nightly pipeline mode: ledger + one line
    regime, top_sectors, buy_list = compute_buy_calls()
    n_logged = log_calls(regime, buy_list)

    if quiet:
        print(f"advisor calls: {len(buy_list[:8])} live, {n_logged} newly logged "
              f"({regime}, sectors: {', '.join(top_sectors)})")
        return

    print("\n==============================")
    print("📊 AI STOCK ADVISOR REPORT")
    print("==============================")

    print("\nMarket Regime:", regime)
    print("Top Sectors:", ", ".join(top_sectors))

    print("\n📈 BUY RECOMMENDATIONS:\n")

    if regime in ["BEAR", "HIGH_RISK"]:
        print("⚠️ Market is currently BEAR/HIGH_RISK.")
        print("Showing best available stocks anyway — use smaller position sizes.\n")

    for c in buy_list[:8]:
        print(f"{'='*42}")
        print(f"  {c['symbol']}")
        print(f"  Alpha Score:    {c['alpha']:.4f}")
        print(f"  Current Price:  ₹{c['price']:.2f}")
        print(f"  Shares:         {c['shares']}")
        print(f"  Position Value: ₹{c['value']:,.0f}")
        print(f"  ─────────────────────────────────────")
        print(f"  📥 Buy at:   ₹{c['buy_at']:.2f}  [{strength_label(c['s_str'])} — {c['s_str']} touches]")
        print(f"  🎯 Target:   ₹{c['target']:.2f}  [{strength_label(c['r_str'])} — {c['r_str']} touches]")
        print(f"  🛑 Stop:     ₹{c['stop']:.2f}  (3% below support)")
        print(f"  ⚖️  R:R:      1:{c['rr']}")
        print()

    if n_logged:
        print(f"({n_logged} call(s) appended to {CALLS_LOG} — score them with: python call_report.py)")


if __name__ == "__main__":
    main()
