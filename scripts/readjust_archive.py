"""
readjust_archive.py
-------------------
Repairs the ONE failure mode that update_prices_kite.py cannot fix and that
never self-heals: the archive is stuck at an OLDER corporate-action adjustment
level than the live feed, so the splice check refuses forever.

THE DEADLOCK. append_new() splices at the newest bar the CSV and Kite share.
When a dividend goes ex, BOTH live sources re-adjust the whole history down by
a constant factor (measured 2026-08-31: BATAINDIA ratio 0.96680 identical in
Kite and yfinance; CESC 0.96428). The archive is never rewritten, so it alone
keeps the pre-dividend level. The splice point then disagrees by the dividend
ratio, append_new returns "disagree", nothing is written — and because the
refused bar stays the newest bar, it stays the splice point. Self-perpetuating,
exactly like the 2026-08-11 partial-candle deadlock, but with a different cause
and a different repair. Observed freezes: CHENNPETRO / INDUSTOWER / HINDPETRO
(repaired 2026-08-14), BATAINDIA / CESC (7 sessions, repaired 2026-08-31).

WHY THE OPERATOR MESSAGE WAS WRONG. sr_daily_logger reports these as "mid
corporate action, self-resolves, re-running will not speed this up". For a
mismatch INSIDE the tolerance that is true. Past AGREE_TOL it is false: the
series is frozen and the missing sessions are unrecoverable if the symbol is
ever needed live.

WHY A RESCALE, NOT A yfinance RE-DOWNLOAD. redownload_fix.py was retired for
good reasons that still stand — yfinance intermittently serves NaN-OHLC bars,
and a wholesale rewrite moves every historical price. A rescale avoids both: it
touches only a single multiplicative constant, keeps every archive row's own
provenance, leaves Volume alone, and is verified against Kite before and after.
It also reproduces what the accepted 2026-08-14 repair actually did (verified
against data/_quarantine/INDUSTOWER.NS_pre_readjust_2026-08-14.csv: O/H/L
ratio 0.963730 with std 8e-8, volume untouched).

TWO REFERENCES, BECAUSE KITE'S HISTORY IS NOT DIVIDEND-CONSISTENT. Measured
2026-08-31 on BATAINDIA, which paid TWO dividends (Rs 9 ex 07-31, Rs 25 ex
08-19): kite/yfinance is 1.01301 for bars BEFORE 07-31 and exactly 1.00000
from 07-31 on. yfinance back-adjusted the Rs 9; Kite did not. So Kite cannot
be used to judge whether the ARCHIVE is internally consistent — an earlier
version of this script did exactly that, measured a two-level 0.97938/0.96680
split across the window, and refused a repair that was in fact correct.

  * yfinance validates the ARCHIVE (it is the archive's native adjustment
    convention — the CSVs were built from it, and yf/csv is a single uniform
    factor across all 2873 rows when the only difference is a missed
    adjustment).
  * Kite validates the SPLICE POINT, which is the only bar append_new
    actually compares. Old bars never enter that decision.

Both must pass: uniform against yfinance over the whole history proves this is
a pure adjustment shift and not a bad print, and agreement with Kite at the
splice point proves the repair actually unblocks the append.

Close is deliberately EXCLUDED from the factor estimate: a stale/partial newest
bar corrupts Close while Open/High/Low stay clean (both symbols carried a wrong
2026-08-04 Close on top of the adjustment, surfaced in the residual report
rather than silently averaged into the factor).

Usage:
    python readjust_archive.py                 # scan all, dry run
    python readjust_archive.py --apply         # repair everything it finds
    python readjust_archive.py BATAINDIA CESC --apply
Then run `python update_prices_kite.py` to append the freed-up sessions.
"""
import os
import sys
import shutil
import datetime as dt

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_prices_kite as U

PRICE_DIR = "../data/price_data/"
QUARANTINE = "../data/_quarantine/"

