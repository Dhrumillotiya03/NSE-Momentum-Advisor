"""
fix_stale_bar.py
----------------
Auto-repairs a specific, narrow failure: a CSV's MOST RECENT bar was written
before Kite's own price for that date had settled (e.g. a repair script ran
mid-session, or Kite briefly returned a not-yet-final print), so it disagrees
with Kite's current value for that same date while every earlier bar still
matches exactly.

This is DIFFERENT from a dividend/corporate-action mismatch, and the two must
never be confused — that confusion is exactly what made the 2026-08-05
incident opaque to a non-technical operator. A dividend shifts prior bars by a
SUSTAINED, CONSTANT ratio for several consecutive sessions (measured:
BRITANNIA sat at exactly 1.665% for 7 straight days before settling); a
stale-write glitch is confined to the newest bar alone, with prior bars
matching closely (small day-to-day noise, not a plateau). Distinguishing on
"is there a run of several consecutive prior days at roughly the same offset"
rather than "does any single prior bar disagree at all" matters in practice —
a first version used the latter and misclassified 292/500 symbols as
corporate actions, most of which were just smaller instances of the SAME
glitch (e.g. a 0.1% residual) sitting on an otherwise-clean history.

Wired into update_prices_kite.py's normal run — no separate manual step. When
it fires, it prints what it fixed in plain language; the pipeline can then
proceed to append the current day's bar normally.

Usage (also importable — see auto_fix() below):
    python fix_stale_bar.py --dry-run
    python fix_stale_bar.py --apply
"""
import os
import sys
import datetime as dt

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_prices_kite as U

PRICE_DIR = "../data/price_data/"
STALE_TOL = 0.001     # only "fix" a bar that's actually wrong by more than this

# A genuine corporate action shows a SUSTAINED plateau across many prior days
# (measured: BRITANNIA sat at a constant 1.665% for 7 straight sessions before
# settling; COALINDIA the same at 1.336%). A stale-write glitch is confined to
# the newest bar alone. The first version of this script checked "does ANY
# prior bar exceed 0.1%" and that was too crude — a single noisy day (e.g.
# ABBOTINDIA's own 08-04 residual sitting at 0.11%, a smaller instance of the
# SAME glitch this script exists to fix) got misclassified as "history
# disagrees", when the correct read was "the glitch is small here too."
# Requiring the disagreement to PERSIST across several consecutive days is
# what actually distinguishes the two cases.
PRIOR_TOL = 0.003     # 0.3% — an isolated blip this size is still noise
PLATEAU_TOL = 0.001   # consecutive-day differences must match to within this
                       # to count as "the same sustained level" (a plateau)
PLATEAU_MIN_DAYS = 3  # this many consecutive prior days at the same offset

# Max spread between the four kite/csv OHLC ratios for the bar to count as a
# uniform rescale (i.e. a real corporate action). Measured on real cases:
# genuine actions land within ~1e-4 (CHENNPETRO spread 9e-5, INDUSTOWER 9e-5),
# partial candles are 100x wider (BRITANNIA 7.8e-3, PCBL 2.1e-2). 5e-4 sits an
# order of magnitude clear of both sides rather than splitting the difference.
UNIFORM_TOL = 0.0005


