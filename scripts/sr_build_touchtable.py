"""
sr_build_touchtable.py
----------------------
Builds an empirical **P(TOUCH)** lookup table keyed on
(distance-to-level bucket x realized-volatility bucket), at a configurable
forward horizon.

WHY THIS EXISTS (the bug it fixes)
----------------------------------
sr_build_reachtable.py scores outcomes with sr_backtest.test_support /
test_resistance, which implement a touch-AND-BOUNCE test:

    never touched        -> None   -> row DROPPED from training data
    touched then bounced -> True
    touched then broke   -> False

So the resulting table is P(bounce | touched) — untouched levels are invisible
to it. But every consumer (sr_daily_logger, analyse_table, sr_monthend_analysis)
labels and uses that number as a *reach*/touch probability, and scores itself by
asking "did price touch the level?", where an untouched level is a MISS, not a
dropped row.

The damage is concentrated in far cells: distant levels are rarely touched, so
they are mostly dropped instead of counted as misses, and the few survivors are
conditioned on having been touched. That makes the old table nearly FLAT in
distance (12%+ reads ~66%, higher than 0-2%'s ~57%) while real forward data
decays steeply (76% at 0-5% -> 3% at 10-15% -> 0% beyond 15%). It also explains
the weak OOS corr (~0.17).

This script keeps the same walk-forward construction, holdout discipline,
bucket edges and min-cell fallback as sr_build_reachtable.py, changing exactly
one thing: the outcome is a pure TOUCH test, and an untouched level is a
recorded miss rather than a dropped row.

HORIZON
-------
--forward N builds the table at an N-trading-day horizon. Because the S/R
subsystem's real question is "will this be touched before the month's last
Tuesday?", horizons shorter than 21 are genuinely useful; building a table per
horizon is the empirically honest alternative to rescaling one 21d table by a
sqrt-of-time approximation (sr_horizon.scale_probability_to_horizon).

Outputs:
  ../data/sr_touch_table.json                  (default, 21d)
  ../data/sr_touch_table_<N>d.json             (--forward N, N != 21)

Usage:
  python sr_build_touchtable.py                    # full universe, 21d
  python sr_build_touchtable.py --forward 10       # 10-day horizon
  python sr_build_touchtable.py RELIANCE.NS TCS.NS # subset (quick check)
"""
import os, sys, json
import numpy as np
import pandas as pd

from support_resistance import get_levels, load_stock
from sr_backtest import MIN_DATA, TEST_MONTHS, PRICE_DIR

# Same bucket geometry as the reach table so the two are directly comparable.
DIST_EDGES  = [0.0, 0.02, 0.04, 0.06, 0.08, 0.12, 1.0]
DIST_LABELS = ["0-2%", "2-4%", "4-6%", "6-8%", "8-12%", "12%+"]
VOL_EDGES   = [0.0, 25.0, 35.0, 45.0, 1000.0]
VOL_LABELS  = ["<25%", "25-35%", "35-45%", "45%+"]

TOUCH_ZONE     = 0.01   # within 1% counts as a touch (matches sr_monthend_analysis)
HOLDOUT_MONTHS = 4
MIN_CELL       = 30


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


def touched(future_df, level, direction):
    """Pure touch test — the question consumers actually ask.

    Unlike sr_backtest.test_support/test_resistance this never returns None:
    an untouched level is False (a real miss), not a dropped observation.
    """
    if direction == "down":
        return bool((future_df["Low"] <= level * (1 + TOUCH_ZONE)).any())
    return bool((future_df["High"] >= level * (1 - TOUCH_ZONE)).any())


