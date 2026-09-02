"""
Gate A of the options-range-selling feasibility check — does the variance
risk premium (implied move overstating realised move) exist, measurably, on
NSE single-stock options?

Pre-registered BEFORE this script produced any output: PREREG_options_range_
selling.md. Sampling design, decision rule, and "what would not count as a
pass" are frozen there — do not add configs, change the panel, or change the
effect-size floor after seeing a result.

WHY THIS RUNS BEFORE ANY FORECASTING/BACKTEST INFRASTRUCTURE
--------------------------------------------------------------
Selling options only has an edge if the market's implied move systematically
overstates the move that actually happens. If that is false or too small to
survive real spreads, no amount of a better S/R band fixes it — the payoff
structure is negative-skew (small frequent wins, rare large losses), so
building strike-selection and backtest machinery on an untested mechanism
risks shipping something that looks fine until a single bad month erases
months of gains. Cheaper to check the mechanism first, same logic as the
2026-09-01 depth-feasibility gate.

METHOD
------
For each (symbol, expiry cycle):
  implied_move = (ATM_call_close + ATM_put_close) / underlying_at_D
  realised_move = abs(underlying_at_E / underlying_at_D - 1)
  VRP = implied_move - realised_move

"ATM" = the strike nearest the underlying price on D, among that cycle's
front-month contracts (min(Expiry) among the symbol's OPTSTK rows on D that
is > D — read from the data, never a hardcoded expiry-weekday rule).

UNDERLYING PRICE — see PREREG for the full reasoning. UDiFF era (>=2024-07-15)
uses NSE's own `Underlying` column directly. Legacy era uses that SAME date's
near-month FUTSTK settlement price — self-consistent within the unadjusted
NSE F&O archive, never joined against yfinance-adjusted price_data/.

Usage:
    python research_vrp_gate.py                 # full 5y sample, live fetch
    python research_vrp_gate.py --cache out.csv  # save/reuse the raw panel
"""
import argparse
import os
import sys
import time

import numpy as np
import pandas as pd

import download_fo_bhavcopy as fo
from sr_daily_logger import WATCHLIST

SAMPLE_START = pd.Timestamp("2021-09-01")
SAMPLE_END = pd.Timestamp("2026-08-31")
ATM_TOLERANCE = 0.03      # strike must be within 3% of underlying to count as ATM
EFFECT_FLOOR = 0.02        # 2 percentage points of underlying — the frozen floor
N_BOOT = 2000
BLOCK_LEN = 6              # unused for the date-cluster bootstrap (kept for parity/reference)
SEED = 42
REQUEST_DELAY = 0.4        # matches download_fo_bhavcopy.backfill's NSE-friendly pacing
PRICE_DIR = "../data/price_data/"
CORP_ACTION_TOL = 0.15    # same 15% threshold data_integrity_check.py uses to
                           # detect a rewritten history (held_close_snapshot)

_ADJ_CLOSE_CACHE = {}


def adjusted_close_on_or_before(symbol, date):
    """Nearest yfinance-ADJUSTED close at/before `date`, from price_data/.

    Found live 2026-09-02, first full Gate A run (see PREREG amendment 1):
    the NSE F&O archive's underlying price is NOT retroactively split/bonus-
    adjusted — a name that goes ex a split BETWEEN a cycle's decision date D
    and its expiry E shows a raw price level shift that has nothing to do
    with the actual return delivered. RELIANCE's real 1:1 bonus (ex ~2024-10,
    verified against this same price_data/ series) made the raw NSE archive
    read underlying_D=2995.95 / underlying_E=1332.10 — a fake ~55.5% "crash"
    computed from a real move of about -11%. This is the exact adjusted-vs-
    unadjusted trap already documented for the equity engine (fix_stale_bar.py,
    readjust_archive.py, corporate_action_watch.json), just previously unseen
    in the derivatives archive. Split/bonus-adjustment is exactly what
    price_data/ already does — reuse it rather than reimplementing detection.

    Only used for REALISED_MOVE (a return across two dates, where an
    in-between split matters). implied_move stays on the raw NSE underlying —
    it is a same-date ratio (straddle price vs spot on date D), so no split
    can occur inside it and raw is the right basis (it is also what a real
    seller would actually see quoted that day).
    """
    if symbol not in _ADJ_CLOSE_CACHE:
        path = os.path.join(PRICE_DIR, f"{symbol}.NS.csv")
        if not os.path.exists(path):
            _ADJ_CLOSE_CACHE[symbol] = None
        else:
            df = pd.read_csv(path, usecols=["Date", "Close"], parse_dates=["Date"])
            df = df.dropna(subset=["Date", "Close"]).sort_values("Date")
            _ADJ_CLOSE_CACHE[symbol] = df.set_index("Date")["Close"]
    s = _ADJ_CLOSE_CACHE[symbol]
    if s is None or s.empty:
        return None
    s = s[s.index <= date]
    if s.empty:
        return None
    return float(s.iloc[-1])


