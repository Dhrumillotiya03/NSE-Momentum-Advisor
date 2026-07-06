import json
import pandas as pd
import numpy as np

import yaml
from core import load_stock, market_regime as _core_market_regime, compute_atr, SECTOR_FILE
from support_resistance import get_levels, strength_label
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

def compute_rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = -delta.clip(upper=0).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs)).iloc[-1]


# ---------- MAIN REPORT ----------

def main():
    regime = market_regime()
    sectors = load_sectors()

    sec_scores = sector_scores(sectors)
    ranked_sec = sorted(sec_scores.items(), key=lambda x: x[1], reverse=True)

    top_sectors = [s[0] for s in ranked_sec[:3]]

    print("\n==============================")
    print("📊 AI STOCK ADVISOR REPORT")
    print("==============================")

    print("\nMarket Regime:", regime)
    print("Top Sectors:", ", ".join(top_sectors))

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

            buy_list.append((sym, alpha, price, shares, value, stop, target, support, resistance, rr, s_str, r_str))

    buy_list.sort(key=lambda x: x[1], reverse=True)

    from trade_journal import log_trade

    print("\n📈 BUY RECOMMENDATIONS:\n")

    if regime in ["BEAR", "HIGH_RISK"]:
        print("⚠️ Market is currently BEAR/HIGH_RISK.")
        print("Showing best available stocks anyway — use smaller position sizes.\n")

    for s in buy_list[:8]:
        sym, alpha, price, shares, value, stop, target, support, resistance, rr, s_str, r_str = s

        print(f"{'='*42}")
        print(f"  {sym}")
        print(f"  Alpha Score:    {alpha:.4f}")
        print(f"  Current Price:  ₹{price:.2f}")
        print(f"  Shares:         {shares}")
        print(f"  Position Value: ₹{value:,.0f}")
        print(f"  ─────────────────────────────────────")
        print(f"  📥 Buy at:   ₹{support:.2f}  [{strength_label(s_str)} — {s_str} touches]")
        print(f"  🎯 Target:   ₹{resistance:.2f}  [{strength_label(r_str)} — {r_str} touches]")
        print(f"  🛑 Stop:     ₹{stop:.2f}  (3% below support)")
        print(f"  ⚖️  R:R:      1:{rr}")
        print()
        #log_trade(sym, "BUY", price, shares, regime, "N/A", "Alpha signal", pnl=None)


if __name__ == "__main__":
    main()