# Max spread between the Open/High/Low ratios (and of any single bar around
# the fitted factor) for the shift to count as a uniform whole-history rescale.
# Measured on real cases: a genuine missed adjustment holds to ~1e-7 across
# 2873 rows, so 1e-4 leaves three orders of magnitude of headroom while staying
# far below the ~1e-2 spread a partial candle or bad print produces.
UNIFORM_TOL = 1e-4
# Only act when the archive is off by more than the splice tolerance — below
# it, append_new already works and history must be left alone.
MIN_SHIFT = U.AGREE_TOL
# Bars still disagreeing with yfinance AFTER the rescale get reported.
RESIDUAL_TOL = 0.001
# Minimum overlapping bars before a factor is trusted.
MIN_OVERLAP = 200


def _yf_history(sym):
    """Full adjusted history from the archive's native source. Returns None if
    the pull is unusable — yfinance intermittently serves bars with real Volume
    but NaN OHLC, which is precisely why redownload_fix.py was retired, so a
    pull containing any is refused outright rather than partially trusted."""
    import yfinance as yf
    try:
        y = yf.Ticker(f"{sym}.NS").history(period="max", auto_adjust=True)
    except Exception:
        return None
    if y is None or y.empty:
        return None
    if getattr(y.index, "tz", None) is not None:
        y.index = y.index.tz_localize(None)
    y.index = pd.DatetimeIndex(y.index).normalize()
    cols = ["Open", "High", "Low", "Close"]
    if any(c not in y.columns for c in cols):
        return None
    if y[cols].isna().any(axis=1).any():
        return None
    return y


def diagnose(kite, token, path, sym):
    """Measure the archive's missed adjustment. Two independent references:
    yfinance for whole-history uniformity, Kite for the splice point."""
    df = U.load_csv(path)
    good = df.dropna(subset=["Close"])
    if good.empty:
        return {"outcome": "empty"}
    last = pd.Timestamp(good["Date"].max())
    dates = pd.DatetimeIndex(good["Date"]).normalize()

    y = _yf_history(sym)
    if y is None:
        return {"outcome": "no_yf_data"}
    idx = dates.intersection(y.index)
    if len(idx) < MIN_OVERLAP:
        return {"outcome": "insufficient_overlap", "n": len(idx)}

    csv_o = good.set_index(dates)
    ratios = {}
    for col in ("Open", "High", "Low"):
        if col not in csv_o.columns:
            continue
        a = pd.to_numeric(csv_o.loc[idx, col], errors="coerce").astype(float)
        b = y.loc[idx, col].astype(float)
        m = (a > 0) & np.isfinite(a) & (b > 0) & np.isfinite(b)
        if m.sum():
            ratios[col] = b[m] / a[m]
    if len(ratios) < 3:
        return {"outcome": "unmeasurable"}

    meds = {c: float(s.median()) for c, s in ratios.items()}
    factor = float(np.median(list(meds.values())))
    field_spread = max(meds.values()) - min(meds.values())
    # Every individual bar must sit on the factor too, not just the medians —
    # that is what separates a whole-history adjustment from a few odd bars.
    worst_bar = max(float((s / factor - 1).abs().max()) for s in ratios.values())

    # SPLICE CHECK against Kite: would the rescale actually unblock append_new?
    k = U.kite_daily(kite, token, (last - pd.Timedelta(days=30)).date(),
                     last.date())
    splice_after = None
    if k is not None and not k.empty and pd.Timestamp(last) in k.index:
        cur = float(csv_o.loc[pd.Timestamp(last), "Close"])
        kc = float(k.loc[pd.Timestamp(last), "close"])
        if np.isfinite(cur) and cur > 0 and np.isfinite(kc):
            splice_after = abs(kc / (cur * factor) - 1.0)

    return {"outcome": "measured", "factor": factor, "n": len(idx),
            "field_spread": field_spread, "worst_bar_dev": worst_bar,
            "shift_pct": (factor - 1) * 100, "last": str(last.date()),
            "splice_after": splice_after, "df": df, "yf": y, "idx": idx}


def repair(path, sym, d, apply=False):
    factor = d["factor"]
    df = d["df"]
    stamp = dt.date.today().isoformat()
    if apply:
        os.makedirs(QUARANTINE, exist_ok=True)
        shutil.copy2(path, os.path.join(
            QUARANTINE, f"{sym}.NS_pre_readjust_{stamp}.csv"))
        out = df.copy()
        for col in ("Open", "High", "Low", "Close"):
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce") * factor
        # Volume is a share count — a price adjustment must never touch it.
        out.to_csv(path, index=False)
    return factor