def underlying_price(raw_df, symbol):
    """Best-source-first underlying spot for one symbol on one bhavcopy date.

    UDiFF: NSE's own Underlying column (constant across a symbol's rows by
    construction, so any non-null value is used). Legacy: that date's
    near-month FUTSTK settlement price. Returns None if neither is available
    — a genuine data gap, not something to paper over with an assumption.
    """
    g = raw_df[raw_df["Symbol"] == symbol]
    if g.empty:
        return None
    u = g["Underlying"].dropna()
    if len(u):
        return float(u.iloc[0])
    fut = g[g["Instrument"] == "FUTSTK"].copy()
    if fut.empty:
        return None
    fut = fut.dropna(subset=["Expiry"])
    if fut.empty:
        return None
    fut["Expiry"] = pd.to_datetime(fut["Expiry"], errors="coerce")
    fut = fut.dropna(subset=["Expiry"]).sort_values("Expiry")
    if fut.empty:
        return None
    settle = fut.iloc[0]["Settle"]
    return float(settle) if pd.notna(settle) and settle > 0 else None


def front_month_options(raw_df, symbol, as_of):
    """This symbol's OPTSTK rows for the nearest expiry strictly after
    `as_of`, read from the data — never a hardcoded expiry-day rule."""
    g = raw_df[(raw_df["Symbol"] == symbol) & (raw_df["Instrument"] == "OPTSTK")].copy()
    if g.empty:
        return None, None
    g["Expiry"] = pd.to_datetime(g["Expiry"], errors="coerce")
    g = g.dropna(subset=["Expiry"])
    g = g[g["Expiry"] > as_of]
    if g.empty:
        return None, None
    expiry = g["Expiry"].min()
    return g[g["Expiry"] == expiry], expiry


def atm_straddle_price(chain, underlying):
    """Nearest-strike call+put close, if within ATM_TOLERANCE of underlying."""
    if chain is None or chain.empty or underlying is None or underlying <= 0:
        return None
    strikes = chain["Strike"].dropna().unique()
    if len(strikes) == 0:
        return None
    atm_strike = min(strikes, key=lambda k: abs(k - underlying))
    if abs(atm_strike - underlying) / underlying > ATM_TOLERANCE:
        return None
    row_c = chain[(chain["Strike"] == atm_strike) & (chain["OptionType"] == "CE")]
    row_p = chain[(chain["Strike"] == atm_strike) & (chain["OptionType"] == "PE")]
    if row_c.empty or row_p.empty:
        return None

    def px(row):
        c = row["Close"].iloc[0]
        s = row["Settle"].iloc[0]
        # Legacy CLOSE is 0.0 for untraded strikes — SETTLE_PR is NSE's
        # theoretical/settlement price, and what actually prices a position.
        # Prefer Close when it's real (a real traded print beats a mark),
        # fall back to Settle otherwise. Never average the two.
        if pd.notna(c) and c > 0:
            return float(c)
        if pd.notna(s) and s > 0:
            return float(s)
        return None

    pc, pp = px(row_c), px(row_p)
    if pc is None or pp is None:
        return None
    return pc + pp


def monthly_cycle_dates(start, end):
    """Business-day sample dates, ~one per calendar month, used only to seed
    `download_fo_date` calls that then read the REAL expiry off the data."""
    return pd.bdate_range(start, end, freq="BMS")   # business-month-start


