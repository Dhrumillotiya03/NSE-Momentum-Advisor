"""
Advisory-call hit-rate report — scores every call in data/advisor_calls_log.csv
(written by full_advisor.py, nightly + manual runs) against what the price
actually did afterwards. This is the Univest-style honesty loop: the calls
carry entry/target/stop, so their quality is measurable, per call, forever.

Evaluation model (per call, using daily High/Low from the call's data date):
  FILL:    the call says "buy at" support — filled on the first later day
           whose Low <= buy_at (within FILL_WINDOW trading days). No fill ->
           the call is scored separately (a runaway that never came back).
  OUTCOME: from the fill day, first touch wins: High >= target -> TARGET,
           Low <= stop -> STOP, within HORIZON trading days of the fill.
           Both touched the SAME day -> counted AMBIGUOUS (conservatively
           grouped with STOP in the headline hit rate). Neither -> OPEN
           (still running) or EXPIRED (horizon passed; scored by close).
  Plus a fill-model-free mark: return from call-day close after 21 trading
           days (comparable to the momentum engine's hold period).

Aggregates: overall + by regime + by fill status; average R multiple
(realized move / initial risk). No look-ahead: only data after the call's
date is used. RCOM-class dead names simply stop producing bars and show as
OPEN/EXPIRED. Run from scripts/:  python call_report.py  [--horizon N]
"""
import argparse
import os

import numpy as np
import pandas as pd

CALLS_LOG = "../data/advisor_calls_log.csv"
FILL_WINDOW = 10       # trading days to wait for the buy-at level
HORIZON = 42           # trading days from fill for the target/stop race
MARK_DAYS = 21


def load_ohlc(sym):
    for d in ("../data/price_data", "../data/etf_data"):
        path = os.path.join(d, f"{sym}.csv")
        if os.path.exists(path):
            df = pd.read_csv(path, low_memory=False)
            df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
            for c in ("High", "Low", "Close"):
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df = df[df["Date"].notna()].dropna(subset=["High", "Low", "Close"])
            return df.sort_values("Date").set_index("Date")
    return None


def score_call(call, ohlc, horizon):
    """Returns dict with fill/outcome/returns for one call row."""
    after = ohlc[ohlc.index > call["date"]]
    res = {"fill": "NO_FILL", "outcome": "-", "days_to_outcome": np.nan,
           "r_multiple": np.nan, "mark_21d": np.nan}
    if len(after) == 0:
        res["fill"] = "NO_DATA"
        return res

    # fill-model-free 21d mark from call-day close
    if len(after) >= MARK_DAYS:
        res["mark_21d"] = after["Close"].iloc[MARK_DAYS - 1] / call["price"] - 1

    fill_win = after.iloc[:FILL_WINDOW]
    hit = fill_win[fill_win["Low"] <= call["buy_at"]]
    if len(hit) == 0:
        return res
    res["fill"] = "FILLED"
    fill_idx = after.index.get_loc(hit.index[0])
    race = after.iloc[fill_idx:fill_idx + horizon]

    risk = call["buy_at"] - call["stop"]
    for k, (_, day) in enumerate(race.iterrows()):
        tgt = day["High"] >= call["target"]
        stp = day["Low"] <= call["stop"]
        if tgt and stp:
            res.update(outcome="AMBIGUOUS", days_to_outcome=k,
                       r_multiple=np.nan)
            return res
        if stp:
            res.update(outcome="STOP", days_to_outcome=k,
                       r_multiple=-1.0)
            return res
        if tgt:
            res.update(outcome="TARGET", days_to_outcome=k,
                       r_multiple=(call["target"] - call["buy_at"]) / risk if risk > 0 else np.nan)
            return res

    last_close = race["Close"].iloc[-1]
    r = (last_close - call["buy_at"]) / risk if risk > 0 else np.nan
    if len(race) < horizon:
        res.update(outcome="OPEN", r_multiple=r)
    else:
        res.update(outcome="EXPIRED", days_to_outcome=len(race) - 1, r_multiple=r)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=HORIZON,
                    help=f"trading days from fill for target/stop race (default {HORIZON})")
    args = ap.parse_args()

    if not os.path.exists(CALLS_LOG):
        print(f"no ledger yet ({CALLS_LOG}) — run full_advisor.py first")
        return
    calls = pd.read_csv(CALLS_LOG)
    if calls.empty:
        print("ledger is empty")
        return

    scored = []
    for _, call in calls.iterrows():
        ohlc = load_ohlc(call["symbol"])
        if ohlc is None:
            continue
        scored.append({**call.to_dict(), **score_call(call, ohlc, args.horizon)})
    df = pd.DataFrame(scored)

    print("==============================")
    print("📋 ADVISORY-CALL REPORT")
    print("==============================")
    print(f"calls logged: {len(df)}  |  span: {df['date'].min()} → {df['date'].max()}")

    filled = df[df["fill"] == "FILLED"]
    print(f"\nfill rate (Low touched buy-at within {FILL_WINDOW}d): "
          f"{len(filled)}/{len(df)} ({len(filled)/len(df):.0%})")

    resolved = filled[filled["outcome"].isin(["TARGET", "STOP", "AMBIGUOUS", "EXPIRED"])]
    if len(resolved):
        tgt = (resolved["outcome"] == "TARGET").sum()
        stp = resolved["outcome"].isin(["STOP", "AMBIGUOUS"]).sum()
        exp = (resolved["outcome"] == "EXPIRED").sum()
        decided = tgt + stp
        print(f"resolved: {len(resolved)}  (target {tgt}, stop/ambig {stp}, expired {exp})")
        if decided:
            print(f"TARGET-before-STOP hit rate: {tgt}/{decided} ({tgt/decided:.0%})")
        rm = resolved["r_multiple"].dropna()
        if len(rm):
            print(f"avg R multiple (incl. expired-at-mark): {rm.mean():+.2f}")
    open_n = (filled["outcome"] == "OPEN").sum()
    if open_n:
        print(f"still open: {open_n}")

    marks = df["mark_21d"].dropna()
    if len(marks):
        print(f"\n21d mark from call-day close (fill-model-free): "
              f"n={len(marks)}  mean {marks.mean():+.2%}  median {np.median(marks):+.2%}  "
              f"win rate {(marks > 0).mean():.0%}")

    if df["regime"].nunique() > 1:
        print("\nby regime:")
        for reg, g in df.groupby("regime"):
            gm = g["mark_21d"].dropna()
            line = f"  {reg}: {len(g)} calls"
            if len(gm):
                line += f", 21d mean {gm.mean():+.2%}, win {(gm > 0).mean():.0%}"
            print(line)

    n_pending = len(df) - len(marks)
    if n_pending:
        print(f"\n({n_pending} call(s) too recent for a 21d mark — re-run as data accrues)")


if __name__ == "__main__":
    main()
