"""
research_tradeable_levels.py
----------------------------
Does a TRADEABLE containment band exist? See PREREG_tradeable_levels.md for the
frozen protocol — decision rules below are copied from it and must not drift.

THE QUESTION
------------
The production S/R output answers P(touch): "will price reach this level". On
2026-08-04 that produced median S1 at -2.1% and median R1 at +1.9%, with
corr(|distance|, prob) = -0.88/-0.91. The probability column had become a
restatement of distance, and a 94% "support" is a level with a ~6% chance of
holding — the number read backwards.

The user needs the complementary quantity: a level price does NOT breach over
the month AND at which a trade entered there is profitable.

THE TENSION (this is the whole problem)
---------------------------------------
    rarely breached  <=> far  <=> rarely filled <=> untradeable
    often filled     <=> near <=> often breached

So this is an expected-value question with a possible interior optimum:

    E[profit] = P(fill) x E[return | fill]

The claim under test is that some intermediate distance maximises E[profit].
THAT CLAIM MAY BE FALSE. A monotone-decreasing or flat/noisy profile is a
legitimate NULL result and is reported as such.

FILL REALISM — WHY INTRADAY DATA IS REQUIRED
--------------------------------------------
sr_build_touchtable.touched() fills on `Low <= level` anywhere in the window.
For a user who is NOT an intraday trader (irregular presence at the machine,
monthly rebalance mandate), a Low printed for six minutes is a fill that would
never have happened. That bias is largest for tight levels that spike-touch and
recover — i.e. most of the current panel.

So fills are defined on 15-minute bars with a PERSISTENCE requirement: price
must sit at/through the level for >= K consecutive bars. Five rules are scored
so the answer's dependence on this assumption is visible, not hidden:

    dailylow : daily Low <= level          (THE CURRENT METHOD — baseline only)
    bar1     : >= 1 consecutive 15m bar    (optimistic bound)
    bar2     : >= 2 bars, ~30 min          (PRIMARY — the user's realistic case)
    bar8     : >= 8 bars, ~2 hours         (conservative)
    close    : daily CLOSE beyond level    (strictest, fully automatable)

If an edge exists only under dailylow/bar1 and dies under bar2, it is not real
for this user. That comparison is the point, not a footnote.

Usage:
    python research_tradeable_levels.py --horizon 21
    python research_tradeable_levels.py --horizon 21 --limit 60   # quick
"""
import os
import sys
import json
import datetime as dt

import numpy as np
import pandas as pd

INTRA_DIR = "../data/intraday_data/"
OUT_DIR = "../data/_research/"

# ---- protocol constants (from PREREG, do not tune) ----
SPLIT_DATE = pd.Timestamp("2025-01-01")   # train < this <= holdout
COST_ONE_WAY = 0.001                      # strategy_config.COST
ALPHA = 0.15                              # target breach rate -> 85% containment

VOL_EDGES = [0.0, 25.0, 35.0, 45.0, 1e9]
VOL_LABELS = ["<25%", "25-35%", "35-45%", "45%+"]

# Candidate entry distances below price (support side). Deliberately spans
# "tighter than today's S1" to "far enough it rarely fills" so the shape of
# E[profit] vs distance is visible rather than assumed.
DISTANCES = [0.005, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12, 0.15, 0.20]

FILL_RULES = ["dailylow", "bar1", "bar2", "bar8", "close"]
PRIMARY_RULE = "bar2"

BARS_PER_SESSION = 25          # NSE 09:15-15:30 at 15min
MIN_SESSION_BARS = 20          # below this = Muhurat/special session, excluded