def build_panel(symbols, start, end, cache_path=None):
    if cache_path and os.path.exists(cache_path):
        print(f"Loading cached panel from {cache_path}")
        return pd.read_csv(cache_path, parse_dates=["decision_date", "expiry_date"])

    fo.init_nse_session()
    rows, excluded = [], {"no_options_on_D": 0, "no_atm_strike": 0,
                           "no_underlying_D": 0, "no_expiry_bhavcopy": 0,
                           "no_underlying_E": 0, "fetch_failed": 0,
                           "no_adjusted_close": 0}

    seed_dates = monthly_cycle_dates(start, end)
    # Walk forward: after processing one cycle for a symbol, the next
    # decision date is that cycle's own expiry (mirrors a real seller
    # rolling into the next cycle right after the previous one settles).
    next_decision = {s: seed_dates[0] for s in symbols}
    cache_raw = {}   # date -> raw bhavcopy df, avoid re-fetching the same date

    def get_raw(d):
        key = d.strftime("%Y-%m-%d")
        if key in cache_raw:
            return cache_raw[key]
        try:
            raw = fo.download_fo_date(d)
        except Exception:
            raw = None
        cache_raw[key] = raw
        time.sleep(REQUEST_DELAY)
        return raw

    for sym in symbols:
        base = sym.replace(".NS", "")
        while next_decision[sym] <= end:
            D = next_decision[sym]
            while D.weekday() >= 5:
                D += pd.Timedelta(days=1)
            raw_D = get_raw(D)
            if raw_D is None:
                excluded["fetch_failed"] += 1
                next_decision[sym] = D + pd.Timedelta(days=28)
                continue
            chain, E = front_month_options(raw_D, base, D)
            if chain is None:
                excluded["no_options_on_D"] += 1
                next_decision[sym] = D + pd.Timedelta(days=28)
                continue
            u_D = underlying_price(raw_D, base)
            if u_D is None:
                excluded["no_underlying_D"] += 1
                next_decision[sym] = E
                continue
            straddle = atm_straddle_price(chain, u_D)
            if straddle is None:
                excluded["no_atm_strike"] += 1
                next_decision[sym] = E
                continue

            E_bd = E
            while E_bd.weekday() >= 5:
                E_bd += pd.Timedelta(days=1)
            raw_E = get_raw(E_bd)
            if raw_E is None:
                # try the next business day once (settlement can print T+1
                # on the archive around some holiday-shifted expiries)
                raw_E = get_raw(E_bd + pd.Timedelta(days=1))
            if raw_E is None:
                excluded["no_expiry_bhavcopy"] += 1
                next_decision[sym] = E
                continue
            u_E = underlying_price(raw_E, base)
            if u_E is None:
                excluded["no_underlying_E"] += 1
                next_decision[sym] = E
                continue

            implied = straddle / u_D
            raw_realised = abs(u_E / u_D - 1.0)

            # Split/bonus-adjusted realised move — see adjusted_close_on_or_
            # before's docstring. Falls back to the raw NSE-archive move if
            # price_data/ has no history for this symbol (should be rare on
            # the WATCHLIST panel, but excluded rather than silently
            # defaulting to the unadjusted number that caused the bug).
            adj_D = adjusted_close_on_or_before(base, D)
            adj_E = adjusted_close_on_or_before(base, E)
            if adj_D is None or adj_E is None or adj_D <= 0:
                excluded["no_adjusted_close"] = excluded.get("no_adjusted_close", 0) + 1
                next_decision[sym] = E
                continue
            realised = abs(adj_E / adj_D - 1.0)
            corp_action = abs(raw_realised - realised) > CORP_ACTION_TOL

            rows.append(dict(symbol=base, decision_date=D, expiry_date=E,
                             underlying_D=u_D, underlying_E=u_E,
                             implied_move=implied, realised_move=realised,
                             raw_realised_move=raw_realised,
                             corp_action_in_window=corp_action,
                             vrp=implied - realised, source_D="udiff" if D >= fo.LEGACY_LAST_DATE else "legacy"))
            next_decision[sym] = E

        print(f"  {sym}: done ({sum(1 for r in rows if r['symbol']==base)} cycles)")

    panel = pd.DataFrame(rows)
    print(f"\nPanel built: {len(panel)} (symbol, cycle) observations")
    print("Exclusions:", excluded)
    if cache_path and len(panel):
        panel.to_csv(cache_path, index=False)
        print(f"Cached to {cache_path}")
    return panel


def date_clustered_bootstrap(values, dates, n_boot=N_BOOT, seed=SEED):
    """95% CI on the median, resampling EXPIRY-DATE groups, not rows — the
    same lesson as sr_monthend_analysis.clustered_ci: cycles sharing an
    expiry date share the market's move that month, so treating rows as
    independent understates the interval."""
    df = pd.DataFrame({"v": values, "d": dates})
    groups = [g["v"].to_numpy() for _, g in df.groupby("d")]
    n = len(groups)
    if n < 2:
        return float("nan"), float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    meds = np.empty(n_boot)
    for i in range(n_boot):
        pick = rng.integers(0, n, n)
        meds[i] = np.median(np.concatenate([groups[j] for j in pick]))
    return float(np.median(values)), float(np.percentile(meds, 2.5)), float(np.percentile(meds, 97.5))


