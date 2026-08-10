"""
Deployment-gate report — paper periods vs the backtest distribution.

The deployment gate (memory paper-trading-loop) is: 3-6 months of forward
paper trading whose returns are CONSISTENT with the walk-forward / backtest
distribution before real capital. "Consistent" was a manual judgment; this
script makes it a number: each completed paper REBALANCE PERIOD is placed at
its percentile of the production engine's historical 21d-period return
distribution (full 2015-2026 — the same engine the paper trader mirrors).

PERIODS, NOT CALENDAR MONTHS (fixed 2026-08-10). This script used to group by
calendar month. That measured a different object than the reference
distribution: the paper book rotates on the LAST TUESDAY (2026-08-25 for
August), so a calendar-August window spans 07-31 -> 08-31 and its last ~4
sessions belong to the SEPTEMBER rotation's names. The backtest's reference
returns are single 21-session holding periods of ONE set of names
(backtest_portfolio steps a fixed HOLD-day grid). Scoring a calendar month
against that mixed two rotations into one number. Periods now run
rebalance-day -> rebalance-day, exactly one holding period each, which is what
the reference distribution measures. Same class of error as the July-2026
cash-month bug: a number scored against a distribution measuring something
slightly different.

EXPOSURE IS REPORTED, NOT CORRECTED FOR. REGIME_EXPOSURE caps deployed capital
at 0.375 in BEAR, so a BEAR period is STRUCTURALLY muted versus a pooled
all-regime reference — it can land in the bottom decile while behaving exactly
as designed. The `deployed` column (mean share of equity actually at risk) and
the regime column exist so that a low percentile can be read correctly instead
of triggering a false "the live path diverges" alarm. Deliberately NOT
converted into a regime-conditional reference distribution: slicing ~128
periods by regime leaves buckets too thin to be a real distribution, and it
would become a way to explain away every bad month.

Reading it:
  - periods between p10 and p90: normal — the paper book behaves like the
    backtest said it would.
  - a period below p5 (or two below p10): the live signal path may diverge
    from the backtest (data issue, execution gap, or regime the history
    never saw) — investigate BEFORE deploying, don't average it away.
    CHECK `deployed` AND `regime` FIRST: a low-exposure BEAR period landing
    low is expected, not evidence of divergence.
  - periods above p90 prove nothing good either — they're luck, not
    validation. Consistency, not outperformance, is what's being tested.

Run from scripts/ (monthly, or whenever):  python gate_report.py
  --attrib              per-period attribution: what drove the number
  --attrib PERIOD       attribution for one period label (e.g. 2026-08-25)
"""
import os
import sys

import numpy as np
import pandas as pd

import backtest_portfolio as bp
import core
import strategy_config as sc
from exit_engine import rebalance_day

EQUITY_PATH = "../data/paper_equity.csv"
LOG_PATH = "../data/paper_log.csv"
# A period with fewer logged sessions than this is partial — skip. A full
# rebalance period is ~21 NSE sessions; 15 allows for holidays and the odd
# missed pipeline run without admitting a half-formed period.
MIN_SESSIONS = 15


def _parse_dates(col):
    """Parse a date column tolerantly, then coerce whatever is left.

    `format="mixed"` matters: pandas otherwise infers ONE format from the
    first values and silently NaTs every row in a different shape. A log
    holding both "2026-08-07" and "2026-08-07 00:00:00" (a hand-edit, a
    recovery append, a different pandas version) would lose real sessions —
    dropping them from the gate rather than failing loudly. Verified: without
    it, 12 of 33 valid rows coerced to NaT in a mixed-format fixture.
    """
    parsed = pd.to_datetime(col, errors="coerce", format="mixed")
    if parsed.isna().all() and len(col):
        parsed = pd.to_datetime(col, errors="coerce")
    return parsed


def load_equity_log():
    """The paper equity log, date-hardened and de-duplicated.

    Date parsing is coerced explicitly rather than trusting parse_dates: a
    truncated/interrupted write can leave a malformed row whose date survives
    as a literal STRING in the index (CLAUDE.md documents this class of
    corruption for the price CSVs), and a mixed str/Timestamp index raises a
    TypeError on the first comparison instead of failing loudly at read time.
    """
    if not os.path.exists(EQUITY_PATH):
        return None
    df = pd.read_csv(EQUITY_PATH)
    if "date" not in df.columns:
        return None
    df["date"] = _parse_dates(df["date"])
    df = df.dropna(subset=["date"]).sort_values("date")
    return df.drop_duplicates(subset="date", keep="last").set_index("date")