def residuals(path, d):
    """Bars still disagreeing with yfinance after the rescale."""
    df = U.load_csv(path)
    good = df.dropna(subset=["Close"])
    dates = pd.DatetimeIndex(good["Date"]).normalize()
    idx = dates.intersection(d["yf"].index)
    a = pd.to_numeric(good.set_index(dates).loc[idx, "Close"],
                      errors="coerce").astype(float)
    b = d["yf"].loc[idx, "Close"].astype(float)
    r = (b / a - 1).abs()
    return r[r > RESIDUAL_TOL].dropna()


def main():
    argv = sys.argv[1:]
    apply = "--apply" in argv
    only = {a.upper() for a in argv if not a.startswith("--")}

    try:
        import kite_auth
        kite = kite_auth.get_kite_client()
        kite.profile()
    except Exception as e:
        print(f"ABORT: kite unavailable ({e}) — run: python kite_auth.py refresh")
        sys.exit(1)

    nse = {i["tradingsymbol"]: i["instrument_token"]
           for i in kite.instruments("NSE")}

    files = sorted(f for f in os.listdir(PRICE_DIR) if f.endswith(".csv"))
    if only:
        files = [f for f in files if f[:-4].replace(".NS", "") in only]

    print(f"Archive re-adjustment scan — {len(files)} symbol(s) "
          f"{'(DRY RUN — nothing written)' if not apply else '(APPLYING)'}\n")

    fixed, refused, skipped = [], [], 0
    for f in files:
        sym = f[:-4].replace(".NS", "")
        token = nse.get(sym)
        if not token:
            skipped += 1
            continue
        path = PRICE_DIR + f
        d = diagnose(kite, token, path, sym)
        if d["outcome"] != "measured":
            skipped += 1
            continue
        if abs(d["factor"] - 1) <= MIN_SHIFT:
            continue     # within splice tolerance — leave history alone

        uniform = (d["field_spread"] < UNIFORM_TOL
                   and d["worst_bar_dev"] < UNIFORM_TOL)
        sp = d["splice_after"]
        splice_ok = sp is not None and sp <= U.AGREE_TOL

        print(f"{sym:14s} last={d['last']}  shift={d['shift_pct']:+.3f}%  "
              f"factor={d['factor']:.6f}  n={d['n']}")
        print(f"               vs yfinance: field_spread={d['field_spread']:.1e}  "
              f"worst_bar={d['worst_bar_dev']:.1e}  "
              f"{'UNIFORM' if uniform else 'NOT UNIFORM'}")
        print(f"               vs Kite at splice after rescale: "
              + (f"{sp*100:.3f}% ({'unblocks' if splice_ok else 'STILL BLOCKED'})"
                 if sp is not None else "no Kite bar at splice point"))

        if not uniform:
            print("               REFUSED — not a uniform whole-history shift, "
                  "so this is not a missed adjustment. Investigate by hand.")
            refused.append(sym)
            continue
        if not splice_ok:
            print("               REFUSED — rescale would not unblock the "
                  "append; the archive is not the only thing wrong here.")
            refused.append(sym)
            continue

        repair(path, sym, d, apply=apply)
        if apply:
            res = residuals(path, d)
            print(f"               rescaled by {d['factor']:.6f} "
                  f"(backup: _quarantine/{sym}.NS_pre_readjust_"
                  f"{dt.date.today().isoformat()}.csv)")
            if len(res):
                print(f"               NOTE {len(res)} bar(s) still off >"
                      f"{RESIDUAL_TOL*100:.1f}% vs yfinance — pre-existing bad "
                      f"print(s), NOT caused by this repair:")
                for t, v in res.items():
                    print(f"                 {t.date()}  {v*100:.2f}%")
            else:
                print("               verified: every bar now matches yfinance")
        else:
            print(f"               would rescale by {d['factor']:.6f}")
        fixed.append(sym)

    print(f"\n{'Repaired' if apply else 'Would repair'}: "
          f"{', '.join(fixed) if fixed else 'none'}")
    if refused:
        print(f"Refused (non-uniform): {', '.join(refused)}")
    if fixed and apply:
        print("\nNext: python update_prices_kite.py "
              + " ".join(f"{s}.NS.csv" for s in fixed))


if __name__ == "__main__":
    main()