def collect(symbols, forward_days):
    """One row per (stock, test-date, side) with dist, vol, hit."""
    rows = []
    total = len(symbols)
    for idx, sym in enumerate(symbols, 1):
        sym = sym.strip().upper()
        if not sym.endswith(".NS"):
            sym += ".NS"
        if idx % 50 == 0:
            print(f"  [{idx}/{total}]", flush=True)
        df = load_stock(sym)
        if df is None or len(df) < MIN_DATA:
            continue

        test_dates = pd.date_range(end=df.index[-1],
                                   periods=TEST_MONTHS + 1, freq="ME")[:-1]
        for td in test_dates:
            past   = df[df.index <= td]
            future = df[df.index >  td].head(forward_days)
            # REQUIRE a complete forward window. The reach-table builder
            # accepted len(future) >= 5 and scored those as full-horizon
            # outcomes, so a no-touch in a 6-bar window was recorded as a
            # 21-day miss — the same truncation bias that produced the fake
            # 100% in sr_monthend_analysis. A partial window cannot resolve a
            # miss, so it must not enter training at all.
            if len(past) < MIN_DATA // 2 or len(future) < forward_days:
                continue

            vol = realized_vol(past["Close"])
            if vol is None:
                continue
            try:
                # reachable_only=False: build from RAW pivots, not the
                # display band. Measured 2026-08-05 — capping the training
                # data hollows out the far cells (12%+|<25% went to ZERO
                # observations, thin cells 2/24 -> 6/24) and costs ~0.14 OOS
                # correlation. Worse, the table then never sees an unreachable
                # level, so it loses the ability to say a level is
                # unreachable: the 12%+ bucket read 19.5% vs 0.0% uncapped.
                sup, res_lvl, s_str, r_str = get_levels(past, fast=True,
                                                        reachable_only=False)
            except Exception:
                continue
            cur = float(past["Close"].iloc[-1])
            if not cur:
                continue

            # Belt-and-braces: with reachable_only=False no projected level can
            # reach here (projection only happens inside the band path), but
            # strength 0 is checked anyway so this loop stays correct if the
            # call above is ever changed back. A projected level must never
            # enter the table — it sits at the band edge by construction, so it
            # is touched far more often than a real pivot at the same distance.
            for level, direction, strength in [(sup, "down", s_str),
                                               (res_lvl, "up", r_str)]:
                if level is None or not np.isfinite(level) or level <= 0:
                    continue
                if not strength:
                    continue
                # Skip levels already on the wrong side of price: a "support"
                # above spot is not a support, and its touch is trivially true.
                if direction == "down" and level >= cur:
                    continue
                if direction == "up" and level <= cur:
                    continue
                rows.append({
                    "td": td, "sym": sym, "side": direction,
                    "dist": abs(level - cur) / cur,
                    "vol": vol,
                    "hit": touched(future, level, direction),
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
                table[f"{db}|{vb}"] = {"prob": round(cell["hit"].mean() * 100, 1),
                                       "n": n}
            else:
                # Thin cell: fall back to the DISTANCE-marginal rate rather than
                # the global base rate. P(touch) varies enormously across
                # distance, so a global fallback hands a far cell a near-base
                # number — exactly the distortion this table exists to remove.
                marg = train_df[train_df["db"] == db]
                prob = round(marg["hit"].mean() * 100, 1) if len(marg) >= MIN_CELL \
                    else base_rate
                table[f"{db}|{vb}"] = {"prob": prob, "n": n, "fallback": True}
    return table, base_rate


def calibrate(df, table, base_rate, label):
    df = df.copy()
    df["db"] = df["dist"].apply(lambda x: bucket(x, DIST_EDGES, DIST_LABELS))
    df["vb"] = df["vol"].apply(lambda x: bucket(x, VOL_EDGES, VOL_LABELS))
    df["pred"] = df.apply(
        lambda r: table.get(f"{r['db']}|{r['vb']}", {}).get("prob", base_rate), axis=1)

    print(f"\n{'='*54}\n  {label}   (n={len(df)})\n{'='*54}")
    print(f"  {'Predicted':<12} {'Actual':>8} {'n':>7}")
    print("  " + "-" * 30)
    for lo in range(0, 100, 10):
        sub = df[(df["pred"] >= lo) & (df["pred"] < lo + 10)]
        if len(sub) == 0:
            continue
        print(f"  {lo}-{lo+9}%{'':<6} {sub['hit'].mean()*100:>7.1f}% {len(sub):>7}")
    corr = df["pred"].corr(df["hit"].astype(float))
    print(f"\n  Correlation(pred, hit) = {corr:.3f}")
    return float(corr) if pd.notna(corr) else None


def main():
    argv = sys.argv[1:]
    forward_days = 21
    if "--forward" in argv:
        i = argv.index("--forward")
        forward_days = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]

    symbols = [a for a in argv if not a.startswith("--")]
    if not symbols:
        symbols = sorted(f[:-4] for f in os.listdir(PRICE_DIR) if f.endswith(".csv"))

    out_path = "../data/sr_touch_table.json" if forward_days == 21 \
        else f"../data/sr_touch_table_{forward_days}d.json"

    print(f"\nBuilding P(touch) table @ {forward_days}d over {len(symbols)} symbols")
    df = collect(symbols, forward_days)
    if df.empty:
        print("No observations collected."); return
    print(f"\nCollected {len(df)} observations  "
          f"(overall touch rate {df['hit'].mean()*100:.1f}%)")

    # Time-based holdout: last N test dates are OOS.
    dates = sorted(df["td"].unique())
    if len(dates) > HOLDOUT_MONTHS:
        cut = dates[-HOLDOUT_MONTHS]
        train, hold = df[df["td"] < cut], df[df["td"] >= cut]
    else:
        train, hold = df, df.iloc[0:0]

    table, base_rate = build_table(train)
    print(f"\nTrain n={len(train)}  base_rate={base_rate}%  holdout n={len(hold)}")

    calibrate(train, table, base_rate, "IN-SAMPLE (train)")
    oos_corr = calibrate(hold, table, base_rate, "OUT-OF-SAMPLE (holdout)") \
        if len(hold) else None

    print(f"\n{'='*54}\n  TABLE — P(touch) by distance x vol\n{'='*54}")
    print(f"  {'cell':<16} {'prob':>7} {'n':>7}")
    for db in DIST_LABELS:
        for vb in VOL_LABELS:
            c = table[f"{db}|{vb}"]
            tag = " (fallback)" if c.get("fallback") else ""
            print(f"  {db+'|'+vb:<16} {c['prob']:>6.1f}% {c['n']:>7}{tag}")

    payload = {
        "metric": "touch",          # distinguishes this from the bounce table
        "base_rate": base_rate,
        "dist_edges": DIST_EDGES, "dist_labels": DIST_LABELS,
        "vol_edges": VOL_EDGES,   "vol_labels": VOL_LABELS,
        "min_cell": MIN_CELL, "forward_days": forward_days,
        "touch_zone": TOUCH_ZONE,
        "n_train": len(train), "n_holdout": len(hold),
        "oos_corr": round(oos_corr, 3) if oos_corr is not None else None,
        "table": table,
    }
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"\n✅ wrote {out_path}")


if __name__ == "__main__":
    main()