def rebalance_boundaries(dates):
    """Rebalance-day boundaries covering `dates`, as a sorted DatetimeIndex.

    Uses the SAME definition as the live engine (exit_engine.rebalance_day —
    last Tuesday, rolled back over NSE holidays), so paper periods line up
    exactly with the days paper_trader actually rotated the book on.
    """
    idx = pd.DatetimeIndex(dates).normalize().sort_values()
    if len(idx) == 0:
        return pd.DatetimeIndex([])
    # Walk one month before the first session to one month after the last, so
    # the period CONTAINING the first session has its opening boundary.
    start = (idx[0] - pd.offsets.MonthBegin(1)).normalize()
    end = (idx[-1] + pd.offsets.MonthBegin(1)).normalize()
    months = pd.date_range(start, end, freq="MS")
    bounds = [rebalance_day(d.year, d.month, idx) for d in months]
    return pd.DatetimeIndex(sorted(set(bounds)))


def paper_periods():
    """Completed paper rebalance periods.

    Returns list of dicts with: label (the closing rebalance day), ret,
    n_sessions, invested (share of sessions holding >=1 position), deployed
    (mean share of equity actually at risk, i.e. 1 - cash/equity), regime,
    start/end dates.

    `invested` gates whether a period counts toward the gate at all: a period
    spent entirely in cash earns only the idle-cash yield, which is NOT a
    strategy return — scoring it against the backtest's EQUITY-return
    distribution is a category error (July 2026 read "+0.37%, 38th pctile,
    consistent" on a book that never bought a share).

    `deployed` does NOT gate anything — it is context for reading a
    percentile, because REGIME_EXPOSURE legitimately caps it well below 1.0.
    """
    if not os.path.exists(EQUITY_PATH):
        return None
    df = load_equity_log()
    if df is None or df.empty:
        return None

    eq = df["equity"]
    n_pos = df["n_pos"] if "n_pos" in df.columns else pd.Series(1, index=df.index)
    cash = df["cash"] if "cash" in df.columns else pd.Series(0.0, index=df.index)
    regime_col = df["regime"] if "regime" in df.columns else pd.Series("", index=df.index)

    bounds = rebalance_boundaries(eq.index)
    out = []
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        # Period = sessions AFTER the opening rebalance day through the
        # closing one. The opening day's close is the entry mark, so it is the
        # baseline, not a member of the period.
        grp = eq[(eq.index > lo) & (eq.index <= hi)]
        if len(grp) < MIN_SESSIONS:
            continue
        # Only score a CLOSED period: the closing rebalance day must actually
        # have been logged, otherwise the period is still open and its return
        # is a partial mark, not a result.
        if grp.index[-1] < hi:
            continue
        prev = eq.index[eq.index <= lo]
        start_val = eq.loc[prev[-1]] if len(prev) else grp.iloc[0]

        invested = float((n_pos.loc[grp.index] > 0).mean())
        eq_g = grp.replace(0, np.nan)
        deployed = float((1 - cash.loc[grp.index] / eq_g).clip(lower=0).mean())
        regs = [r for r in regime_col.loc[grp.index].tolist()
                if isinstance(r, str) and r]
        out.append({
            "label": str(hi.date()),
            "ret": grp.iloc[-1] / start_val - 1,
            "n_sessions": len(grp),
            "invested": invested,
            "deployed": deployed,
            "regime": regs[-1] if regs else "?",
            "start": prev[-1] if len(prev) else grp.index[0],
            "end": grp.index[-1],
        })
    return out


def open_period(periods):
    """The period currently in flight, as a progress note (never scored)."""
    df = load_equity_log()
    if df is None or df.empty:
        return None
    eq = df["equity"]
    bounds = rebalance_boundaries(eq.index)
    last_logged = eq.index[-1]
    # The open period is the one whose closing boundary is still ahead of the
    # last logged session.
    ahead = bounds[bounds > last_logged]
    if len(ahead) == 0:
        return None
    hi = ahead[0]
    prior = bounds[bounds < hi]
    if len(prior) == 0:
        return None
    lo = prior[-1]
    grp = eq[(eq.index > lo) & (eq.index <= last_logged)]
    if len(grp) == 0:
        return None
    prev = eq.index[eq.index <= lo]
    start_val = eq.loc[prev[-1]] if len(prev) else grp.iloc[0]
    return {
        "label": str(hi.date()),
        "closes_on": hi,
        "ret_so_far": grp.iloc[-1] / start_val - 1,
        "n_sessions": len(grp),
        "last": last_logged,
    }


# ---------- Attribution ----------

