"""
Live-vs-backtest divergence check — is the paper book running the strategy
the backtest validated?

THE QUESTION THIS ANSWERS. gate_report.py scores the paper book's return
against the backtest's distribution. If a period lands in the bottom decile,
that number alone cannot distinguish:
  (a) the strategy is fine, the market was unkind  — expected, keep going
  (b) the LIVE code path selects/sizes differently from the BACKTEST path
      — a bug, and no amount of further paper trading will reveal it
This script isolates (b) by running both selection paths over the SAME dates
and diffing them, so a bad gate month can be attributed instead of guessed at.

WHY THIS IS THE RIGHT THING TO CHECK. This repo has hit live/backtest drift
repeatedly and each time it was invisible in returns until someone looked:
  - exit_engine's live top-N was missing the 2-per-sector cap the backtest
    enforced (found 2026-07-12 building the paper trader)
  - momentum_score existed as TWO copies that had silently drifted, so live
    ranked BUYs by a different score than the validated one (2026-07-17)
  - inverse-vol sizing existed as THREE hand-inlined copies when conviction
    sizing was adopted (2026-08-05)
The formulas are shared today (paper_trader imports scan_universe/
market_regime from core and select_top_n_capped/conviction_weights from
backtest_portfolio), so the remaining risk is not the MATH but the INPUTS:
the live path reads per-symbol CSVs through core.load_stock, the backtest
reads a merged matrix through load_price_matrix. Those can disagree — a
stale CSV, a partial candle, a name present in one and absent from the other
— and that disagreement IS the drift this checks for.

WHAT IT DOES NOT DO. It does not test whether the strategy makes money, and
it is not a backtest. Agreement here means the live path faithfully
implements the validated strategy; it says nothing about whether that
strategy has an edge. That is what the gate is for. Nothing here writes to
any book, state file or log.

Run from scripts/:
    python divergence_check.py              # selection agreement, recent dates
    python divergence_check.py --dates 20   # over the last N rebalance-eligible dates
    python divergence_check.py --sizing     # also diff conviction weights
"""
import sys

import numpy as np
import pandas as pd

import backtest_portfolio as bp
import core
import strategy_config as sc


def stale_symbols(matrix):
    """{symbol: (csv_last_date, sessions_behind)} for names whose per-symbol
    CSV ends before the matrix does.

    These are the names where load_price_matrix's ffill(limit=5) is inventing
    a flat tail: the backtest path sees a forward-filled price, the live path
    sees the real (shorter) series. That difference is a DATA condition, not
    a code-path divergence, and must be reported separately or every stale
    CSV looks like a strategy bug.
    """
    out = {}
    last = matrix.index[-1]
    for s in matrix.columns:
        df = core.load_stock(s)
        if df is None or len(df) == 0:
            continue
        d = df.index[-1]
        if d < last:
            out[s] = (d.date(), int(((matrix.index > d) & (matrix.index <= last)).sum()))
    return out


def corporate_action_symbols():
    """Symbols fix_stale_bar.py diagnosed as mid dividend/split adjustment.

    Those are deliberately left un-updated until the adjustment settles, so
    their staleness is EXPECTED and self-resolving — distinguishing them from
    a genuinely broken price feed is the difference between 'ignore' and
    'investigate'. Same file sr_daily_logger reads, so the two agree.
    """
    import json
    try:
        with open("../data/corporate_action_watch.json") as f:
            return set(json.load(f).get("symbols", []))
    except Exception:
        return set()


def live_pool(date=None):
    """The eligible pool + scores as the LIVE path computes them.

    core.scan_universe() reads per-symbol CSVs via core.load_stock and scores
    with core.compute_score -> core.momentum_score. Passing `date` is not
    supported by scan_universe (it always reads to the end of each CSV), so
    this is only meaningful for the most recent date — which is exactly the
    date the live pipeline acts on.
    """
    reg, _ = core.market_regime()
    eligible = core.scan_universe()
    return reg, {s: r["score"] for s, r in eligible.items()}, \
        {s: r.get("vol_63") for s, r in eligible.items()}