def check_and_fix(kite, nse, path, sym, apply=False):
    """Returns a dict describing the outcome, or None if there's nothing to do.

    outcome in {"fixed", "would_fix", "corporate_action", "no_kite_bar",
                "insufficient_history"}.
    """
    token = nse.get(sym)
    if not token:
        return None

    df = U.load_csv(path)
    good = df.dropna(subset=["Close"])
    if not len(good):
        return None
    last_date = good["Date"].max()

    k = U.kite_daily(kite, token,
                     (last_date - pd.Timedelta(days=30)).date(),
                     last_date.date())
    if k is None or pd.Timestamp(last_date) not in k.index:
        return {"outcome": "no_kite_bar", "symbol": sym}

    dates = pd.DatetimeIndex(good["Date"]).normalize()
    prior_idx = dates[dates < last_date].intersection(
        k.index[k.index < last_date])
    if len(prior_idx) < PLATEAU_MIN_DAYS:
        return {"outcome": "insufficient_history", "symbol": sym}

    csv_prior = good.set_index(dates).loc[prior_idx, "Close"].astype(float)
    kite_prior = k.loc[prior_idx, "close"].astype(float)
    prior_diff = (kite_prior / csv_prior - 1).abs().sort_index()

    row_mask = dates == last_date
    old_close = float(good.loc[row_mask, "Close"].iloc[0])
    new_close = float(k.loc[pd.Timestamp(last_date), "close"])
    delta = abs(new_close / old_close - 1) if old_close else 0.0

    # OHLC-UNIFORMITY TEST — decisive, and deliberately checked BEFORE the
    # plateau heuristic below, because it MEASURES what the blocking bar is
    # instead of inferring it from the shape of prior history.
    #
    # A corporate action rescales the whole bar: Open, High, Low and Close all
    # move by the SAME ratio (measured 2026-08-11 — CHENNPETRO 0.95820/0.95818/
    # 0.95822/0.95813, INDUSTOWER 0.96377/0.96368/0.96372/0.96373; volume
    # unchanged, ratio 1.00). A PARTIAL/stale candle cannot do that: the
    # session's Open is already final the moment it prints, so it matches Kite
    # EXACTLY, while Close/Low/Volume are frozen mid-session (measured same
    # day — COFORGE/BRITANNIA/ICICIBANK/COALINDIA/CEATLTD/PCBL all had
    # open_ratio == 1.00000 with close_ratio 1.007-1.012 and Kite volume
    # 1.03-3.4x the CSV's).
    #
    # WHY THIS IS NEEDED: those six symbols each had a REAL dividend in late
    # July whose plateau had already SETTLED (visible as 0.000% days on 07-31
    # /08-03), and then took an unrelated partial-candle write on 08-04/08-05.
    # The plateau walk-back below allows up to 2 clean days, finds the old
    # settled plateau just beyond them, and calls it "corporate action in
    # progress" — so the bar is never repaired, the CSV never advances, and
    # because agreement() splices at the CSV's newest bar, that same bad bar
    # stays the splice point forever. Self-perpetuating: all six sat stuck for
    # 5-6 sessions. Same class of bug as the ANGELONE misclassification, but
    # with the stale plateau ending 1-2 days before the glitch instead of
    # three weeks.
    kbar = k.loc[pd.Timestamp(last_date)]
    csv_row = good.loc[row_mask].iloc[0]
    ratios = []
    for col, src in [("Open", "open"), ("High", "high"),
                     ("Low", "low"), ("Close", "close")]:
        if col not in good.columns:
            continue
        cv, kv = float(csv_row[col]), float(kbar[src])
        if pd.notna(cv) and cv > 0 and pd.notna(kv) and kv > 0:
            ratios.append(kv / cv)
    # Uniform rescale across all four fields => genuine corporate action.
    # Non-uniform (Open pinned at parity, Close adrift) => partial/stale write.
    ohlc_uniform = (len(ratios) == 4
                    and (max(ratios) - min(ratios)) < UNIFORM_TOL
                    and abs(sum(ratios) / 4 - 1.0) > STALE_TOL)

    if ohlc_uniform:
        return {"outcome": "corporate_action", "symbol": sym,
                "prior_diff_pct": round(abs(sum(ratios) / 4 - 1.0) * 100, 2),
                "basis": "ohlc_uniform_rescale"}

    # PLATEAU CHECK: is TODAY's bar the tail end of a run of PLATEAU_MIN_DAYS+
    # consecutive prior days all sitting at roughly the same offset? That
    # shape is the corporate-action signature — an archive adjustment that
    # took effect some days ago and is STILL IN EFFECT right up to today.
    #
    # Must be adjacent to today, not merely present somewhere in recent
    # history. First version scanned the whole 15-day window for any plateau
    # and misclassified ANGELONE: it has an OLD, already-settled 0.309%
    # plateau from three weeks ago, unrelated to a genuine small stale-write
    # glitch (0.100%) on TODAY's bar — the old plateau isn't why today
    # disagrees, so it must not block fixing today.
    #
    # PCBL is the case this must still catch: 1.396% held for 8 sessions,
    # then one clean 0.000% day, then today's bar. That single clean day
    # breaks a naive "must end exactly at yesterday" check, so this allows up
    # to 2 clean days between the plateau and today before treating it as
    # unrelated — a real adjustment settling shows a short clean run right
    # before trading resumes at parity, not a long gap.
    # Only consulted when the OHLC test above was INCONCLUSIVE (bar not a
    # uniform rescale). If OHLC says "partial candle", that measurement wins —
    # a settled-but-recent plateau must not veto repairing a fresh glitch.
    tail = prior_diff.tail(PLATEAU_MIN_DAYS + 3)
    is_plateau = False
    if len(tail) >= PLATEAU_MIN_DAYS:
        # walk backward from the newest prior day, allow <=2 near-zero days,
        # then require PLATEAU_MIN_DAYS consecutive days at a shared offset
        vals = tail.to_numpy()
        i = len(vals) - 1
        clean_gap = 0
        while i >= 0 and vals[i] <= PRIOR_TOL and clean_gap < 2:
            i -= 1
            clean_gap += 1
        run = vals[max(0, i - PLATEAU_MIN_DAYS + 1):i + 1]
        if len(run) >= PLATEAU_MIN_DAYS and (run > PRIOR_TOL).all() \
                and (run.max() - run.min()) < PLATEAU_TOL:
            is_plateau = True

    # A plateau in PRIOR history only means a corporate action happened
    # recently — not that TODAY's bar is part of it. When the bar itself was
    # measured non-uniform above (Open at parity, Close adrift), it is a
    # partial candle sitting AFTER a settled adjustment, and repairing it is
    # both safe and necessary: leaving it makes it the permanent splice point.
    # Only treat the plateau as decisive when the bar's own shape is
    # consistent with a rescale, i.e. it moved roughly as much as the plateau.
    # `ohlc_uniform` is False here by construction (the uniform case returned
    # above), so a non-uniform bar means Open/High sit at parity while Close
    # drifted — measured proof of a partial candle. Only let the plateau
    # decide when the bar's fields are too incomplete to measure (missing
    # OHLC columns), never in preference to a successful measurement.
    bar_shape_unmeasurable = len(ratios) < 4
    if is_plateau and bar_shape_unmeasurable:
        # Sustained plateau ending at (or just before) today -> a real
        # corporate action (dividend/split), not a stale write. NEVER touch
        # this: overwriting today's bar here would create the exact
        # discontinuity adjustment exists to prevent.
        return {"outcome": "corporate_action", "symbol": sym,
                "prior_diff_pct": round(float(tail.max()) * 100, 2),
                "basis": "prior_plateau"}

    if delta < STALE_TOL:
        return None   # already correct, nothing to do

    result = {"outcome": "would_fix" if not apply else "fixed",
              "symbol": sym, "date": str(last_date.date()),
              "old_close": old_close, "new_close": new_close,
              "delta_pct": round(delta * 100, 2)}

    if apply:
        full_dates = pd.DatetimeIndex(df["Date"]).normalize()
        idx = df.index[full_dates == last_date]
        for col, src in [("Open", "open"), ("High", "high"),
                         ("Low", "low"), ("Close", "close"),
                         ("Volume", "volume")]:
            if col in df.columns:
                df.loc[idx, col] = float(k.loc[pd.Timestamp(last_date), src])
        df.to_csv(path, index=False)

    return result


