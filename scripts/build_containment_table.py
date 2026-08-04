"""
build_containment_table.py
--------------------------
Fits ../data/containment_table.json — the empirical band widths used by
containment_band.py.

WHAT IT MEASURES
----------------
For each (volatility bucket, horizon), the quantiles of the forward excursion:

    floor_width   = -quantile(min(fwd close)/cur - 1, alpha)
    ceiling_width =  quantile(max(fwd close)/cur - 1, 1 - alpha)

i.e. "how far down does this kind of stock go, in the worst alpha of months".
This directly measures the target quantity — no pivot, no touch table, no model
in the path. It is the honest way to answer "a level it won't dip below".

ASYMMETRY IS REAL AND IS PRESERVED
-----------------------------------
Measured (2023-2026 train, 21d): the downside excursion is 1.07x to 1.36x the
upside, and the gap is WIDEST for low-volatility names —

    vol       15th pct min   85th pct max   ratio
    <25%         -10.6%          +7.8%      1.36
    25-35%       -11.9%          +9.8%      1.22
    35-45%       -13.9%         +12.3%      1.13
    45%+         -18.2%         +17.0%      1.07

So a SYMMETRIC band understates downside most on exactly the stable names a
holder feels safest in. Floor and ceiling are therefore fitted separately and
never mirrored.

CLOSES, NOT LOWS
----------------
Excursions use CLOSING prices. A single intraday wick through a level is not
the "the stock dipped below this for the month" event being asked about;
scoring wicks as breaches would make every band look broken. This matches
research_tradeable_levels.contained().

Time-split: train < 2025-01-01, holdout >= 2025-01-01. The holdout calibration
is reported so the claimed confidence can be checked against reality rather
than assumed.

Usage:
    python build_containment_table.py
    python build_containment_table.py --horizons 5,10,15,21 --alpha 0.15
"""
import os
import sys
import json

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_tradeable_levels as R

OUT_PATH = "../data/containment_table.json"
SPLIT_DATE = pd.Timestamp("2025-01-01")
MIN_CELL = 30


def collect(symbols, horizons):
    rows = []
    for n, s in enumerate(symbols, 1):
        if n % 25 == 0:
            print(f"    [{n}/{len(symbols)}]", flush=True)
        intra = R.load_intraday(s)
        if intra is None:
            continue
        d = R.daily_from_intraday(intra)
        if len(d) < 300:
            continue
        for td in pd.date_range(end=d.index[-1], periods=40, freq="ME"):
            past = d[d.index <= td]
            if len(past) < 260:
                continue
            v = R.realized_vol(past["Close"])
            if v is None:
                continue
            cur = float(past["Close"].iloc[-1])
            if not cur or not np.isfinite(cur):
                continue
            for H in horizons:
                fwd = d[d.index > td].head(H)
                if len(fwd) < H:
                    continue
                rows.append({
                    "sym": s, "td": td, "H": H, "vb": R.vol_bucket(v),
                    "min_ret": float(fwd["Close"].min() / cur - 1.0),
                    "max_ret": float(fwd["Close"].max() / cur - 1.0),
                })
    return pd.DataFrame(rows)


def main():
    argv = sys.argv[1:]
    horizons = [5, 10, 15, 21]
    if "--horizons" in argv:
        i = argv.index("--horizons")
        horizons = [int(x) for x in argv[i + 1].split(",")]
    alpha = 0.15
    if "--alpha" in argv:
        i = argv.index("--alpha")
        alpha = float(argv[i + 1])

    symbols = sorted(f[:-4] for f in os.listdir(R.INTRA_DIR) if f.endswith(".csv"))
    print(f"Building containment table — {len(symbols)} symbols, "
          f"horizons {horizons}, alpha={alpha}")

    df = collect(symbols, horizons)
    if df.empty:
        print("No observations."); return
    train = df[df["td"] < SPLIT_DATE]
    hold = df[df["td"] >= SPLIT_DATE]
    print(f"\nn={len(df)}  train={len(train)}  holdout={len(hold)}")

    table, report = {}, []
    for H in horizons:
        for vb in R.VOL_LABELS:
            c = train[(train["H"] == H) & (train["vb"] == vb)]
            if len(c) < MIN_CELL:
                continue
            fw = float(-c["min_ret"].quantile(alpha))
            cw = float(c["max_ret"].quantile(1 - alpha))
            table[f"{vb}|{H}|{int(round(alpha*100))}"] = {
                "floor_width": round(max(fw, 0.0), 4),
                "ceiling_width": round(max(cw, 0.0), 4),
                "n": len(c),
            }
            # holdout calibration: did the band actually hold (1-alpha) of the time?
            h = hold[(hold["H"] == H) & (hold["vb"] == vb)]
            if len(h) >= MIN_CELL:
                held = float(((h["min_ret"] > -fw) & (h["max_ret"] < cw)).mean())
                floor_held = float((h["min_ret"] > -fw).mean())
                report.append((H, vb, fw, cw, len(c), len(h), floor_held, held))

    print("\n" + "=" * 78)
    print(f"HOLDOUT CALIBRATION — claimed {(1-alpha)*100:.0f}% floor hold rate")
    print("=" * 78)
    print(f"  {'H':>3} {'vol':<9} {'floor':>8} {'ceil':>8} {'n_tr':>6} "
          f"{'n_ho':>6} {'floor held':>11} {'both held':>10}")
    for H, vb, fw, cw, ntr, nho, fh, bh in report:
        flag = "" if abs(fh - (1 - alpha)) <= 0.10 else "   <-- off"
        print(f"  {H:>3} {vb:<9} {-fw*100:>7.1f}% {cw*100:>7.1f}% {ntr:>6} "
              f"{nho:>6} {fh*100:>10.1f}% {bh*100:>9.1f}%{flag}")

    payload = {
        "metric": "containment",
        "alpha": alpha,
        "vol_edges": R.VOL_EDGES, "vol_labels": R.VOL_LABELS,
        "horizons": horizons,
        "split_date": str(SPLIT_DATE.date()),
        "n_train": len(train), "n_holdout": len(hold),
        "basis": "closing prices; floor and ceiling fitted separately "
                 "(forward excursion is asymmetric, downside 1.07-1.36x upside)",
        "table": table,
    }
    with open(OUT_PATH, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"\nwrote {OUT_PATH}  ({len(table)} cells)")


if __name__ == "__main__":
    main()
