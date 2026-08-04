"""
research_sr_exit_side.py   —  H1 of PREREG_sr_improvement_batch.md
------------------------------------------------------------------
Does SELLING INTO RESISTANCE beat holding to month-end?

WHY THIS IS THE MOST PROMISING TEST IN THE BATCH
------------------------------------------------
research_tradeable_levels.py rejected buying at support. The mechanism was
ADVERSE SELECTION: a limit order only fills when price comes to you, and price
coming to you is itself evidence the trend turned. Measured there: of levels
that got reached, 9.5% held; of levels never reached, 100% held.

**Exits do not have that problem.** You already own the stock. Nothing has to
fill for you to be in the position, so "the level was reached" carries no
adverse information about your entry — you were already there. The selection
effect that killed H0 is structurally absent.

Prior evidence, stated honestly: in the entry study, resistance exits averaged
+6.6% vs -2.1% for horizon exits. That is suggestive and is why this is H1. But
it was measured ON THE LOSING ENTRY POPULATION (positions opened by a rule that
lost money), so it is NOT evidence for the exit rule standing alone. This test
measures it properly: on positions held from month start, regardless of entry.

THE TEST
--------
For each (symbol, month):
    entry  = close on the decision date (own it from month start — no fill
             model, no adverse selection)
    (a) HOLD     : exit at month-end close                    [baseline]
    (b) RESIST   : exit at first resistance touch, else month-end
    (c) BAND     : exit at containment ceiling touch, else month-end
Paired by (symbol, date) so the comparison is like-for-like.

Fills for (b)/(c) use the 15-min persistence rule (>=2 consecutive bars), the
same realism standard as the entry study — an exit you could not have caught is
not an exit.

DECISION RULE (PREREG, not tuned after the fact): adopt only if mean return
improves >= +0.5pp AND wins a majority of the 4 horizons AND survives Holm
correction across the 6-test family AND a by-date block bootstrap.

Usage:
    python research_sr_exit_side.py [--horizons 5,10,15,21] [--limit N]
"""
import os
import sys
import json

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_tradeable_levels as R
from containment_band import containment_band

OUT_DIR = "../data/_research/"
SPLIT_DATE = pd.Timestamp("2025-01-01")
COST_ONE_WAY = 0.001


def first_touch_pos(fwd_intra, level, side, need=2):
    """First position where price holds beyond `level` for `need` bars."""
    hit = ((fwd_intra["high"] >= level).values if side == "up"
           else (fwd_intra["low"] <= level).values)
    run = 0
    for i, h in enumerate(hit):
        run = run + 1 if h else 0
        if run >= need:
            return i
    return None


def collect(symbols, horizons):
    rows = []
    for n, s in enumerate(symbols, 1):
        if n % 25 == 0:
            print(f"    [{n}/{len(symbols)}]", flush=True)
        intra = R.load_intraday(s)
        if intra is None:
            continue
        daily = R.daily_from_intraday(intra)
        if len(daily) < 300:
            continue

        for td in pd.date_range(end=daily.index[-1], periods=40, freq="ME"):
            past = daily[daily.index <= td]
            if len(past) < 280:
                continue
            cur = float(past["Close"].iloc[-1])
            if not cur or not np.isfinite(cur):
                continue
            vol = R.realized_vol(past["Close"])
            if vol is None:
                continue

            # Resistance candidates, all computed from PAST bars only.
            resist_63 = float(past["High"].tail(63).max())
            band = containment_band(past, horizon=21)
            ceiling = band["ceiling"] if band else None

            for H in horizons:
                fwd_d = daily[daily.index > td].head(H)
                if len(fwd_d) < H:
                    continue
                last_day = fwd_d.index[-1]
                fwd_i = intra[(intra["d"] > td) & (intra["d"] <= last_day)]
                if len(fwd_i) < H * 10:
                    continue

                end_close = float(fwd_d["Close"].iloc[-1])
                # (a) baseline: hold to horizon end
                ret_hold = end_close / cur - 1.0

                # (b) exit at first resistance touch (persistence rule)
                p = first_touch_pos(fwd_i, resist_63, "up")
                ret_resist = ((resist_63 / cur - 1.0) if p is not None
                              else ret_hold)

                # (c) exit at containment ceiling
                p2 = None
                if ceiling:
                    p2 = first_touch_pos(fwd_i, ceiling, "up")
                    ret_band = ((ceiling / cur - 1.0) if p2 is not None
                                else ret_hold)
                else:
                    ret_band = np.nan

                # An early exit is one extra round trip vs simply holding, so
                # it is charged one extra one-way cost. Holding is charged
                # nothing here: both legs share the same entry, and the
                # month-end exit happens under either rule.
                rows.append({
                    "sym": s, "td": td, "H": H, "vol": vol,
                    "vb": R.vol_bucket(vol),
                    "ret_hold": ret_hold,
                    "ret_resist": ret_resist - (COST_ONE_WAY if p is not None else 0),
                    "ret_band": (ret_band - COST_ONE_WAY if p2 is not None
                                 else ret_band),
                    "resist_hit": p is not None,
                    "band_hit": p2 is not None,
                })
    return pd.DataFrame(rows)