# ------------------------------------------------------------------ loading
def load_intraday(sym):
    """15m bars, normal sessions only.

    Muhurat (evening, ~4 bars) and Saturday contingency sessions (~7 bars) are
    dropped: they are real trades but not normal days, and a 4-bar session makes
    a '>=2 consecutive bars' persistence test trivially easy to satisfy. Verified
    present in the data: 5 of 742 RELIANCE sessions (2023-11-12 Sun, 2024-03-02
    Sat, 2024-05-18 Sat, 2024-11-01 Fri evening, 2025-10-21 Tue afternoon).
    """
    path = os.path.join(INTRA_DIR, f"{sym}.csv")
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, parse_dates=["date"])
    if df.empty:
        return None
    df["d"] = df["date"].dt.normalize()
    counts = df.groupby("d").size()
    good = counts[counts >= MIN_SESSION_BARS].index
    df = df[df["d"].isin(good)]
    return df.sort_values("date").reset_index(drop=True) if len(df) else None


def daily_from_intraday(intra):
    """Daily OHLC aggregated from the SAME source as the paths.

    Deliberately not read from price_data/: those CSVs are yfinance-ADJUSTED
    while Kite is UNADJUSTED (kite/csv on NATIONALUM: 1.744 in 2016 -> 1.000
    today). Distances are ratios, so mixing scales silently corrupts far cells.
    """
    g = intra.groupby("d")
    return pd.DataFrame({
        "Open": g["open"].first(), "High": g["high"].max(),
        "Low": g["low"].min(), "Close": g["close"].last(),
        "Volume": g["volume"].sum(),
    })


def realized_vol(close):
    r = close.pct_change().dropna().tail(252)
    if len(r) < 30:
        return None
    return float(r.std() * np.sqrt(252) * 100)


def vol_bucket(v):
    for i in range(len(VOL_LABELS)):
        if VOL_EDGES[i] <= v < VOL_EDGES[i + 1]:
            return VOL_LABELS[i]
    return VOL_LABELS[-1]


# ------------------------------------------------------------ fill mechanics
def first_fill_index(fwd_intra, level, side, rule):
    """Index of the bar at which the order fills, or None.

    `fwd_intra` must contain ONLY bars strictly after the decision date.
    Returns a positional index into fwd_intra so the exit leg can start after it.
    """
    if side == "down":
        hit = (fwd_intra["low"] <= level).values
    else:
        hit = (fwd_intra["high"] >= level).values

    if rule == "bar1":
        idx = np.flatnonzero(hit)
        return int(idx[0]) if len(idx) else None

    if rule in ("bar2", "bar8"):
        need = 2 if rule == "bar2" else 8
        # first position where `need` consecutive bars are all hits
        run = 0
        for i, h in enumerate(hit):
            run = run + 1 if h else 0
            if run >= need:
                return i          # fill completes at the end of the run
        return None

    if rule == "dailylow":
        # daily-bar semantics: any session whose Low/High crosses the level.
        # Fill is attributed to the LAST bar of that session, which is the
        # earliest point a daily-bar user could have acted on it.
        d = fwd_intra.groupby("d")
        agg = d["low"].min() if side == "down" else d["high"].max()
        crossed = agg[(agg <= level)] if side == "down" else agg[(agg >= level)]
        if not len(crossed):
            return None
        day = crossed.index[0]
        pos = np.flatnonzero((fwd_intra["d"] == day).values)
        return int(pos[-1]) if len(pos) else None

    if rule == "close":
        d = fwd_intra.groupby("d")["close"].last()
        crossed = d[(d <= level)] if side == "down" else d[(d >= level)]
        if not len(crossed):
            return None
        day = crossed.index[0]
        pos = np.flatnonzero((fwd_intra["d"] == day).values)
        return int(pos[-1]) if len(pos) else None

    raise ValueError(rule)


