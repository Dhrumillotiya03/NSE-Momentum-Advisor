"""
sr_backtest_filtered.py
-----------------------
Same walk-forward backtest BUT only tests S/R levels on bars where:
  1. Price is above 50-day MA  (your trend filter)
  2. 126-day momentum is positive  (your momentum filter)
  3. Market is not in BEAR regime  (Nifty 50 MA filter)

This answers: "how accurate are S/R levels on stocks I'd actually trade?"

Usage:
    python sr_backtest_filtered.py
    python sr_backtest_filtered.py HDFCBANK.NS TCS.NS
"""

import os, sys
import numpy as np
import pandas as pd
from support_resistance import get_levels

PRICE_DIR  = "../data/price_data/"
INDEX_PATH = "../data/index_data/nifty50.csv"
FORWARD_DAYS = 21
TOUCH_ZONE   = 0.01
BOUNCE_PCT   = 0.015
TEST_MONTHS  = 12
MIN_DATA     = 300


# ──────────────────────────────────────────────
# LOADERS
# ──────────────────────────────────────────────

def load_stock(symbol):
    path = os.path.join(PRICE_DIR, f"{symbol}.csv")
    if not os.path.exists(path): return None
    df = pd.read_csv(path, parse_dates=["Date"])
    df = df.sort_values("Date").set_index("Date")
    for col in ["Close", "High", "Low"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["Close", "High", "Low"])


def load_nifty_regimes():
    """
    Returns a Series indexed by date → regime string (BULL/SIDEWAYS/BEAR).
    Computed daily from Nifty 50.
    """
    df = pd.read_csv(INDEX_PATH, parse_dates=["Date"])
    df = df.sort_values("Date").set_index("Date")
    close = pd.to_numeric(df["Close"], errors="coerce")

    ma50  = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()
    rmax  = close.rolling(200).max()
    dd    = (close - rmax) / rmax

    regimes = pd.Series(index=close.index, dtype=str)
    for i in range(len(close)):
        p   = close.iloc[i]
        m50 = ma50.iloc[i]
        m200= ma200.iloc[i]
        d   = dd.iloc[i]
        if pd.isna(m50) or pd.isna(m200):
            regimes.iloc[i] = "UNKNOWN"
        elif p > m50 > m200 and d > -0.1:
            regimes.iloc[i] = "BULL"
        elif p < m200 and d < -0.2:
            regimes.iloc[i] = "BEAR"
        else:
            regimes.iloc[i] = "SIDEWAYS"

    return regimes


# ──────────────────────────────────────────────
# FILTERS
# ──────────────────────────────────────────────

def passes_filters(past_df, regimes, test_date):
    """
    Returns True if this stock/date combo would pass your strategy filters.
    """
    # 1. Regime: not BEAR
    if test_date in regimes.index:
        regime = regimes.loc[:test_date].iloc[-1]
        if regime == "BEAR":
            return False

    if len(past_df) < 130:
        return False

    close = past_df["Close"]

    # 2. Price above 50-day MA
    ma50 = close.rolling(50).mean().iloc[-1]
    if close.iloc[-1] < ma50:
        return False

    # 3. 126-day momentum positive
    if len(close) >= 127:
        mom = close.iloc[-1] / close.iloc[-127] - 1
        if mom <= 0:
            return False

    return True


# ──────────────────────────────────────────────
# LEVEL TESTS
# ──────────────────────────────────────────────

def test_support(future_df, support):
    touch  = support * (1 + TOUCH_ZONE)
    broke  = support * (1 - TOUCH_ZONE)
    bounce = support * (1 + BOUNCE_PCT)
    touched = False
    for _, row in future_df.iterrows():
        if row["Low"] <= touch:
            touched = True
        if touched:
            if row["Close"] >= bounce: return True
            if row["Close"] <  broke:  return False
    return None


def test_resistance(future_df, resistance):
    touch  = resistance * (1 - TOUCH_ZONE)
    broke  = resistance * (1 + TOUCH_ZONE)
    reject = resistance * (1 - BOUNCE_PCT)
    touched = False
    for _, row in future_df.iterrows():
        if row["High"] >= touch:
            touched = True
        if touched:
            if row["Close"] <= reject: return True
            if row["Close"] >  broke:  return False
    return None


# ──────────────────────────────────────────────
# PER-STOCK BACKTEST
# ──────────────────────────────────────────────

