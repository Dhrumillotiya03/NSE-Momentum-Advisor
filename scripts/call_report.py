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

No look-ahead: only data after the call's date is used. RCOM-class dead names
simply stop producing bars and show as OPEN/EXPIRED.

TWO MEASUREMENT DEFECTS FIXED 2026-09-01 — the aggregation was wrong in the
same two ways this repo has been bitten by before, and together they made a
month of perfectly ordinary calls read as a losing system ("TARGET-before-STOP
21%, avg R -0.30"):

  1. THE STATISTICAL UNIT WAS THE CALL, NOT THE SYMBOL. full_advisor logs its
     top ~8 names EVERY session, so 145 'calls' were 21 symbols re-observed
     in one shared tape. 15 of the 19 stops behind that 21% were two names
     (ADANIENSOL x9, LLOYDSME x6) re-logged daily as one deteriorating
     position drifted down — effective n was 6 symbols, not 24 calls. Same
     error as counting S/R log rows instead of dates (memory
     sr-touch-table-distance-calibration-2026-08), and the same shape as the
     exit-flow study's "3 raw events driving 29/36 window wins".
  2. THE RACE WAS SCORED WHILE CENSORED. HORIZON is 42 sessions, but no call
     had more than 20 sessions of forward data — so ZERO races could complete
     and every 'resolved' row had resolved EARLY. That is a self-selected
     subset, the mirror image of the hit/miss resolution asymmetry that
     produced the fake 100% S/R hit rate (memory sr-measurement-100pct-bug).
     The race is now simply NOT REPORTED until calls have the full window.

Also added: THE PICK, SEPARATED FROM THE LEVEL. The target/stop race scores
full_advisor's ATR entry/stop/target geometry, which has never been walk-
forward validated. Holding from the call-day close and benchmarking against
Nifty over the identical span scores only whether the NAMES were right — a
different question, and the one the momentum engine actually answers.

Run from scripts/:  python call_report.py  [--horizon N] [--by-call]
"""
import argparse
import os

import numpy as np
import pandas as pd

CALLS_LOG = "../data/advisor_calls_log.csv"
INDEX_CSV = "../data/index_data/nifty50.csv"
FILL_WINDOW = 10       # trading days to wait for the buy-at level
HORIZON = 42           # trading days from fill for the target/stop race
MARK_DAYS = 21
N_BOOT = 20000
SEED = 0

# full_advisor.py was REBUILT 2026-08-01 (see memory advisor-strategy-
# divergence-2026-08): before this date, entries were priced AT nearest
# support with rr measured against it — structurally anti-momentum, produced
# unfillable limits, and had ZERO overlap with the validated strategy's own
# picks. Calls logged before the rebuild are NOT comparable to calls logged
# after it; pooling them into one hit-rate silently averages a broken
# construction into a fixed one. Segment on this date, always.
ADVISOR_REBUILD_DATE = pd.Timestamp("2026-08-01")


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


def load_index():
    df = pd.read_csv(INDEX_CSV, low_memory=False)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    return df.dropna(subset=["Date", "Close"]).sort_values("Date").set_index("Date")["Close"]


def cluster_bootstrap(unit_means, n_boot=N_BOOT, seed=SEED):
    """Resample UNITS (symbols), not rows.

    The ledger logs the same ~8 names every session, so 145 'calls' are 21
    symbols re-observed. Treating rows as independent understates the interval
    by roughly sqrt(rows per symbol) — the identical error diagnosed for the
    S/R panel in memory sr-touch-table-distance-calibration-2026-08, where 61
    symbols on one session share the market's move.
    """
    v = np.asarray(unit_means, dtype=float)
    v = v[~np.isnan(v)]
    if len(v) < 2:
        return np.nan, np.nan, np.nan
    rng = np.random.default_rng(seed)
    means = v[rng.integers(0, len(v), size=(n_boot, len(v)))].mean(axis=1)
    return np.percentile(means, 2.5), np.percentile(means, 97.5), float((means > 0).mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=HORIZON,
                    help=f"trading days from fill for target/stop race (default {HORIZON})")
    ap.add_argument("--by-call", action="store_true",
                    help="also print the raw per-CALL aggregates (inflated by "
                         "repeat logging of the same names — read the per-symbol "
                         "numbers instead)")
    args = ap.parse_args()

    if not os.path.exists(CALLS_LOG):
        print(f"no ledger yet ({CALLS_LOG}) — run full_advisor.py first")
        return
    calls = pd.read_csv(CALLS_LOG)
    if calls.empty:
        print("ledger is empty")
        return
    calls["date"] = pd.to_datetime(calls["date"], errors="coerce")

    idx = load_index()
    scored = []
    for _, call in calls.iterrows():
        ohlc = load_ohlc(call["symbol"])
        if ohlc is None:
            continue
        after = ohlc[ohlc.index > call["date"]]
        # NOTE the derived keys are deliberately NOT named "alpha"/"bench":
        # the ledger ALREADY has an `alpha` column (the momentum alpha at call
        # time). Writing into it only on the branch below left calls with zero
        # forward bars silently carrying the LEDGER's alpha (a score like 7.15)
        # into a return aggregate, reading as +715%. Same silent-schema-
        # collision family as memory advisor-ledger-header-drift-2026-08.
        row = {**call.to_dict(), **score_call(call, ohlc, args.horizon),
               "bars_available": len(after),
               "hold_ret": np.nan, "bench_ret": np.nan, "pick_alpha": np.nan}
        # Fill-model-free mark to the LATEST available bar, benchmarked against
        # the index over the SAME span. Unlike mark_21d this is always
        # computable, and unlike the target/stop race it does not depend on the
        # never-validated ATR geometry — so it answers "was the PICK any good"
        # separately from "was the LEVEL any good".
        if len(after):
            row["hold_ret"] = after["Close"].iloc[-1] / call["price"] - 1
            ix_after = idx[idx.index > call["date"]]
            ix_before = idx[idx.index <= call["date"]]
            if len(ix_after) and len(ix_before):
                row["bench_ret"] = ix_after.iloc[-1] / ix_before.iloc[-1] - 1
                row["pick_alpha"] = row["hold_ret"] - row["bench_ret"]
        scored.append(row)
    df = pd.DataFrame(scored)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in ("hold_ret", "bench_ret", "pick_alpha"):
        if c not in df.columns:
            df[c] = np.nan

    pre = df[df["date"] < ADVISOR_REBUILD_DATE]
    if len(pre):
        print(f"NOTE: {len(pre)} call(s) logged before the {ADVISOR_REBUILD_DATE.date()} "
              f"full_advisor.py rebuild (support-priced entries, structurally anti-"
              f"momentum, zero overlap with the validated strategy). EXCLUDED from "
              f"the report below — not comparable to post-rebuild calls. See memory "
              f"advisor-strategy-divergence-2026-08.")
        df = df[df["date"] >= ADVISOR_REBUILD_DATE]
        if df.empty:
            print("\nno post-rebuild calls yet.")
            return

    print("==============================")
    print("📋 ADVISORY-CALL REPORT")
    print("==============================")
    n_sym = df["symbol"].nunique()
    per_sym = df.groupby("symbol").size()
    print(f"calls logged: {len(df)}  |  span: {df['date'].min().date()} → {df['date'].max().date()}")
    print(f"distinct symbols: {n_sym}  (max {per_sym.max()} calls on one name, "
          f"median {per_sym.median():.0f})")
    if "in_strategy_top_n" in df.columns:
        top_n_calls = df["in_strategy_top_n"].astype(str).str.lower().isin(["true", "1"])
        print(f"  of which in the strategy's own regime top-N: {top_n_calls.sum()}/{len(df)}")
    if len(df) > n_sym * 1.5:
        print(f"\n  ** THE STATISTICAL UNIT IS THE SYMBOL, NOT THE CALL. ** The advisor "
              f"re-logs\n  the same names every session, so these {len(df)} rows are "
              f"{n_sym} symbols re-observed\n  in one shared tape. Every headline below is "
              f"therefore reported per SYMBOL;\n  per-call figures are available with "
              f"--by-call and should not be quoted.")

    filled = df[df["fill"] == "FILLED"]
    print(f"\nfill rate (Low touched buy-at within {FILL_WINDOW}d): "
          f"{len(filled)}/{len(df)} ({len(filled)/len(df):.0%})")

    # ---- target/stop race, with the censoring stated up front ----
    # A call resolves only when target or stop is TOUCHED. Until every call has
    # had the FULL horizon to do that, "resolved" is a self-selected subset of
    # the fastest-moving names, and its hit rate is not the hit rate. This is
    # the same hit/miss resolution asymmetry that produced the fake 100% S/R
    # number in 2026-07 (memory sr-measurement-100pct-bug), mirrored.
    complete = filled[filled["bars_available"] >= args.horizon]
    print(f"\nTARGET/STOP RACE (horizon {args.horizon}d from fill)")
    print(f"  calls with the full {args.horizon}d window available: "
          f"{len(complete)}/{len(filled)}")
    if len(complete) == 0:
        oldest = int(filled["bars_available"].max()) if len(filled) else 0
        print(f"  -> NOT REPORTED. The oldest filled call has only {oldest} sessions of "
              f"forward data.\n     Any hit rate computed now would be built purely from "
              f"calls that resolved\n     EARLY, which is a biased subset, not a result. "
              f"Re-run once calls age past\n     {args.horizon} sessions.")
        counts = filled["outcome"].value_counts().to_dict()
        print(f"     (interim, DO NOT QUOTE: {counts})")
    else:
        dec = complete[complete["outcome"].isin(["TARGET", "STOP", "AMBIGUOUS"])]
        by_sym = dec.groupby("symbol")["outcome"].apply(lambda g: (g == "TARGET").mean())
        tgt = int((dec["outcome"] == "TARGET").sum())
        print(f"  decided: {len(dec)} calls over {dec['symbol'].nunique()} symbols")
        print(f"  TARGET-before-STOP, per SYMBOL: {by_sym.mean():.0%} "
              f"(n={len(by_sym)} symbols)")
        if args.by_call and len(dec):
            print(f"  [--by-call] per call: {tgt}/{len(dec)} ({tgt/len(dec):.0%})")
        rm = complete["r_multiple"].dropna()
        if len(rm):
            per_sym_r = complete.groupby("symbol")["r_multiple"].mean().dropna()
            lo, hi, p = cluster_bootstrap(per_sym_r)
            print(f"  avg R multiple, per SYMBOL: {per_sym_r.mean():+.2f}  "
                  f"95% CI [{lo:+.2f}, {hi:+.2f}]")
    open_n = int((filled["outcome"] == "OPEN").sum())
    if open_n:
        print(f"  still open: {open_n}")

    # ---- the pick, separated from the level ----
    # The target/stop race scores full_advisor's ATR entry/stop/target geometry,
    # which has never been walk-forward validated (CLAUDE.md flags it as needing
    # its own study). This block scores only the PICK: hold from the call-day
    # close, benchmarked against Nifty over the identical span.
    a = df.dropna(subset=["pick_alpha"])
    if len(a):
        sym_alpha = a.groupby("symbol")["pick_alpha"].mean()
        sym_ret = a.groupby("symbol")["hold_ret"].mean()
        lo, hi, p = cluster_bootstrap(sym_alpha)
        print(f"\nTHE PICK, SEPARATED FROM THE LEVEL "
              f"(hold call-close → latest bar, vs Nifty over the same span)")
        print(f"  per SYMBOL (n={len(sym_alpha)}): mean return {sym_ret.mean():+.2%}, "
              f"mean alpha {sym_alpha.mean():+.2%}")
        print(f"    95% CI [{lo:+.2%}, {hi:+.2%}]  P(alpha>0)={p:.1%}  "
              f"{int((sym_alpha > 0).sum())}/{len(sym_alpha)} symbols positive")
        if not (lo > 0 or hi < 0):
            print(f"    NOT SIGNIFICANT — the interval spans zero.")
        if args.by_call:
            print(f"  [--by-call] per call (n={len(a)}): return {a['hold_ret'].mean():+.2%}, "
                  f"alpha {a['pick_alpha'].mean():+.2%}  <- narrower by construction, do not quote")
        print(f"  worst 3 symbols: " + ", ".join(
            f"{s} {v:+.1%}" for s, v in sym_alpha.nsmallest(3).items()))
        print(f"  best 3 symbols:  " + ", ".join(
            f"{s} {v:+.1%}" for s, v in sym_alpha.nlargest(3).items()))
        print(f"  NOTE this is a variable, still-open horizon — it says whether the "
              f"NAMES were\n  right, not whether the entry/stop/target levels were.")

    marks = df["mark_21d"].dropna()
    if len(marks):
        sym_mark = df.dropna(subset=["mark_21d"]).groupby("symbol")["mark_21d"].mean()
        print(f"\n21d mark from call-day close: {len(marks)} calls / {len(sym_mark)} symbols, "
              f"per-symbol mean {sym_mark.mean():+.2%}, "
              f"{int((sym_mark > 0).sum())}/{len(sym_mark)} symbols positive")

    if df["regime"].nunique() > 1:
        print("\nby regime (per-symbol alpha):")
        for reg, g in a.groupby("regime"):
            gs = g.groupby("symbol")["pick_alpha"].mean()
            print(f"  {reg}: {len(g)} calls / {len(gs)} symbols, alpha {gs.mean():+.2%}, "
                  f"{int((gs > 0).sum())}/{len(gs)} positive")
    else:
        print(f"\nONE REGIME ONLY ({df['regime'].dropna().unique().tolist()}) — nothing "
              f"here generalises to the other regimes.")

    n_pending = len(df) - len(marks)
    if n_pending:
        print(f"\n({n_pending} call(s) too recent for a 21d mark — re-run as data accrues)")


if __name__ == "__main__":
    main()
