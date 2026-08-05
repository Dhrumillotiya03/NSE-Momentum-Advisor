"""
sr_backtest.py — walk-forward S/R accuracy test
Usage:
    python sr_backtest.py                    ← full universe
    python sr_backtest.py HDFCBANK.NS TCS    ← specific
"""
import os, sys
import numpy as np
import pandas as pd
from support_resistance import get_levels

PRICE_DIR    = "../data/price_data/"
FORWARD_DAYS = 21
TOUCH_ZONE   = 0.01    # 1% — Low must reach within 1% of support
BOUNCE_PCT   = 0.015   # 1.5% recovery after touch = held
TEST_MONTHS  = 12
MIN_DATA     = 300


def load_stock(symbol):
    path = os.path.join(PRICE_DIR, f"{symbol}.csv")
    if not os.path.exists(path): return None
    df = pd.read_csv(path, parse_dates=["Date"])
    df = df.sort_values("Date").set_index("Date")
    for col in ["Close", "High", "Low"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "Volume" in df.columns:
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce").fillna(0)
    return df.dropna(subset=["Close", "High", "Low"])


def test_support(future_df, support):
    touch   = support * (1 + TOUCH_ZONE)
    broke   = support * (1 - TOUCH_ZONE)
    bounce  = support * (1 + BOUNCE_PCT)
    lows    = future_df["Low"].values
    closes  = future_df["Close"].values
    touched = False
    for i in range(len(lows)):
        if lows[i] <= touch:
            touched = True
        if touched:
            if closes[i] >= bounce: return True
            if closes[i] <  broke:  return False
    return None


def test_resistance(future_df, resistance):
    touch   = resistance * (1 - TOUCH_ZONE)
    broke   = resistance * (1 + TOUCH_ZONE)
    reject  = resistance * (1 - BOUNCE_PCT)
    highs   = future_df["High"].values
    closes  = future_df["Close"].values
    touched = False
    for i in range(len(highs)):
        if highs[i] >= touch:
            touched = True
        if touched:
            if closes[i] <= reject: return True
            if closes[i] >  broke:  return False
    return None


def backtest_stock(df):
    if len(df) < MIN_DATA:
        return None
    res = dict(s_hit=0, s_miss=0, r_hit=0, r_miss=0)
    test_dates = pd.date_range(end=df.index[-1], periods=TEST_MONTHS + 1, freq="ME")[:-1]

    for td in test_dates:
        past   = df[df.index <= td]
        future = df[df.index >  td].head(FORWARD_DAYS)
        if len(past) < MIN_DATA // 2 or len(future) < 5:
            continue
        try:
            # fast=True: skips volume profile & delivery IO — ~20x faster
            # accuracy impact is minimal because backtest uses touch/bounce
            # not composite scores
            # reachable_only=False: measurement path — raw pivots, not the
            # display band. See get_all_levels docstring (2026-08-05).
            sup, res_lvl, _, _ = get_levels(past, fast=True,
                                            reachable_only=False)
        except Exception:
            continue

        s = test_support(future, sup)
        if s is True:    res["s_hit"]  += 1
        elif s is False: res["s_miss"] += 1

        r = test_resistance(future, res_lvl)
        if r is True:    res["r_hit"]  += 1
        elif r is False: res["r_miss"] += 1

    return res


def run(symbols):
    print(f"\n{'='*65}")
    print(f"  S/R ACCURACY BACKTEST  (swing + MTF + centroid clustering)")
    print(f"  {TEST_MONTHS}mo walk-forward | {FORWARD_DAYS}d window | touch={TOUCH_ZONE*100:.0f}% bounce={BOUNCE_PCT*100:.1f}%")
    print(f"{'='*65}")

    ts_h = ts_m = tr_h = tr_m = 0
    rows = []; skipped = 0
    total = len(symbols)

    for idx, sym in enumerate(symbols, 1):
        sym = sym.strip().upper()
        if not sym.endswith(".NS"): sym += ".NS"

        # progress every 10 stocks
        if idx % 10 == 0 or idx == total:
            done = len(rows)
            ov_s = round(ts_h / (ts_h + ts_m) * 100, 1) if (ts_h + ts_m) else 0
            ov_r = round(tr_h / (tr_h + tr_m) * 100, 1) if (tr_h + tr_m) else 0
            ov   = round((ts_h + tr_h) / (ts_h + ts_m + tr_h + tr_m) * 100, 1) if (ts_h + ts_m + tr_h + tr_m) else 0
            print(f"  [{idx}/{total}] done={done} skipped={skipped}  "
                  f"S={ov_s}%  R={ov_r}%  Combined={ov}%", flush=True)

        df = load_stock(sym)
        if df is None: skipped += 1; continue

        r = backtest_stock(df)
        if r is None: skipped += 1; continue

        st = r["s_hit"] + r["s_miss"]
        rt = r["r_hit"] + r["r_miss"]
        sr = round(r["s_hit"] / st * 100, 1) if st else None
        rr = round(r["r_hit"] / rt * 100, 1) if rt else None
        ts_h += r["s_hit"];  ts_m += r["s_miss"]
        tr_h += r["r_hit"];  tr_m += r["r_miss"]
        rows.append((sym, sr, rr, st, rt))

    if not rows:
        print("No results."); return

    print(f"\n{'Symbol':<18} {'Supp%':>7} {'Res%':>7} {'S n':>6} {'R n':>6}")
    print("─" * 48)
    for sym, sr, rr, st, rt in sorted(rows, key=lambda x: x[1] or 0, reverse=True):
        print(f"{sym:<18} {(str(sr)+'%') if sr is not None else 'n/a':>7} "
              f"{(str(rr)+'%') if rr is not None else 'n/a':>7} {st:>6} {rt:>6}")

    ts  = ts_h + ts_m;  tr  = tr_h + tr_m
    ov_s = round(ts_h / ts * 100, 1) if ts else 0
    ov_r = round(tr_h / tr * 100, 1) if tr else 0
    ov   = round((ts_h + tr_h) / (ts + tr) * 100, 1) if (ts + tr) else 0

    print(f"\n{'='*65}")
    print(f"  OVERALL")
    print(f"{'='*65}")
    print(f"  Support   held : {ov_s}%  ({ts_h}/{ts})")
    print(f"  Resistance held: {ov_r}%  ({tr_h}/{tr})")
    print(f"  Combined       : {ov}%")
    print(f"  Stocks tested  : {len(rows)}  |  Skipped: {skipped}")
    print()
    if ov >= 68:   print("  ✅ GOOD — reliable")
    elif ov >= 58: print("  ⚠️  MODERATE")
    else:          print("  ❌ WEAK")
    print(f"{'='*65}\n")


def main():
    if len(sys.argv) > 1:
        symbols = sys.argv[1:]
    else:
        symbols = [f.replace(".csv", "") for f in os.listdir(PRICE_DIR) if f.endswith(".csv")]
        print(f"Testing full universe ({len(symbols)} stocks)...")
    run(symbols)


if __name__ == "__main__":
    main()