def attribution(period, matrix=None):
    """Why did this period score where it did?

    Decomposes the period return into: per-position contribution (entry mark
    -> exit mark, in rupees and as a share of opening equity) and the residual
    idle-cash yield. Pure accounting off paper_log.csv + paper_equity.csv —
    it computes no signal and feeds nothing back into scoring.
    """
    if not os.path.exists(LOG_PATH):
        return None
    fills = pd.read_csv(LOG_PATH)
    fills["date"] = _parse_dates(fills["date"])
    fills = fills.dropna(subset=["date"]).sort_values("date")
    eqdf = load_equity_log()
    if eqdf is None or eqdf.empty:
        return None

    start, end = period["start"], period["end"]
    open_eq = float(eqdf["equity"].loc[start])

    # Positions held during the period: bought on/before the period and not
    # yet sold within it. Reconstruct from the fill journal.
    held = {}
    for _, r in fills[fills["date"] <= end].iterrows():
        sym, act = r["symbol"], str(r["action"]).upper()
        if act == "BUY":
            p = held.setdefault(sym, {"qty": 0, "cost": 0.0, "entry": None})
            p["qty"] += int(r["qty"])
            p["cost"] += float(r["value"])
            if p["entry"] is None:
                p["entry"] = (r["date"], float(r["price"]))
        elif act == "SELL":
            p = held.get(sym)
            if p:
                p["qty"] -= int(r["qty"])
                if p["qty"] <= 0:
                    held.pop(sym, None)
    held = {s: p for s, p in held.items() if p["qty"] > 0}
    if not held:
        return {"open_equity": open_eq, "rows": [], "cash_yield": None}

    if matrix is None:
        matrix = bp.load_price_matrix()

    rows = []
    for sym, p in sorted(held.items()):
        col = sym if sym in matrix.columns else sym.replace(".NS", "")
        if col not in matrix.columns:
            continue
        series = matrix[col].dropna()
        # Mark at the period's start (or the entry fill, if bought mid-period)
        entry_date, entry_px = p["entry"]
        if entry_date > start:
            # Rotation fills land the session AFTER the rebalance day by
            # design (paper_trader queues at month-end, fills at the next
            # close), so an entry in the first few sessions is the normal
            # rotation, not a late mid-period addition worth flagging.
            sessions_in = int(((eqdf.index > start)
                               & (eqdf.index <= entry_date)).sum())
            mark_from, from_px = entry_date, entry_px
            note = "" if sessions_in <= 2 else f"entered +{sessions_in} sessions"
        else:
            prior = series.index[series.index <= start]
            if len(prior) == 0:
                continue
            mark_from, from_px, note = prior[-1], float(series.loc[prior[-1]]), ""
        upto = series.index[series.index <= end]
        if len(upto) == 0:
            continue
        mark_to, to_px = upto[-1], float(series.loc[upto[-1]])
        pnl = p["qty"] * (to_px - from_px)
        rows.append({
            "symbol": sym, "qty": p["qty"],
            "from_date": mark_from, "from_px": from_px,
            "to_date": mark_to, "to_px": to_px,
            "ret": to_px / from_px - 1,
            "pnl": pnl,
            "contrib": pnl / open_eq,
            "note": note,
        })
    rows.sort(key=lambda r: r["contrib"], reverse=True)
    equity_contrib = sum(r["contrib"] for r in rows)
    return {
        "open_equity": open_eq,
        "rows": rows,
        "equity_contrib": equity_contrib,
        "residual": period["ret"] - equity_contrib,
    }


def print_attribution(period, matrix=None):
    a = attribution(period, matrix)
    print(f"\n  ATTRIBUTION — period ending {period['label']} "
          f"({period['start'].date()} -> {period['end'].date()}, "
          f"{period['n_sessions']} sessions, regime {period['regime']})")
    if a is None:
        print("    no fill journal available")
        return
    print(f"    opening equity: ₹{a['open_equity']:,.0f}   "
          f"period return {period['ret']:+.2%}")
    if not a["rows"]:
        print("    no positions held — return is idle-cash yield only")
        return
    print(f"    {'symbol':<16s} {'qty':>5s} {'entry':>9s} {'exit':>9s} "
          f"{'ret':>8s} {'P&L':>12s} {'of book':>9s}")
    for r in a["rows"]:
        tag = f"  ({r['note']})" if r["note"] else ""
        print(f"    {r['symbol']:<16s} {r['qty']:5d} {r['from_px']:9.2f} "
              f"{r['to_px']:9.2f} {r['ret']:+8.2%} {r['pnl']:+12,.0f} "
              f"{r['contrib']:+8.2%}{tag}")
    print(f"    {'equity positions':<16s} {'':>5s} {'':>9s} {'':>9s} {'':>8s} "
          f"{'':>12s} {a['equity_contrib']:+8.2%}")
    print(f"    {'cash yield + res':<16s} {'':>5s} {'':>9s} {'':>9s} {'':>8s} "
          f"{'':>12s} {a['residual']:+8.2%}")
    dep = period["deployed"]
    print(f"    deployed {dep:.0%} of equity (regime {period['regime']}, "
          f"exposure cap {sc.REGIME_EXPOSURE.get(period['regime'], float('nan')):.3f}) — "
          f"the other {1-dep:.0%} sat in cash by design")