def evaluate_entry(fwd_intra, level, resistance, rule, horizon_end_price):
    """Simulate: limit-buy at `level`; exit at resistance or horizon end.

    Returns None if never filled. Otherwise a dict of net returns.
    Exit rule = "whichever comes first" (user choice 2026-08-04):
      - if resistance is reached after fill -> exit there
      - else exit at the horizon's final close
    """
    fi = first_fill_index(fwd_intra, level, "down", rule)
    if fi is None:
        return None

    after = fwd_intra.iloc[fi + 1:]
    exit_px, exit_kind = horizon_end_price, "horizon"

    if resistance is not None and len(after):
        ri = first_fill_index(after, resistance, "up", rule)
        if ri is not None:
            exit_px, exit_kind = resistance, "resistance"

    gross = exit_px / level - 1.0
    net = gross - 2 * COST_ONE_WAY          # round trip, cost applied before wins
    return {"net": net, "gross": gross, "exit_kind": exit_kind}


def contained(fwd_daily, level):
    """Did price stay above `level` for the whole window?

    Uses CLOSES, not Lows: a single intraday wick through a level is not the
    'stock dipped below this for the month' event the user cares about, and
    scoring it as a breach would make every band look broken.
    """
    return bool((fwd_daily["Close"] > level).all())


# ------------------------------------------------------------- observation
def collect(symbols, horizon, verbose=True):
    rows = []
    for n, sym in enumerate(symbols, 1):
        if verbose and n % 25 == 0:
            print(f"    [{n}/{len(symbols)}]", flush=True)
        intra = load_intraday(sym)
        if intra is None or len(intra) < BARS_PER_SESSION * 300:
            continue
        daily = daily_from_intraday(intra)
        if len(daily) < 280:
            continue

        days = daily.index
        # monthly decision dates -> ~1 obs/symbol/month, keeps observations
        # closer to independent than a daily grid would.
        dec_dates = pd.date_range(end=days[-1], periods=40, freq="ME")
        for td in dec_dates:
            past = daily[daily.index <= td]
            fwd_d = daily[daily.index > td].head(horizon)
            if len(past) < 260 or len(fwd_d) < horizon:
                continue

            vol = realized_vol(past["Close"])
            if vol is None:
                continue
            cur = float(past["Close"].iloc[-1])
            if not cur or not np.isfinite(cur):
                continue

            # forward intraday leg: strictly after the decision date, capped at
            # the horizon's last session
            last_day = fwd_d.index[-1]
            fwd_i = intra[(intra["d"] > td) & (intra["d"] <= last_day)]
            if len(fwd_i) < horizon * 10:
                continue

            horizon_close = float(fwd_d["Close"].iloc[-1])

            # resistance for the exit leg: prior 63d high (a simple, honest
            # structural ceiling). Pivot-anchored variants are tested in 8b.
            resistance = float(past["High"].tail(63).max())

            # Trend state at the decision date (PREREG amendment A1). Both use
            # only `past`, so no lookahead. A fixed %-distance limit order
            # cannot distinguish "wobble inside an uptrend" from "a downtrend
            # started", and fills preferentially in the latter — adverse
            # selection. This axis is what separates the two.
            ma50 = float(past["Close"].tail(50).mean())
            above_50dma = bool(cur > ma50)
            if len(past) >= 127:
                mom126 = float(cur / float(past["Close"].iloc[-127]) - 1.0)
            else:
                mom126 = np.nan
            trend = ("UP" if above_50dma and (mom126 > 0 or not np.isfinite(mom126))
                     else "DOWN")

            for dist in DISTANCES:
                level = cur * (1 - dist)
                rec = {
                    "sym": sym, "td": td, "dist": dist, "vol": vol,
                    "vb": vol_bucket(vol), "cur": cur, "level": level,
                    "contained": contained(fwd_d, level),
                    "above_50dma": above_50dma, "mom126": mom126,
                    "trend": trend,
                }
                for rule in FILL_RULES:
                    r = evaluate_entry(fwd_i, level, resistance, rule,
                                       horizon_close)
                    rec[f"fill_{rule}"] = r is not None
                    rec[f"net_{rule}"] = r["net"] if r else np.nan
                    rec[f"exit_{rule}"] = r["exit_kind"] if r else None
                rows.append(rec)
    return pd.DataFrame(rows)