def backtest_stock(df, regimes):
    if len(df) < MIN_DATA: return None

    res = dict(s_hit=0, s_miss=0, r_hit=0, r_miss=0,
               skipped_filter=0, tested=0)

    test_dates = pd.date_range(end=df.index[-1], periods=TEST_MONTHS+1, freq="ME")[:-1]

    for td in test_dates:
        past   = df[df.index <= td]
        future = df[df.index >  td].head(FORWARD_DAYS)
        if len(past) < MIN_DATA // 2 or len(future) < 5:
            continue

        # Apply filters
        if not passes_filters(past, regimes, td):
            res["skipped_filter"] += 1
            continue

        res["tested"] += 1

        try:
            # reachable_only=False: measurement path — see sr_backtest.py.
            sup, res_lvl, _, _ = get_levels(past, fast=True,
                                            reachable_only=False)
        except Exception:
            continue

        s = test_support(future, sup)
        if s is True:  res["s_hit"]  += 1
        elif s is False: res["s_miss"] += 1

        r = test_resistance(future, res_lvl)
        if r is True:  res["r_hit"]  += 1
        elif r is False: res["r_miss"] += 1

    return res


# ──────────────────────────────────────────────
# REPORT
# ──────────────────────────────────────────────

def run(symbols):
    print(f"\n{'='*65}")
    print(f"  S/R ACCURACY — FILTERED (trend + momentum + regime)")
    print(f"  Only tests where stock passes your actual entry conditions")
    print(f"{'='*65}")

    regimes = load_nifty_regimes()

    ts_h=ts_m=tr_h=tr_m=0
    total_filtered=0
    rows=[]; skipped=0
    total = len(symbols)

    for idx, sym in enumerate(symbols, 1):
        sym = sym.strip().upper()
        if not sym.endswith(".NS"): sym += ".NS"

        if idx % 10 == 0 or idx == total:
            print(f"  [{idx}/{total}] processed...", flush=True)

        df = load_stock(sym)
        if df is None: skipped+=1; continue
        r = backtest_stock(df, regimes)
        if r is None: skipped+=1; continue

        st = r["s_hit"]+r["s_miss"]
        rt = r["r_hit"]+r["r_miss"]
        sr = round(r["s_hit"]/st*100,1) if st else None
        rr = round(r["r_hit"]/rt*100,1) if rt else None

        ts_h+=r["s_hit"]; ts_m+=r["s_miss"]
        tr_h+=r["r_hit"]; tr_m+=r["r_miss"]
        total_filtered += r["skipped_filter"]
        rows.append((sym, sr, rr, st, rt, r["tested"], r["skipped_filter"]))

    if not rows: print("No results."); return

    # Only show stocks that had at least 1 filtered test
    active = [(sym,sr,rr,st,rt,t,f) for sym,sr,rr,st,rt,t,f in rows if t > 0]
    inactive = len(rows) - len(active)

    print(f"\n{'Symbol':<18} {'Supp%':>7} {'Res%':>7} {'S n':>5} {'R n':>5} {'Tests':>6} {'Filtered':>9}")
    print("─"*60)
    for sym,sr,rr,st,rt,t,f in sorted(active, key=lambda x: x[1] or 0, reverse=True):
        print(f"{sym:<18} {(str(sr)+'%') if sr is not None else 'n/a':>7} "
              f"{(str(rr)+'%') if rr is not None else 'n/a':>7} "
              f"{st:>5} {rt:>5} {t:>6} {f:>9}")

    ts=ts_h+ts_m; tr=tr_h+tr_m
    ov_s = round(ts_h/ts*100,1) if ts else 0
    ov_r = round(tr_h/tr*100,1) if tr else 0
    ov   = round((ts_h+tr_h)/(ts+tr)*100,1) if (ts+tr) else 0

    print(f"\n{'='*65}")
    print(f"  OVERALL  (filtered — only tradeable conditions)")
    print(f"{'='*65}")
    print(f"  Support   held : {ov_s}%  ({ts_h}/{ts})")
    print(f"  Resistance held: {ov_r}%  ({tr_h}/{tr})")
    print(f"  Combined       : {ov}%")
    print(f"  Active stocks  : {len(active)}  |  No-filter hits: {inactive}  |  Skipped: {skipped}")
    print(f"  Months filtered out (failed entry conditions): {total_filtered}")
    print()
    if ov >= 70:   print("  ✅ GOOD — reliable on your actual trades")
    elif ov >= 60: print("  ⚠️  MODERATE — usable, confirm with price action")
    else:          print("  ❌ WEAK — levels don't hold even in good conditions")
    print(f"{'='*65}\n")

    # Bonus: compare filtered vs unfiltered
    print(f"  Interpretation:")
    print(f"  If filtered % > unfiltered (65.7%) → filters improve S/R quality ✅")
    print(f"  If similar → S/R accuracy is independent of trend conditions")
    print(f"  If lower   → trending stocks break levels more often (momentum effect)")


def main():
    if len(sys.argv) > 1:
        symbols = sys.argv[1:]
    else:
        symbols = [f.replace(".csv","") for f in os.listdir(PRICE_DIR) if f.endswith(".csv")]
        print(f"Testing full universe ({len(symbols)} stocks)...")
    run(symbols)


if __name__ == "__main__":
    main()