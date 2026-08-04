"""
build_containment_table_daily.py
--------------------------------
Fits ../data/containment_table.json from the DAILY archive (2015-2026) instead
of Kite intraday (2023-2026).

WHY A SECOND BUILDER
--------------------
build_containment_table.py fits on Kite 15-minute data, which starts 2023-08.
That leaves only ~17 months before the 2025-01-01 split — 3,784 train rows vs
13,988 holdout, an inverted and thin split. The consequence was measurable:

    15th-pct floor width, by period
    vol        train(pre-2025)   holdout(2025+)
    <25%           10.1%             8.2%
    25-35%         12.6%             9.4%
    35-45%         14.8%            11.5%
    45%+           18.8%            14.2%

and per-quarter it swings 6.0% (2025Q3) to 14.8% (2025Q1) — a 2.5x range. The
bands were fitted on a more volatile stretch than they were scored on, which is
why holdout floor-hold came in at 86-93% against a claimed 85%: too wide, in the
safe direction, but not calibrated.

CONTAINMENT NEEDS ONLY CLOSES. Unlike the fill/persistence work, this quantity
is a quantile of forward closing excursion — no intraday information enters it.
So the daily archive's 11 years is strictly better here, covering 2018-20's
grind, the COVID crash, and 2022's drawdown. Intraday data earns its keep in
research_tradeable_levels.py (fill realism), not in this table.

ADJUSTMENT NOTE: this reads price_data/ (yfinance-ADJUSTED) end-to-end. That is
safe BECAUSE it never mixes sources — every price in a given observation comes
from the same series, so the ratio is scale-free. Do NOT "improve" this by
splicing Kite prices in; see update_prices_kite.py's header for why.

Usage:
    python build_containment_table_daily.py
    python build_containment_table_daily.py --alpha 0.15 --split 2022-01-01
"""
import os
import sys
import json

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support_resistance import load_stock

PRICE_DIR = "../data/price_data/"
OUT_PATH = "../data/containment_table.json"
HORIZONS = [5, 10, 15, 21]
MIN_CELL = 100

VOL_EDGES = [0.0, 25.0, 35.0, 45.0, 1e9]
VOL_LABELS = ["<25%", "25-35%", "35-45%", "45%+"]


def vol_bucket(v):
    for i in range(len(VOL_LABELS)):
        if VOL_EDGES[i] <= v < VOL_EDGES[i + 1]:
            return VOL_LABELS[i]
    return VOL_LABELS[-1]


def realized_vol(close):
    r = close.pct_change().dropna().tail(252)
    if len(r) < 30:
        return None
    return float(r.std() * np.sqrt(252) * 100)


def collect(symbols, horizons):
    rows = []
    for n, s in enumerate(symbols, 1):
        if n % 50 == 0:
            print(f"    [{n}/{len(symbols)}]", flush=True)
        df = load_stock(s if s.endswith(".NS") else s + ".NS")
        if df is None or len(df) < 400:
            continue
        # monthly decision grid over the full history
        for td in pd.date_range(end=df.index[-1], periods=140, freq="ME"):
            past = df[df.index <= td]
            if len(past) < 300:
                continue
            v = realized_vol(past["Close"])
            if v is None:
                continue
            cur = float(past["Close"].iloc[-1])
            if not cur or not np.isfinite(cur):
                continue
            vb = vol_bucket(v)
            for H in horizons:
                fwd = df[df.index > td].head(H)
                if len(fwd) < H:
                    continue
                rows.append({
                    "sym": s, "td": td, "H": H, "vb": vb,
                    "min_ret": float(fwd["Close"].min() / cur - 1.0),
                    "max_ret": float(fwd["Close"].max() / cur - 1.0),
                })
    return pd.DataFrame(rows)