# --------------------------------------------------------------- reporting
def summarize(df, rule):
    """Per (vol bucket, distance): fill rate, win rate|fill, median ret|fill, EV."""
    out = []
    for vb in VOL_LABELS:
        for d in DISTANCES:
            c = df[(df["vb"] == vb) & (df["dist"] == d)]
            if len(c) < 30:
                continue
            filled = c[c[f"fill_{rule}"]]
            fr = len(filled) / len(c)
            if len(filled) >= 10:
                win = float((filled[f"net_{rule}"] > 0).mean())
                med = float(filled[f"net_{rule}"].median())
                mean = float(filled[f"net_{rule}"].mean())
            else:
                win = med = mean = np.nan
            out.append({
                "vb": vb, "dist": d, "n": len(c), "n_fill": len(filled),
                "fill_rate": fr, "win_rate": win, "med_ret": med,
                "ev": fr * mean if np.isfinite(mean) else np.nan,
                "containment": float(c["contained"].mean()),
            })
    return pd.DataFrame(out)


def main():
    argv = sys.argv[1:]
    horizon = 21
    if "--horizon" in argv:
        i = argv.index("--horizon"); horizon = int(argv[i + 1])
    limit = None
    if "--limit" in argv:
        i = argv.index("--limit"); limit = int(argv[i + 1])

    os.makedirs(OUT_DIR, exist_ok=True)
    symbols = sorted(f[:-4] for f in os.listdir(INTRA_DIR) if f.endswith(".csv"))
    if limit:
        symbols = symbols[:limit]

    print("=" * 74)
    print(f"TRADEABLE LEVELS — horizon {horizon}d, {len(symbols)} symbols")
    print("Protocol: PREREG_tradeable_levels.md (frozen before collection)")
    print("=" * 74)

    df = collect(symbols, horizon)
    if df.empty:
        print("No observations."); return
    print(f"\nCollected {len(df)} observations "
          f"({df['sym'].nunique()} symbols, {df['td'].nunique()} dates)")

    train = df[df["td"] < SPLIT_DATE]
    hold = df[df["td"] >= SPLIT_DATE]
    print(f"TRAIN {len(train)} (<{SPLIT_DATE.date()})   "
          f"HOLDOUT {len(hold)} (>={SPLIT_DATE.date()})")
    if not len(train) or not len(hold):
        print("Insufficient split — aborting."); return

    # ---- 8c: does intraday persistence change the answer? ----
    print("\n" + "=" * 74)
    print("FILL-RULE SENSITIVITY  (8c — does intraday data matter here?)")
    print("=" * 74)
    print(f"  {'rule':<10} {'fill%':>7} {'win%|fill':>10} {'med ret':>9} {'EV':>8}")
    for rule in FILL_RULES:
        f = train[train[f"fill_{rule}"]]
        if not len(f):
            continue
        fr = len(f) / len(train)
        win = (f[f"net_{rule}"] > 0).mean() * 100
        med = f[f"net_{rule}"].median() * 100
        ev = fr * f[f"net_{rule}"].mean() * 100
        tag = "  <- PRIMARY" if rule == PRIMARY_RULE else ""
        tag += "  (current method)" if rule == "dailylow" else ""
        print(f"  {rule:<10} {fr*100:>6.1f}% {win:>9.1f}% {med:>8.2f}% "
              f"{ev:>7.3f}%{tag}")

    # ---- E[profit] shape vs distance (the interior-optimum claim) ----
    print("\n" + "=" * 74)
    print(f"E[profit] vs DISTANCE — TRAIN, rule={PRIMARY_RULE}")
    print("  (tests the interior-optimum claim; monotone/flat = NULL result)")
    print("=" * 74)
    s = summarize(train, PRIMARY_RULE)
    for vb in VOL_LABELS:
        sub = s[s["vb"] == vb]
        if not len(sub):
            continue
        print(f"\n  vol {vb}:")
        print(f"    {'dist':>6} {'n':>6} {'fill%':>7} {'win%':>7} "
              f"{'med%':>7} {'EV%':>7} {'contain%':>9}")
        for _, r in sub.iterrows():
            print(f"    {r['dist']*100:>5.1f}% {r['n']:>6.0f} "
                  f"{r['fill_rate']*100:>6.1f}% "
                  f"{r['win_rate']*100 if np.isfinite(r['win_rate']) else float('nan'):>6.1f}% "
                  f"{r['med_ret']*100 if np.isfinite(r['med_ret']) else float('nan'):>6.2f}% "
                  f"{r['ev']*100 if np.isfinite(r['ev']) else float('nan'):>6.3f}% "
                  f"{r['containment']*100:>8.1f}%")

    # ---- A1: trend conditioning (the adverse-selection test) ----
    print("\n" + "=" * 74)
    print(f"TREND CONDITIONING (PREREG A1) — TRAIN, rule={PRIMARY_RULE}")
    print("  UP = close>50DMA and 126d momentum>0, measured at decision date")
    print("=" * 74)
    for tr_state in ["UP", "DOWN"]:
        sub = train[train["trend"] == tr_state]
        if not len(sub):
            continue
        f = sub[sub[f"fill_{PRIMARY_RULE}"]]
        if not len(f):
            continue
        print(f"\n  trend={tr_state}  (n={len(sub)}, fills={len(f)})")
        print(f"    {'dist':>6} {'n':>6} {'fill%':>7} {'win%':>7} "
              f"{'med%':>7} {'EV%':>7} {'contain%':>9}")
        for d in DISTANCES:
            c = sub[sub["dist"] == d]
            if len(c) < 30:
                continue
            fl = c[c[f"fill_{PRIMARY_RULE}"]]
            fr = len(fl) / len(c)
            if len(fl) >= 10:
                win = (fl[f"net_{PRIMARY_RULE}"] > 0).mean() * 100
                med = fl[f"net_{PRIMARY_RULE}"].median() * 100
                ev = fr * fl[f"net_{PRIMARY_RULE}"].mean() * 100
            else:
                win = med = ev = float("nan")
            print(f"    {d*100:>5.1f}% {len(c):>6} {fr*100:>6.1f}% "
                  f"{win:>6.1f}% {med:>6.2f}% {ev:>6.3f}% "
                  f"{c['contained'].mean()*100:>8.1f}%")

    df.to_csv(os.path.join(OUT_DIR, f"tradeable_levels_raw_{horizon}d.csv"),
              index=False)
    s.to_csv(os.path.join(OUT_DIR, f"tradeable_levels_train_{horizon}d.csv"),
             index=False)
    print(f"\nwrote {OUT_DIR}tradeable_levels_*_{horizon}d.csv")

    # ---- containment calibration: which distance actually holds 85%? ----
    print("\n" + "=" * 74)
    print(f"CONTAINMENT CALIBRATION — distance achieving {(1-ALPHA)*100:.0f}% "
          f"hold rate (TRAIN)")
    print("=" * 74)
    for vb in VOL_LABELS:
        sub = s[s["vb"] == vb].sort_values("dist")
        if not len(sub):
            continue
        ok = sub[sub["containment"] >= (1 - ALPHA)]
        if len(ok):
            r = ok.iloc[0]
            print(f"  {vb:<8} -> {r['dist']*100:>5.1f}% below price "
                  f"(actual containment {r['containment']*100:.1f}%, "
                  f"fill {r['fill_rate']*100:.1f}%, "
                  f"win {r['win_rate']*100 if np.isfinite(r['win_rate']) else float('nan'):.1f}%)")
        else:
            best = sub.iloc[-1]
            print(f"  {vb:<8} -> NONE of the tested distances reach "
                  f"{(1-ALPHA)*100:.0f}% containment "
                  f"(max {best['containment']*100:.1f}% at {best['dist']*100:.1f}%)")

    # ---- ADOPTION: select on TRAIN, score HOLDOUT exactly once ----
    print("\n" + "=" * 74)
    print("ADOPTION TEST (PREREG §8a + A1) — select on TRAIN, score HOLDOUT once")
    print("=" * 74)
    print("  Thresholds (NOT relaxed): fill>=20%, win>=55%, median>0,")
    print(f"  containment in [0.75,0.95] of claimed {(1-ALPHA)*100:.0f}%\n")

    verdicts = []
    for tr_state in ["UP", "DOWN"]:
        tr_sub = train[train["trend"] == tr_state]
        ho_sub = hold[hold["trend"] == tr_state]
        if len(tr_sub) < 50 or len(ho_sub) < 50:
            print(f"  trend={tr_state}: insufficient data "
                  f"(train {len(tr_sub)}, holdout {len(ho_sub)}) — not scored")
            continue

        # SELECT on train: highest EV among distances with >=20% fill rate.
        best, best_ev = None, -np.inf
        for d in DISTANCES:
            c = tr_sub[tr_sub["dist"] == d]
            fl = c[c[f"fill_{PRIMARY_RULE}"]]
            if len(c) < 30 or len(fl) < 10:
                continue
            fr = len(fl) / len(c)
            if fr < 0.20:
                continue
            ev = fr * fl[f"net_{PRIMARY_RULE}"].mean()
            if ev > best_ev:
                best, best_ev = d, ev
        if best is None:
            print(f"  trend={tr_state}: no TRAIN distance met the 20% fill "
                  f"floor — REJECT")
            verdicts.append((tr_state, None, "REJECT (no candidate)"))
            continue

        # SCORE on holdout at that distance — once, no re-selection.
        c = ho_sub[ho_sub["dist"] == best]
        fl = c[c[f"fill_{PRIMARY_RULE}"]]
        fr = len(fl) / len(c) if len(c) else 0.0
        win = float((fl[f"net_{PRIMARY_RULE}"] > 0).mean()) if len(fl) >= 10 else np.nan
        med = float(fl[f"net_{PRIMARY_RULE}"].median()) if len(fl) >= 10 else np.nan
        cont = float(c["contained"].mean()) if len(c) else np.nan

        checks = {
            "fill>=20%": fr >= 0.20,
            "win>=55%": bool(np.isfinite(win) and win >= 0.55),
            "median>0": bool(np.isfinite(med) and med > 0),
            "containment in band": bool(np.isfinite(cont)
                                        and 0.75 <= cont / (1 - ALPHA) <= 0.95 / (1 - ALPHA)
                                        and 0.75 <= cont <= 0.95),
        }
        ok = all(checks.values())
        print(f"  trend={tr_state}  TRAIN-selected distance {best*100:.1f}%  "
              f"(train EV {best_ev*100:+.3f}%)")
        print(f"    HOLDOUT n={len(c)}  fills={len(fl)}")
        print(f"      fill rate    {fr*100:>6.1f}%   {'PASS' if checks['fill>=20%'] else 'FAIL'}")
        print(f"      win | fill   {win*100 if np.isfinite(win) else float('nan'):>6.1f}%   {'PASS' if checks['win>=55%'] else 'FAIL'}")
        print(f"      median ret   {med*100 if np.isfinite(med) else float('nan'):>6.2f}%   {'PASS' if checks['median>0'] else 'FAIL'}")
        print(f"      containment  {cont*100 if np.isfinite(cont) else float('nan'):>6.1f}%   {'PASS' if checks['containment in band'] else 'FAIL'}")
        print(f"    VERDICT: {'ADOPT' if ok else 'REJECT'}\n")
        verdicts.append((tr_state, best, "ADOPT" if ok else "REJECT"))

    print("=" * 74)
    for st, d, v in verdicts:
        print(f"  trend={st:<5} distance={str(round(d*100,1))+'%' if d else 'n/a':<7} {v}")
    print("=" * 74)


if __name__ == "__main__":
    main()