def report(panel):
    if panel.empty:
        print("No observations — cannot evaluate Gate A.")
        return

    print(f"\n{'='*74}\nGATE A RESULT\n{'='*74}")
    print(f"observations: {len(panel)} | symbols: {panel.symbol.nunique()} | "
          f"expiry dates: {panel.expiry_date.nunique()}")
    if "corp_action_in_window" in panel.columns:
        n_ca = int(panel.corp_action_in_window.sum())
        print(f"corporate-action-in-window flags: {n_ca}/{len(panel)} "
              f"(raw NSE archive vs price_data/ realised move disagree by "
              f">{CORP_ACTION_TOL*100:.0f}pp — realised_move below is the "
              f"ADJUSTED figure, already corrected for these)")

    med, lo, hi = date_clustered_bootstrap(panel.vrp.values, panel.expiry_date.values)
    print(f"\nmedian VRP: {med*100:.2f}pp   95% CI [{lo*100:.2f}pp, {hi*100:.2f}pp]  "
          f"(bootstrap clustered by expiry date, n_boot={N_BOOT})")
    passes_ci = lo > 0
    passes_floor = med > EFFECT_FLOOR
    print(f"  CI excludes zero (positive side): {'PASS' if passes_ci else 'FAIL'}")
    print(f"  median exceeds {EFFECT_FLOOR*100:.0f}pp floor:        "
          f"{'PASS' if passes_floor else 'FAIL'}")

    q = panel.vrp.quantile([.05, .25, .5, .75, .95])
    print(f"\nfull distribution (pp): p5 {q[.05]*100:.2f}  p25 {q[.25]*100:.2f}  "
          f"p50 {q[.5]*100:.2f}  p75 {q[.75]*100:.2f}  p95 {q[.95]*100:.2f}")
    worst = panel.nsmallest(3, "vrp")[["symbol", "decision_date", "expiry_date", "vrp"]]
    print("\nworst 3 cycles (most negative VRP — where a seller would have hurt most):")
    print(worst.to_string(index=False))

    print(f"\n{'-'*74}\nby expiry date (concentration check — is the effect a handful of dates?)\n{'-'*74}")
    by_date = panel.groupby("expiry_date").vrp.agg(["median", "count"])
    print(by_date.describe().to_string())
    frac_positive_dates = (by_date["median"] > 0).mean()
    print(f"\nshare of expiry dates with positive median VRP: {frac_positive_dates*100:.1f}%")

    print(f"\n{'-'*74}\nUDiFF-only check (direct Underlying column, no futures-basis proxy)\n{'-'*74}")
    udiff = panel[panel.source_D == "udiff"]
    if len(udiff) >= 30:
        med_u, lo_u, hi_u = date_clustered_bootstrap(udiff.vrp.values, udiff.expiry_date.values)
        print(f"n={len(udiff)}  median {med_u*100:.2f}pp  CI [{lo_u*100:.2f}, {hi_u*100:.2f}]")
        agree = abs(med_u - med) < (hi - lo)
        print(f"agrees with full-sample result within the full-sample CI width: "
              f"{'YES' if agree else 'NO — legacy futures-basis proxy is suspect'}")
    else:
        print(f"n={len(udiff)} — too few UDiFF-era cycles yet to check separately "
              f"(more will accumulate as the sample window advances).")

    print(f"\n{'='*74}")
    if passes_ci and passes_floor:
        print("GATE A: PASSES its frozen bar. Gross VRP edge measured; still not")
        print("netted against real spreads (Gate B) or margin capacity (Gate C).")
        print("Per the PREREG, this alone is NOT a green light to trade.")
    else:
        print("GATE A: DOES NOT PASS its frozen bar as measured. Per the plan,")
        print("this can independently end the project — do not lower the bar")
        print("post-hoc to make it pass.")
    print(f"{'='*74}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="../data/_research/vrp_gate_panel.csv")
    ap.add_argument("--symbols", type=int, default=None,
                    help="limit to first N watchlist symbols (fast smoke test)")
    args = ap.parse_args()

    symbols = WATCHLIST[:args.symbols] if args.symbols else WATCHLIST
    panel = build_panel(symbols, SAMPLE_START, SAMPLE_END, cache_path=args.cache)
    report(panel)


if __name__ == "__main__":
    main()