def auto_fix(kite, nse, symbols=None, apply=True):
    """Importable entry point for update_prices_kite.py.

    Scans (or a given subset of) price_data/*.csv, silently fixes genuine
    stale-write glitches, and returns (fixed, corporate_actions) — two lists
    of symbol names — so the caller can report both plainly without the
    operator needing to know the mechanism.
    """
    files = sorted(f for f in os.listdir(PRICE_DIR) if f.endswith(".csv"))
    if symbols:
        want = {s.replace(".NS", "") for s in symbols}
        files = [f for f in files if f[:-7] in want]

    fixed, corp_actions = [], []
    for f in files:
        sym = f[:-7]
        r = check_and_fix(kite, nse, PRICE_DIR + f, sym, apply=apply)
        if r is None:
            continue
        if r["outcome"] in ("fixed", "would_fix"):
            fixed.append(sym)
        elif r["outcome"] == "corporate_action":
            corp_actions.append(sym)
    return fixed, corp_actions


def main():
    apply = "--apply" in sys.argv
    import kite_auth
    kite = kite_auth.get_kite_client()
    if kite is None:
        print("ABORT: no cached Kite token — run: python kite_auth.py refresh")
        sys.exit(1)
    nse = {i["tradingsymbol"]: i["instrument_token"] for i in kite.instruments("NSE")}

    files = sorted(f for f in os.listdir(PRICE_DIR) if f.endswith(".csv"))
    fixed, corp, skipped = [], [], []
    for i, f in enumerate(files, 1):
        sym = f[:-7]
        if i % 25 == 0 or i == len(files):
            print(f"  ... {i}/{len(files)} scanned", file=sys.stderr)
        r = check_and_fix(kite, nse, PRICE_DIR + f, sym, apply=apply)
        if r is None:
            continue
        if r["outcome"] in ("fixed", "would_fix"):
            print(f"  {sym:<14} {r['date']}  {r['old_close']:>10.2f} -> "
                  f"{r['new_close']:>10.2f}  ({r['delta_pct']:+.2f}%)")
            fixed.append(sym)
        elif r["outcome"] == "corporate_action":
            corp.append((sym, r["prior_diff_pct"]))
        else:
            skipped.append((sym, r["outcome"]))

    tag = "FIXED" if apply else "WOULD FIX"
    print(f"\n{tag}: {len(fixed)} stale-write glitch(es)")
    if corp:
        print(f"LEFT ALONE (corporate action, not a glitch): {len(corp)} — "
              + ", ".join(f"{s}({d}%)" for s, d in corp[:10])
              + (" ..." if len(corp) > 10 else ""))
    if not apply and fixed:
        print("Re-run with --apply to write these.")


if __name__ == "__main__":
    main()
