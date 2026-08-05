"""
sr_build_reachtable.py
----------------------
Builds an empirical reach-probability lookup table keyed on
(distance-to-level bucket  x  realized-volatility bucket), replacing the
historical-analog reach_probability() that had zero discriminative power
(corr(prob,hit) = -0.06 in calibration).

Method — walk-forward, no lookahead:
  For every stock and every month-end test date (same grid as sr_backtest.py):
    - compute S1 / R1 from PAST data only  (get_levels fast=True)
    - features from PAST data only:
        distance% = |level - cur| / cur
        annualized realized vol% over the past 252 trading days
    - outcome from the FORWARD 21-day window (same touch/bounce test as
      sr_backtest.py) -> hit True/False
  Then tabulate hit-rate per (dist_bucket, vol_bucket) cell.

Overfitting guards:
  - Time split: the LAST `HOLDOUT_MONTHS` test dates are held out. The table
    is built ONLY on the earlier dates; calibration is reported on the holdout.
  - Cells with < MIN_CELL samples fall back to the global base rate, so we
    never emit a confident number from a thin cell.

Outputs:
  ../data/sr_reach_table.json   (the table + metadata, loaded by
                                 support_resistance.reach_probability_v2)

Usage:
  python sr_build_reachtable.py            # full universe
  python sr_build_reachtable.py TCS.NS ... # subset (for quick checks)
"""
import os, sys, json
import numpy as np
import pandas as pd
from support_resistance import get_levels, load_stock
from sr_backtest import test_support, test_resistance, FORWARD_DAYS, MIN_DATA, TEST_MONTHS, PRICE_DIR

# ── bucket edges ──────────────────────────────────────────────────────
# distance% from current price to the level (absolute)
DIST_EDGES = [0.0, 0.02, 0.04, 0.06, 0.08, 0.12, 1.0]
DIST_LABELS = ["0-2%", "2-4%", "4-6%", "6-8%", "8-12%", "12%+"]
# annualized realized vol% (validated lever: monotonic with accuracy)
VOL_EDGES = [0.0, 25.0, 35.0, 45.0, 1000.0]
VOL_LABELS = ["<25%", "25-35%", "35-45%", "45%+"]

HOLDOUT_MONTHS = 4     # last N month-end test dates held out for OOS calibration
MIN_CELL       = 30    # below this, a cell falls back to global base rate
OUT_PATH       = "../data/sr_reach_table.json"


def realized_vol(past_close):
    ret = past_close.pct_change().dropna().tail(252)
    if len(ret) < 30:
        return None
    return float(ret.std() * np.sqrt(252) * 100)


def bucket(value, edges, labels):
    for i in range(len(labels)):
        if edges[i] <= value < edges[i + 1]:
            return labels[i]
    return labels[-1]


def collect(symbols):
    """Return DataFrame: one row per (stock,date,side) with dist,vol,hit,td."""
    rows = []
    total = len(symbols)
    for idx, sym in enumerate(symbols, 1):
        sym = sym.strip().upper()
        if not sym.endswith(".NS"): sym += ".NS"
        if idx % 50 == 0: print(f"  [{idx}/{total}]", flush=True)
        df = load_stock(sym)
        if df is None or len(df) < MIN_DATA:
            continue
        test_dates = pd.date_range(end=df.index[-1], periods=TEST_MONTHS + 1, freq="ME")[:-1]
        for td in test_dates:
            past   = df[df.index <= td]
            future = df[df.index >  td].head(FORWARD_DAYS)
            if len(past) < MIN_DATA // 2 or len(future) < 5:
                continue
            vol = realized_vol(past["Close"])
            if vol is None:
                continue
            try:
                # reachable_only=False: measurement path. This legacy
                # builder is superseded by sr_build_touchtable but is kept
                # as a safety net, so it must stay on the same basis.
                sup, res_lvl, _, _ = get_levels(past, fast=True,
                                                reachable_only=False)
            except Exception:
                continue
            cur = float(past["Close"].iloc[-1])

            for level, direction in [(sup, "down"), (res_lvl, "up")]:
                dist = abs(level - cur) / cur
                if direction == "down":
                    hit = test_support(future, level)
                else:
                    hit = test_resistance(future, level)
                if hit is None:
                    continue
                rows.append({
                    "td": td, "sym": sym, "side": direction,
                    "dist": dist, "vol": vol, "hit": bool(hit),
                })
    return pd.DataFrame(rows)