def main():
    args = sys.argv[1:]
    want_attrib = "--attrib" in args
    attrib_target = None
    if want_attrib:
        i = args.index("--attrib")
        if i + 1 < len(args) and not args[i + 1].startswith("-"):
            attrib_target = args[i + 1]

    periods = paper_periods()
    if periods is None:
        print("[gate] no paper equity log yet")
        return

    matrix = bp.load_price_matrix()
    index = bp.load_index()
    turnover = bp.load_turnover_matrix(matrix)
    eq = bp.run_backtest_gold_blend(matrix, index, turnover)
    ref = eq[1:] / eq[:-1] - 1   # historical 21d-period returns, production engine

    print(f"DEPLOYMENT GATE — paper rebalance periods vs backtest 21d-return "
          f"distribution (n={len(ref)} periods)")
    print(f"  reference: p5 {np.percentile(ref,5):+.2%}  p10 {np.percentile(ref,10):+.2%}  "
          f"median {np.percentile(ref,50):+.2%}  p90 {np.percentile(ref,90):+.2%}")
    print("  periods run rebalance-day -> rebalance-day (last Tuesday), matching")
    print("  the backtest's one-holding-period-per-return construction.")

    if not periods:
        print("\n  no COMPLETED rebalance period yet "
              f"(need >={MIN_SESSIONS} sessions and the closing rebalance day logged)")
    else:
        print(f"\n  {'period end':>12s} {'return':>8s} {'pctile':>7s} {'invested':>9s} "
              f"{'deployed':>9s} {'regime':>8s}  verdict")

    n_low = 0
    n_scored = 0
    MIN_INVESTED = 0.5
    scored_periods = []
    for p in periods:
        if p["invested"] < MIN_INVESTED:
            note = ("NOT SCORED — book in cash (idle yield only, not a strategy return)"
                    if p["invested"] == 0 else
                    f"NOT SCORED — invested only {p['invested']:.0%} of sessions")
            print(f"  {p['label']:>12s} {p['ret']:+8.2%} {'—':>7s} "
                  f"{p['invested']:8.0%} {p['deployed']:8.0%} {p['regime']:>8s}  {note}")
            continue
        n_scored += 1
        scored_periods.append(p)
        pct = (ref < p["ret"]).mean() * 100
        if pct < 5:
            verdict, n_low = "BELOW p5 — investigate before deploying", n_low + 1
        elif pct < 10:
            verdict, n_low = "below p10 — watch", n_low + 1
        elif pct > 90:
            verdict = "above p90 (luck, not validation)"
        else:
            verdict = "consistent"
        if pct < 10 and p["deployed"] < 0.6:
            verdict += f" [only {p['deployed']:.0%} deployed — check before alarming]"
        print(f"  {p['label']:>12s} {p['ret']:+8.2%} {pct:6.0f}% "
              f"{p['invested']:8.0%} {p['deployed']:8.0%} {p['regime']:>8s}  {verdict}")

    n_skipped = len(periods) - n_scored
    print(f"\n  {n_scored} SCORED period(s) — gate needs 3-6 consistent periods.")
    if n_skipped:
        print(f"  ({n_skipped} period(s) not scored: book was in cash — these do NOT")
        print("   count toward the gate, however good the number looks.)")
    if n_scored == 0:
        print("  GATE NOT STARTED: no period has a real invested return yet.")
    elif n_low >= 2:
        print("  WARNING: 2+ periods in the bottom decile — the live path may")
        print("  NOT match the backtest; find the divergence before real capital.")
        print("  First check the `deployed` column: low-exposure BEAR periods are")
        print("  structurally muted vs a pooled all-regime reference.")
    elif n_scored >= 3 and n_low == 0:
        print("  Gate progressing: no bottom-decile periods so far.")

    op = open_period(periods)
    if op:
        print(f"\n  IN FLIGHT: period ending {op['label']} — {op['n_sessions']} sessions "
              f"logged so far, {op['ret_so_far']:+.2%} to date (last {op['last'].date()}).")
        print(f"  Not scored until the {op['closes_on'].date()} rebalance day is logged.")

    if want_attrib:
        targets = scored_periods
        if attrib_target:
            targets = [p for p in periods if p["label"] == attrib_target]
            if not targets:
                print(f"\n  [attrib] no period labelled {attrib_target}; "
                      f"available: {[p['label'] for p in periods]}")
        elif not targets:
            targets = periods  # nothing scored yet — still show what there is
        for p in targets:
            print_attribution(p, matrix)


if __name__ == "__main__":
    main()