def block_bootstrap(df, col_a, col_b, n_boot=2000, seed=0):
    """Paired bootstrap resampled BY DECISION DATE.

    Never by row: same-date observations across symbols share the market move
    and are not independent, so row resampling would understate the CI badly.
    """
    rng = np.random.default_rng(seed)
    obs = float(df[col_b].mean() - df[col_a].mean())
    # index by POSITION, not by the datetime key: rng.choice returns numpy
    # datetime64 which does not hash-match pandas Timestamp group keys.
    groups = [g[[col_a, col_b]].to_numpy() for _, g in df.groupby("td")]
    if len(groups) < 3:
        return obs, float("nan"), float("nan"), float("nan")
    deltas = np.empty(n_boot)
    for i in range(n_boot):
        pick = rng.integers(0, len(groups), size=len(groups))
        sub = np.vstack([groups[j] for j in pick])
        deltas[i] = sub[:, 1].mean() - sub[:, 0].mean()
    lo, hi = np.percentile(deltas, [2.5, 97.5])
    p_better = float((deltas > 0).mean())
    return obs, lo, hi, p_better


def main():
    argv = sys.argv[1:]
    horizons = [5, 10, 15, 21]
    if "--horizons" in argv:
        horizons = [int(x) for x in argv[argv.index("--horizons") + 1].split(",")]
    limit = None
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])

    os.makedirs(OUT_DIR, exist_ok=True)
    symbols = sorted(f[:-4] for f in os.listdir(R.INTRA_DIR) if f.endswith(".csv"))
    if limit:
        symbols = symbols[:limit]

    print("=" * 76)
    print("H1 — EXIT SIDE: sell into resistance vs hold to month-end")
    print("Protocol: PREREG_sr_improvement_batch.md")
    print("=" * 76)

    df = collect(symbols, horizons)
    if df.empty:
        print("No observations."); return
    print(f"\nn={len(df)}  symbols={df['sym'].nunique()}  dates={df['td'].nunique()}")

    train = df[df["td"] < SPLIT_DATE]
    hold = df[df["td"] >= SPLIT_DATE]
    print(f"train={len(train)}  holdout={len(hold)}")

    for label, data in [("TRAIN", train), ("HOLDOUT", hold)]:
        print("\n" + "=" * 76)
        print(f"{label}")
        print("=" * 76)
        print(f"  {'H':>3} {'n':>6} {'hold%':>8} {'resist%':>9} {'band%':>8} "
              f"{'d_resist':>9} {'d_band':>8} {'hit%':>7}")
        for H in horizons:
            c = data[data["H"] == H]
            if len(c) < 50:
                continue
            h = c["ret_hold"].mean() * 100
            r = c["ret_resist"].mean() * 100
            b = c["ret_band"].mean() * 100
            print(f"  {H:>3} {len(c):>6} {h:>7.2f}% {r:>8.2f}% {b:>7.2f}% "
                  f"{r-h:>+8.2f}% {b-h:>+7.2f}% "
                  f"{c['resist_hit'].mean()*100:>6.1f}%")

    # ---- decision on HOLDOUT ----
    print("\n" + "=" * 76)
    print("DECISION (PREREG: >= +0.5pp mean AND majority of horizons)")
    print("=" * 76)
    wins_r = wins_b = 0
    deltas_r, deltas_b = [], []
    for H in horizons:
        c = hold[hold["H"] == H]
        if len(c) < 50:
            continue
        dr = (c["ret_resist"].mean() - c["ret_hold"].mean()) * 100
        db = (c["ret_band"].mean() - c["ret_hold"].mean()) * 100
        deltas_r.append(dr); deltas_b.append(db)
        wins_r += dr > 0; wins_b += db > 0
    n = len(deltas_r)
    for nm, ds, w in [("resistance exit", deltas_r, wins_r),
                      ("band-ceiling exit", deltas_b, wins_b)]:
        md = float(np.mean(ds)) if ds else float("nan")
        ok = md >= 0.5 and w > n / 2
        print(f"  {nm:<20} mean {md:+.2f}pp  wins {w}/{n}  "
              f"{'PASS gate' if ok else 'FAIL gate'}")

    # ---- bootstrap the 21d holdout, by date ----
    c21 = hold[hold["H"] == 21]
    if len(c21) > 100:
        print("\n  Paired block bootstrap (21d holdout, resampled BY DATE):")
        for nm, col in [("resistance", "ret_resist"), ("band", "ret_band")]:
            o, lo, hi, p = block_bootstrap(c21.dropna(subset=[col]),
                                           "ret_hold", col)
            print(f"    {nm:<12} delta {o*100:+.2f}pp  "
                  f"95% CI [{lo*100:+.2f}, {hi*100:+.2f}]pp  "
                  f"P(better)={p*100:.1f}%  "
                  f"{'excludes 0' if lo > 0 or hi < 0 else 'includes 0'}")

    df.to_csv(os.path.join(OUT_DIR, "sr_exit_side.csv"), index=False)
    print(f"\nwrote {OUT_DIR}sr_exit_side.csv")


if __name__ == "__main__":
    main()