def backtest_pool(matrix, turnover, i):
    """The eligible pool + scores as the BACKTEST path computes them at bar i.

    Mirrors run_backtest_laggards_only's inner loop: gate the universe by
    point-in-time trailing turnover, then score each survivor off the price
    matrix up to and including bar i. Uses the SAME core.momentum_score the
    live path uses — if these two ever disagree, the cause is the input data
    (matrix vs per-symbol CSV), not the formula.
    """
    liq = bp.liquid_symbols_at(turnover, i)
    scores, vols = {}, {}
    for s in liq:
        r = core.momentum_score(matrix[s].iloc[:i + 1])
        if r:
            scores[s] = r["score"]
            vols[s] = r.get("vol_63")
    return scores, vols


def compare_selection(scores_live, scores_bt, regime, sectors):
    """Top-N chosen by each path, and the diff."""
    n = sc.REGIME_NAMES[regime]
    top_live = set(bp.select_top_n_capped(scores_live, n, sectors, sc.MAX_PER_SECTOR))
    top_bt = set(bp.select_top_n_capped(scores_bt, n, sectors, sc.MAX_PER_SECTOR))
    return top_live, top_bt


def main():
    args = sys.argv[1:]
    n_dates = 1
    if "--dates" in args:
        i = args.index("--dates")
        if i + 1 < len(args):
            n_dates = int(args[i + 1])
    want_sizing = "--sizing" in args

    matrix = bp.load_price_matrix()
    turnover = bp.load_turnover_matrix(matrix)
    sectors = bp.load_sector_map()
    index_dates = pd.DatetimeIndex(core.load_index().index).normalize()

    print("LIVE vs BACKTEST divergence check")
    print(f"  price matrix: {matrix.shape[1]} symbols, last bar "
          f"{matrix.index[-1].date()}")
    nonsession = matrix.index.difference(index_dates)
    if len(nonsession):
        print(f"  note: {len(nonsession)} matrix date(s) are not NSE sessions "
              f"(latest {nonsession[-1].date()}) — see the score section")

    # ---- 1. Same-date selection agreement (the live path can only speak
    # for the most recent bar, since scan_universe reads to CSV end) ----
    regime, scores_live, vols_live = live_pool()
    i = len(matrix) - 1
    scores_bt, vols_bt = backtest_pool(matrix, turnover, i)

    print(f"\n  regime (live): {regime}   n={sc.REGIME_NAMES[regime]}   "
          f"exposure={sc.REGIME_EXPOSURE[regime]:.3f}")
    print(f"  eligible pool — live {len(scores_live)}  backtest {len(scores_bt)}")

    only_live = set(scores_live) - set(scores_bt)
    only_bt = set(scores_bt) - set(scores_live)
    both = set(scores_live) & set(scores_bt)
    if only_live or only_bt:
        print(f"  POOL DIFFERS: {len(only_live)} live-only, {len(only_bt)} backtest-only")
        if only_live:
            print(f"    live-only    : {sorted(only_live)[:8]}")
        if only_bt:
            print(f"    backtest-only: {sorted(only_bt)[:8]}")
        print("    (a name in one pool but not the other means the two data")
        print("     sources disagree — check for a stale CSV or a symbol")
        print("     missing from the matrix, NOT a scoring bug)")
    else:
        print("  pool: IDENTICAL")

    # Score agreement on the shared names. A non-zero diff here is NOT
    # automatically a formula bug: load_price_matrix ffills up to 5 sessions
    # (deliberate, for halts), so a symbol whose CSV has stopped updating
    # scores off a flat forward-filled tail in the backtest path while the
    # live path scores off its real (shorter) history. Separate the two
    # causes before alarming — a stale CSV is a DATA problem, a diff on a
    # FRESH symbol is a genuine code-path divergence.
    if both:
        stale_map = stale_symbols(matrix)
        ca = corporate_action_symbols()
        fresh_bad, stale_bad = [], []
        for s in both:
            d = abs(scores_live[s] - scores_bt[s])
            if d <= 1e-6:
                continue
            (stale_bad if s in stale_map else fresh_bad).append((s, d))
        diffs = np.array([abs(scores_live[s] - scores_bt[s]) for s in both])
        print(f"  score agreement on {len(both)} shared names: "
              f"max |diff| {diffs.max():.6f}, mean {diffs.mean():.6f}")

        if stale_bad:
            stale_bad.sort(key=lambda x: -x[1])
            print(f"\n  {len(stale_bad)} name(s) differ because their CSV is STALE "
                  f"(matrix ffills, live does not):")
            for s, d in stale_bad[:10]:
                last, lag = stale_map[s]
                tag = " [corporate action — self-resolves]" if s.split(".")[0] in ca else ""
                print(f"    {s:<18s} csv_last={last} lag={lag}d  "
                      f"live {scores_live[s]:.3f} vs bt {scores_bt[s]:.3f}{tag}")
            unexplained = [s for s, _ in stale_bad if s.split(".")[0] not in ca]
            if unexplained:
                print(f"    NOT explained by a corporate action: {unexplained[:8]}")
                print("    -> check the price pipeline; these are genuinely behind.")
            else:
                print("    All are mid dividend/split adjustment (data/"
                      "corporate_action_watch.json) — expected, resolves itself.")

        if fresh_bad:
            fresh_bad.sort(key=lambda x: -x[1])
            # Third cause, distinct from both a stale CSV and a formula bug:
            # the matrix index is the UNION of every symbol's dates, which
            # includes ~12 non-NSE-session dates over 2015-2026 (New Year's
            # Day, special/Muhurat sessions) that most but not all CSVs carry.
            # A symbol MISSING such a date gets ffilled there, so its 126-day
            # lookback window covers one different bar than the live path's.
            # Prices are byte-identical; only the window alignment differs.
            nonsession = matrix.index.difference(pd.DatetimeIndex(index_dates))
            hol_bad, real_bad = [], []
            for s, d in fresh_bad:
                live_idx = core.load_stock(s)
                miss = (len(nonsession.difference(live_idx.index)) > 0
                        if live_idx is not None else False)
                (hol_bad if miss else real_bad).append((s, d))
            if hol_bad:
                print(f"\n  {len(hol_bad)} name(s) differ from NON-SESSION rows in the")
                print("  price matrix (dates absent from nifty50.csv that most CSVs")
                print("  carry — the matrix ffills them for symbols that lack them,")
                print("  shifting the lookback window by a bar). Prices agree exactly;")
                print("  only window alignment differs. Second-order, see notes:")
                for s, d in hol_bad[:10]:
                    print(f"    {s:<18s} live {scores_live[s]:.4f} "
                          f"vs bt {scores_bt[s]:.4f}  diff {d:.6f}")
            if real_bad:
                print(f"\n  {len(real_bad)} name(s) differ on FRESH, ALIGNED data — this")
                print("  is a real live-vs-backtest code-path divergence. Investigate")
                print("  before trusting any gate result.")
                for s, d in real_bad[:10]:
                    print(f"    {s:<18s} live {scores_live[s]:.4f} "
                          f"vs bt {scores_bt[s]:.4f}  diff {d:.6f}")
        elif not stale_bad:
            print("  scores: IDENTICAL on every shared name")
        else:
            print("\n  No divergence on any FRESH symbol — the code paths agree;")
            print("  every difference is attributable to stale input data.")

    top_live, top_bt = compare_selection(scores_live, scores_bt, regime, sectors)
    print(f"\n  top-{sc.REGIME_NAMES[regime]} selection")
    print(f"    live    : {sorted(top_live)}")
    print(f"    backtest: {sorted(top_bt)}")
    if top_live == top_bt:
        print("    SELECTION: IDENTICAL")
    else:
        print(f"    SELECTION DIFFERS — live-only {sorted(top_live - top_bt)}, "
              f"backtest-only {sorted(top_bt - top_live)}")

    # ---- 1b. Could a stale name actually get BOUGHT? ----
    # full_advisor has a MAX_STALE_SESSIONS guard, but core.scan_universe and
    # paper_trader do NOT — the paper book (the gate's evidence) can select a
    # name on forward-filled prices. This measures the actual exposure rather
    # than assuming it: how close is the best stale-data name to the cutoff?
    stale_map = stale_symbols(matrix)
    stale_elig = {s: sc_ for s, sc_ in scores_live.items() if s in stale_map}
    if stale_elig:
        ranked = sorted(scores_live.values(), reverse=True)
        n = sc.REGIME_NAMES[regime]
        cutoff = ranked[n - 1] if len(ranked) >= n else float("-inf")
        best = max(stale_elig, key=lambda s: stale_elig[s])
        print(f"\n  stale-data names that are ELIGIBLE: {len(stale_elig)}")
        print(f"    highest-scoring: {best} at {stale_elig[best]:.2f}   "
              f"top-{n} cutoff {cutoff:.2f}")
        if stale_elig[best] >= cutoff:
            print("    *** A NAME WITH STALE PRICES IS INSIDE THE TOP-N. ***")
            print("    paper_trader has no staleness guard — this can enter the")
            print("    book on forward-filled prices and contaminate gate evidence.")
        else:
            print("    none inside the top-N — no signal is affected right now")
            print("    (latent risk only: neither core.scan_universe nor")
            print("     paper_trader screens for staleness the way full_advisor does)")

    # ---- 2. Sizing agreement ----
    if want_sizing and top_live == top_bt and top_live:
        names = sorted(top_live)
        w_live = bp.conviction_weights(scores_live, vols_live, names, sc.CONVICTION_TILT)
        w_bt = bp.conviction_weights(scores_bt, vols_bt, names, sc.CONVICTION_TILT)
        print(f"\n  conviction weights (tilt={sc.CONVICTION_TILT})")
        print(f"    {'symbol':<16s} {'live':>9s} {'backtest':>9s} {'diff':>9s}")
        for s in names:
            print(f"    {s:<16s} {w_live[s]:9.4f} {w_bt[s]:9.4f} "
                  f"{w_live[s]-w_bt[s]:+9.6f}")
        md = max(abs(w_live[s] - w_bt[s]) for s in names)
        print(f"    max |weight diff|: {md:.6f}"
              + ("  IDENTICAL" if md < 1e-9 else "  DIFFERS — investigate"))

    # ---- 3. What the paper book actually holds vs what it should ----
    try:
        import json
        with open("../data/paper_state.json") as f:
            st = json.load(f)
        held = {s for s, p in st.get("positions", {}).items()
                if p.get("entry_price", 0) > 0
                and s not in (sc.GOLD_SYMBOL, sc.INTL_SYMBOL)}
        pending = {o["sym"] for o in st.get("pending_buys", [])}
        print(f"\n  paper book: {len(held)} held {sorted(held)}"
              + (f", {len(pending)} pending {sorted(pending)}" if pending else ""))
        # The book is only EXPECTED to match top-N right after a rebalance —
        # laggards-only means it holds last rotation's names until the next
        # rebalance day. A mismatch here is information, not necessarily a bug.
        stale = held - top_live
        if stale:
            print(f"    holds {sorted(stale)} not in today's top-N — EXPECTED")
            print("    under laggards-only between rebalances (names are")
            print("    re-evaluated on the rebalance day, not daily).")
        else:
            print("    all held names are in today's top-N")
    except FileNotFoundError:
        print("\n  (no paper_state.json — skipping book comparison)")

    # ---- 4. Verdict ----
    print("\n" + "=" * 62)
    if top_live == top_bt and not only_live and not only_bt:
        print("  VERDICT: live and backtest paths AGREE on today's selection.")
        print("  Differences found are attributable to input data (stale CSVs,")
        print("  non-session matrix rows), not to the strategy code.")
    else:
        print("  VERDICT: PATHS DISAGREE — do not trust gate results until the")
        print("  cause is found. A gate month scored while selection diverges")
        print("  measures the bug, not the strategy.")
    print("=" * 62)
    print("  Agreement means the live path implements the validated strategy")
    print("  faithfully. It does NOT mean the strategy is profitable — that is")
    print("  what gate_report.py measures.")


if __name__ == "__main__":
    main()