def build_table(train_df):
    train_df = train_df.copy()
    train_df["db"] = train_df["dist"].apply(lambda x: bucket(x, DIST_EDGES, DIST_LABELS))
    train_df["vb"] = train_df["vol"].apply(lambda x: bucket(x, VOL_EDGES, VOL_LABELS))
    base_rate = round(train_df["hit"].mean() * 100, 1)

    table = {}
    for db in DIST_LABELS:
        for vb in VOL_LABELS:
            cell = train_df[(train_df["db"] == db) & (train_df["vb"] == vb)]
            n = len(cell)
            if n >= MIN_CELL:
                table[f"{db}|{vb}"] = {"prob": round(cell["hit"].mean() * 100, 1), "n": n}
            else:
                table[f"{db}|{vb}"] = {"prob": base_rate, "n": n, "fallback": True}
    return table, base_rate


def calibrate(df, table, base_rate, label):
    """Apply the table to df and report predicted-vs-actual by bucket."""
    df = df.copy()
    df["db"] = df["dist"].apply(lambda x: bucket(x, DIST_EDGES, DIST_LABELS))
    df["vb"] = df["vol"].apply(lambda x: bucket(x, VOL_EDGES, VOL_LABELS))
    df["pred"] = df.apply(lambda r: table.get(f"{r['db']}|{r['vb']}", {}).get("prob", base_rate), axis=1)

    print(f"\n{'='*54}\n  {label}   (n={len(df)})\n{'='*54}")
    buckets = [(0, 55), (55, 60), (60, 65), (65, 70), (70, 75), (75, 101)]
    print(f"  {'Predicted':<12} {'Actual':>8} {'n':>7}")
    print("  " + "-" * 30)
    for lo, hi in buckets:
        sub = df[(df["pred"] >= lo) & (df["pred"] < hi)]
        if len(sub) == 0: continue
        print(f"  {lo}-{hi-1}%{'':<6} {sub['hit'].mean()*100:>7.1f}% {len(sub):>7}")
    print(f"\n  Correlation(pred, hit) = {df['pred'].corr(df['hit'].astype(float)):.3f}")
    return df["pred"].corr(df["hit"].astype(float))


def main():
    if len(sys.argv) > 1:
        symbols = sys.argv[1:]
    else:
        symbols = [f.replace(".csv", "") for f in os.listdir(PRICE_DIR) if f.endswith(".csv")]
        print(f"Building reach table on full universe ({len(symbols)} stocks)...")

    df = collect(symbols)
    print(f"\nCollected {len(df)} (stock,date,side) rows")

    # ── time split: hold out the last HOLDOUT_MONTHS distinct test dates ──
    all_tds = sorted(df["td"].unique())
    holdout_tds = set(all_tds[-HOLDOUT_MONTHS:])
    train = df[~df["td"].isin(holdout_tds)]
    test  = df[df["td"].isin(holdout_tds)]
    print(f"Train rows: {len(train)}  ({len(all_tds)-HOLDOUT_MONTHS} months)")
    print(f"Holdout rows: {len(test)}  ({HOLDOUT_MONTHS} months, out-of-sample)")

    table, base_rate = build_table(train)
    print(f"\nGlobal base rate (train): {base_rate}%")

    # show the table
    print(f"\n  {'dist \\ vol':<10} " + " ".join(f"{v:>10}" for v in VOL_LABELS))
    for db in DIST_LABELS:
        cells = []
        for vb in VOL_LABELS:
            c = table[f"{db}|{vb}"]
            tag = "*" if c.get("fallback") else " "
            cells.append(f"{c['prob']:>5}%({c['n']:>3}){tag}")
        print(f"  {db:<10} " + " ".join(f"{c:>10}" for c in cells))
    print("  * = thin cell, fell back to base rate")

    # ── calibration: in-sample (train) vs out-of-sample (holdout) ──
    calibrate(train, table, base_rate, "IN-SAMPLE (train)")
    oos_corr = calibrate(test, table, base_rate, "OUT-OF-SAMPLE (holdout)")

    # persist
    meta = {
        "table": table, "base_rate": base_rate,
        "dist_edges": DIST_EDGES, "dist_labels": DIST_LABELS,
        "vol_edges": VOL_EDGES, "vol_labels": VOL_LABELS,
        "min_cell": MIN_CELL, "forward_days": FORWARD_DAYS,
        "n_train": len(train), "oos_corr": round(float(oos_corr), 3),
    }
    with open(OUT_PATH, "w") as f:
        json.dump(meta, f, indent=2)
    print(f"\nSaved table -> {OUT_PATH}")
    print(f"OOS discrimination: corr(pred,hit) = {oos_corr:.3f} "
          f"(old analog method was ~ -0.06)")


if __name__ == "__main__":
    main()