def main():
    argv = sys.argv[1:]
    # Fit SEVERAL confidence levels in one pass. The data collection dominates
    # runtime, and quantiles are cheap once collected, so there is no reason to
    # force a single alpha and re-run for another. 0.25/0.15/0.10/0.05 spans
    # "tradeable but breaks often" to "rarely breaks but far away".
    alphas = [0.25, 0.15, 0.10, 0.05]
    if "--alpha" in argv:
        alphas = [float(x) for x in argv[argv.index("--alpha") + 1].split(",")]
    alpha = alphas[0]
    split = pd.Timestamp("2022-01-01")
    if "--split" in argv:
        split = pd.Timestamp(argv[argv.index("--split") + 1])

    symbols = sorted(f[:-4] for f in os.listdir(PRICE_DIR) if f.endswith(".csv"))
    print(f"Containment table from DAILY archive — {len(symbols)} symbols, "
          f"alpha={alpha}, split={split.date()}")

    df = collect(symbols, HORIZONS)
    if df.empty:
        print("No observations."); return

    train = df[df["td"] < split]
    hold = df[df["td"] >= split]
    print(f"\nn={len(df)}  train={len(train)} (<{split.date()})  "
          f"holdout={len(hold)}")
    print(f"train spans {train['td'].min().date()} -> {train['td'].max().date()}")

    table, report = {}, []
    for a in alphas:
        for H in HORIZONS:
            for vb in VOL_LABELS:
                c = train[(train["H"] == H) & (train["vb"] == vb)]
                if len(c) < MIN_CELL:
                    continue
                fw = float(-c["min_ret"].quantile(a))
                cw = float(c["max_ret"].quantile(1 - a))
                table[f"{vb}|{H}|{int(round(a*100))}"] = {
                    "floor_width": round(max(fw, 0.0), 4),
                    "ceiling_width": round(max(cw, 0.0), 4),
                    "n": len(c),
                }
                h = hold[(hold["H"] == H) & (hold["vb"] == vb)]
                if len(h) >= MIN_CELL and a == alpha:
                    report.append((H, vb, fw, cw, len(c), len(h),
                                   float((h["min_ret"] > -fw).mean()),
                                   float(((h["min_ret"] > -fw) &
                                          (h["max_ret"] < cw)).mean())))

    print("\n" + "=" * 78)
    print(f"HOLDOUT CALIBRATION — claimed {(1-alpha)*100:.0f}% floor hold rate")
    print("=" * 78)
    print(f"  {'H':>3} {'vol':<9} {'floor':>8} {'ceil':>8} {'n_tr':>7} "
          f"{'n_ho':>7} {'floor held':>11} {'both held':>10}")
    off = 0
    for H, vb, fw, cw, ntr, nho, fh, bh in report:
        bad = abs(fh - (1 - alpha)) > 0.10
        off += bad
        print(f"  {H:>3} {vb:<9} {-fw*100:>7.1f}% {cw*100:>7.1f}% {ntr:>7} "
              f"{nho:>7} {fh*100:>10.1f}% {bh*100:>9.1f}%"
              + ("   <-- off" if bad else ""))
    print(f"\n  cells outside +-10pp of claim: {off}/{len(report)}")

    payload = {
        "metric": "containment",
        "source": "daily archive (yfinance-adjusted), 2015-2026",
        "alpha": alpha,
        "alphas": alphas,
        "vol_edges": VOL_EDGES, "vol_labels": VOL_LABELS,
        "horizons": HORIZONS,
        "split_date": str(split.date()),
        "n_train": len(train), "n_holdout": len(hold),
        "basis": "closing prices; floor and ceiling fitted separately "
                 "(forward excursion is asymmetric, downside > upside)",
        "caveat": "band width is REGIME-DEPENDENT: quarterly 15th-pct floor "
                  "ranged 6.0%-14.8% on 2024-26 data. Treat as a typical-month "
                  "expectation, not a guarantee.",
        "table": table,
    }
    with open(OUT_PATH, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"\nwrote {OUT_PATH}  ({len(table)} cells)")


if __name__ == "__main__":
    main()
