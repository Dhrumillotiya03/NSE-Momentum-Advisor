import json
import os
import sys

import pandas as pd
import numpy as np

import yaml
import strategy_config as sc
import chart_analysis as ca
from core import load_stock, load_index, market_regime as _core_market_regime, compute_atr, compute_rsi, SECTOR_FILE, liquid_universe
from support_resistance import get_levels, strength_label

# Advisory-call ledger: every BUY recommendation this advisor emits is
# appended here (deduped per data-date+symbol) so call quality is
# MEASURABLE — python call_report.py scores fills/targets/stops forward.
# Rows are stamped with the DATA date (each symbol's last completed candle),
# not the run date, per the partial-candle convention (see CLAUDE.md).
CALLS_LOG = "../data/advisor_calls_log.csv"
CALL_COLUMNS = ["date", "symbol", "sector", "regime", "rank", "alpha",
                "price", "buy_at", "target", "stop", "rr", "s_str", "r_str",
                # added 2026-08-01: lets call_report separate the strategy's
                # own book from merely-passing names, and flag overbought entries
                "in_strategy_top_n", "rsi",
                # added 2026-08-15: the PREDICTED chance the limit entry fills
                # within FILL_WINDOW_DAYS. call_report.py already measures the
                # REALISED fill rate over the same window, so logging the
                # forecast alongside it makes the estimate falsifiable later —
                # otherwise a number is shown to the user that nothing ever
                # checks.
                "fill_prob"]
with open("../config.yaml") as f:
    cfg = yaml.safe_load(f)
CAPITAL = cfg["capital"]
RISK_PER_TRADE = cfg["risk"]["risk_per_trade"]

# Stale-data guard (added 2026-08-01): data_integrity_check.py runs nightly
# and WARNs on stale CSVs, but nothing stopped the advisor itself from
# issuing a call built on days-old data in between checks (a parallel
# download failure, a missed cron run, a name that quietly stopped updating).
# A call quoting a 5-session-old close as "current price" is actively
# misleading. Measured against the INDEX's last bar, not wall-clock time —
# the market can be legitimately closed (weekend/holiday) with no staleness
# at all; what matters is whether this NAME lags the rest of the data pull.
MAX_STALE_SESSIONS = 3

# Momentum-call level construction (see get_trade_levels). ATR multiples, not
# S/R distances — momentum names trade far above their supports.
ENTRY_ATR_MULT = 0.5    # shallow pullback from last close; fills in normal noise
STOP_ATR_MULT = 2.0     # ~2 ATR below entry, hard-capped at the -18% engine stop
TARGET_ATR_MULT = 4.0   # used when there is no overhead resistance (52w highs)
MIN_RR = 1.5            # momentum calls should offer >=1.5R; ATR targets give 2R


# ---------- MARKET REGIME ----------
#
# Canonical regime formula now lives in core.py (the breadth-gated version,
# same one recommend.py / backtest_portfolio.py / confidence_table.py use).
# This wrapper keeps full_advisor.py's existing 3-value return type (no
# separate HIGH_RISK bucket, no drawdown gate on BULL/BEAR) so the rest of
# this file's control flow is unchanged.

def market_regime():
    regime, _breadth = _core_market_regime()
    return regime


# ---------- DATA LOAD ----------

def load_sectors():
    with open(SECTOR_FILE, "r") as f:
        return json.load(f)


def compute_return(df, days):
    if len(df) < days + 1:
        return np.nan
    return df["Close"].iloc[-1] / df["Close"].iloc[-days-1] - 1


# ---------- SECTOR SCORES ----------

def sector_scores(sectors):
    scores = {}

    for sector, symbols in sectors.items():
        vals = []

        for sym in symbols:
            df = load_stock(sym)
            if df is None:
                continue

            r1 = compute_return(df, 21)
            r3 = compute_return(df, 63)

            if not np.isnan(r1) and not np.isnan(r3):
                vals.append(0.6*r1 + 0.4*r3)

        if vals:
            scores[sector] = np.mean(vals)

    return scores


# ---------- STOCK ALPHA ----------
#
# Formula = core.compute_score's validated scorer (ret_126 / vol_63), gated on
# 3m and 6m returns both positive and price above 50DMA. Walk-forward tested
# across 18 overlapping 3y windows (2015-2026) against the previous 5-factor
# blend: higher/less-variable Sharpe, never negative, better worst-case
# drawdown. The previous blend had higher median raw return but a fatter
# left tail (worse worst-case DD, 2 negative-Sharpe windows) — not worth it
# for risk-adjusted, capital-preservation-first goals.

def compute_alpha(df):
    from core import compute_score
    r = compute_score(df)
    return r["score"] if r is not None else None


def compute_supertrend(df, period=10, multiplier=3):
    high  = df["High"]
    low   = df["Low"]
    close = df["Close"]

    tr = pd.concat([
        high - low,
        abs(high - close.shift()),
        abs(low  - close.shift())
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()

    hl_avg = (high + low) / 2
    upper  = hl_avg + multiplier * atr
    lower  = hl_avg - multiplier * atr

    direction = pd.Series(index=df.index, dtype=float)

    for i in range(1, len(df)):
        if close.iloc[i] > upper.iloc[i - 1]:
            direction.iloc[i] = 1
        elif close.iloc[i] < lower.iloc[i - 1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i - 1]

    supertrend_line = lower.copy()
    supertrend_line[direction == -1] = upper[direction == -1]

    return direction.iloc[-1], supertrend_line.iloc[-1]

def get_trade_levels(df, atr_stop=None):
    """Entry/stop/target for a MOMENTUM call.

    REWRITTEN 2026-08-01. The old construction priced entry AT the nearest
    support and measured rr against it. For strong momentum names that support
    sits 35-44% below price (WELCORP 44.4%, LAURUSLABS 43.6% on 2026-08-01) —
    an unfillable limit, an rr of 0.01-0.71 that always failed the rr>=1 gate,
    and a stop 40%+ wide that the -18% strategy stop would fire long before.
    Net effect: the advisor structurally excluded the exact names the validated
    strategy buys (fill rate 1/8 on the July ledger). Momentum entries are
    pullback/breakout entries near price, not support bounces.

    Entry: a shallow ATR pullback from the last close (fills in normal noise),
    floored at the nearest support if that support is close by.
    Stop:  ATR-based, but never wider than the strategy's own -18% catastrophic
           stop — a call whose stop is looser than the engine's is incoherent.
    Target: nearest resistance if it offers real upside, else an ATR projection
           (a momentum name at 52w highs has no overhead resistance to use).
    """
    current = float(df["Close"].iloc[-1])
    support, resistance, s_str, r_str = get_levels(df)
    atr = atr_stop if atr_stop and not np.isnan(atr_stop) else current * 0.02

    # --- entry: shallow pullback, not a deep support bounce ---
    buy_at = current - ENTRY_ATR_MULT * atr
    if support and current > support > buy_at:
        buy_at = support          # support is nearer than the ATR pullback
    buy_at = round(min(buy_at, current), 2)

    # --- stop: ATR-wide, capped at the strategy's catastrophic stop ---
    stop = buy_at - STOP_ATR_MULT * atr
    floor = buy_at * sc.CATASTROPHIC_STOP      # never wider than -18%
    stop = round(max(stop, floor), 2)

    # --- target: real resistance, else ATR projection ---
    risk = buy_at - stop
    min_target = buy_at + risk                  # at least 1R
    if resistance and resistance > min_target:
        target = resistance
    else:
        target = buy_at + TARGET_ATR_MULT * atr
    target = round(target, 2)

    reward = target - buy_at
    rr = round(reward / risk, 2) if risk > 0 else 0
    return buy_at, stop, target, support, resistance, rr, s_str, r_str

# Limit orders here are worked for a few sessions, not indefinitely.
# call_report.py scores a call as FILLED if the low touches buy_at within 10
# sessions, so the forecast window matches the measurement window — otherwise
# the printed probability and the scorecard would be answering different
# questions.
FILL_WINDOW_DAYS = 10


def fill_probability(df, buy_at):
    """P(price dips to `buy_at` within FILL_WINDOW_DAYS), or None.

    Reuses the S/R subsystem's empirical (distance x volatility) touch table.
    That table is calibrated on SWING-PIVOT levels while this is an ATR-derived
    entry, but it is keyed on distance and volatility rather than on what kind
    of level it is, so it transfers — the same reasoning CLAUDE.md records for
    the min-separation change. Treat it as an estimate of "is this a routine
    pullback or an unusual one", not a precise fill rate; call_report.py
    measures what actually happened.
    """
    try:
        import support_resistance as sr
        p, _n = sr.reach_probability_v2(df, buy_at, "down",
                                        forward_days=FILL_WINDOW_DAYS)
        return p
    except Exception:
        return None


def position_size(price, atr):
    stop_dist = 2 * atr
    risk_amt = CAPITAL * RISK_PER_TRADE

    shares = int(risk_amt / stop_dist)
    value = shares * price
    stop = price - stop_dist

    return shares, value, stop

# ---------- BUY SCAN ----------

def compute_buy_calls():
    """The advisor's full buy pipeline (regime -> top-3 sectors -> gated
    momentum + S/R levels). Returns (regime, top_sectors, buy_list) where
    buy_list is a list of dicts sorted by alpha descending."""
    regime = market_regime()
    sectors = load_sectors()
    nifty_index = load_index()   # loaded once, reused for relative_strength per name

    # F&O liquidity gate (see strategy_config.py's Universe gate comment /
    # memory fno-universe-migration) — restrict both sector scoring and the
    # buy scan to names liquid enough to have listed F&O, so recommendations
    # match what's actually tradeable/hedgeable.
    gated = liquid_universe()
    sectors = {sec: [s for s in syms if s in gated] for sec, syms in sectors.items()}
    sectors = {sec: syms for sec, syms in sectors.items() if syms}

    sec_scores = sector_scores(sectors)
    ranked_sec = sorted(sec_scores.items(), key=lambda x: x[1], reverse=True)
    top_sectors = [s[0] for s in ranked_sec[:3]]

    index_last_date = load_index().index[-1]
    stale_skipped = []

    # SECTOR GATE REMOVED 2026-08-01. The advisor used to scan ONLY the top-3
    # sectors, which is NOT the validated selection path (the backtest ranks
    # the whole gated universe and applies a 2-per-sector CAP). Measured on
    # 2026-08-01 the gate blocked 9 of the top 10 names by momentum score and
    # left ZERO overlap with the strategy's actual top-4: the advisor called
    # DIXON (score 13.8) while the strategy wanted WELCORP/LAURUSLABS/RADICO/
    # ADANIENSOL (scores 40-51). Sector scores were also statistically
    # indistinguishable (0.117 vs 0.088), so a top-3 cut was noise discarding
    # most of the universe — PHARMA missed the cut by 0.007 and took
    # LAURUSLABS (49.42) with it. top_sectors is retained for DISPLAY only.
    buy_list = []
    for sector, symbols in sectors.items():
        for sym in symbols:
            df = load_stock(sym)
            if df is None:
                continue

            # refuse to issue a call on data that lags the rest of the pull —
            # a stale close quoted as "current price" is actively misleading,
            # not just conservative
            gap = np.busday_count(df.index[-1].date(), index_last_date.date())
            if gap > MAX_STALE_SESSIONS:
                stale_skipped.append((sym, df.index[-1].date(), gap))
                continue

            alpha = compute_alpha(df)
            if alpha is None or alpha <= 0:
                continue
            # RSI is ADVISORY per strategy_config.RSI_OVERBOUGHT (=80), not a
            # hard reject — full_advisor previously hardcoded 75 and dropped
            # names the strategy itself would buy (LAURUSLABS RSI 84 on
            # 2026-08-01, momentum score 49.4, a strategy top-4 name).
            # Reported as an overbought flag so the human can judge entry
            # timing, rather than silently discarding a valid signal.
            rsi_now = compute_rsi(df["Close"])

            price = df["Close"].iloc[-1]
            atr = compute_atr(df)
            if np.isnan(atr) or atr == 0:
                continue

            shares, value, stop_atr = position_size(price, atr)
            entry, stop, target, support, resistance, rr, s_str, r_str = get_trade_levels(df, atr)

            # FILTERS RELAXED 2026-08-01 — the old rr>=1.0 / s_str>=2 /
            # "support within 6%" trio encoded a support-BOUNCE premise and so
            # structurally rejected momentum leaders (their nearest support is
            # 35-44% away and freshly-broken-out levels have 1 touch). Entry is
            # now an ATR pullback near price, so the distance test is inherent
            # and s_str is reported as context rather than used as a gate.
            if rr < MIN_RR:
                continue

            buy_list.append({
                "symbol": sym, "sector": sector, "alpha": alpha,
                "price": float(price), "shares": shares, "value": value,
                "buy_at": entry, "target": target, "stop": stop,
                "rr": rr, "s_str": s_str, "r_str": r_str,
                "rsi": round(float(rsi_now), 1),
                "overbought": bool(rsi_now > sc.RSI_OVERBOUGHT),
                # descriptive chart context (chart_analysis.py) — shown to the
                # human, NEVER used to include/exclude a call
                "chart": ca.summarise_plain(ca.analyse(df, index=nifty_index)),
                # How likely is this limit entry to actually FILL? The entry
                # sits BELOW the last close, so the trade only happens if
                # price dips to it — without this the reader has no way to
                # tell a routine 1% pullback from one that rarely comes. Uses
                # the same empirical (distance x volatility) P(touch) table
                # the S/R subsystem is calibrated on, at FILL_WINDOW_DAYS to
                # match how call_report.py scores fills. ADVISORY ONLY: it is
                # displayed, never used to include, exclude or rank a call.
                "fill_prob": fill_probability(df, entry),
                "date": str(df.index[-1].date()),
            })

    buy_list.sort(key=lambda x: x["alpha"], reverse=True)

    # Apply the SAME 2-per-sector cap the backtest enforces
    # (backtest_portfolio.select_top_n_capped), so the advised names are the
    # names the validated strategy would actually hold. Names are flagged
    # rather than dropped: in_strategy_top_n marks the ones inside the current
    # regime's book, so the display can separate "the strategy's picks" from
    # "also passing the filters".
    from backtest_portfolio import select_top_n_capped, load_sector_map
    smap = load_sector_map()
    alphas = {b["symbol"]: b["alpha"] for b in buy_list}
    n_regime = sc.REGIME_NAMES.get(regime, sc.REGIME_NAMES.get("SIDEWAYS", 3))
    book = set(select_top_n_capped(alphas, n_regime, smap, sc.MAX_PER_SECTOR))
    capped = set(select_top_n_capped(alphas, len(alphas), smap, sc.MAX_PER_SECTOR))
    for b in buy_list:
        b["in_strategy_top_n"] = b["symbol"] in book
        b["passes_sector_cap"] = b["symbol"] in capped
    buy_list.sort(key=lambda x: (not x["in_strategy_top_n"], -x["alpha"]))
    return regime, top_sectors, buy_list, stale_skipped


def log_calls(regime, buy_list, top_n=8):
    """Append today's top-N calls to the ledger, deduped per (date, symbol)."""
    calls = buy_list[:top_n]
    if not calls:
        return 0
    existing = set()
    if os.path.exists(CALLS_LOG):
        try:
            prev = pd.read_csv(CALLS_LOG, usecols=["date", "symbol"])
            existing = set(zip(prev["date"].astype(str), prev["symbol"]))
        except Exception as e:
            # Do NOT swallow this silently. A read failure here disables the
            # duplicate guard, so the ledger — a MEASUREMENT record scored by
            # call_report.py — would start double-counting calls without any
            # visible symptom. (Happened 2026-08-01..08-10: CALL_COLUMNS grew
            # by 2 fields while the on-disk header kept 13, so every read
            # raised ParserError and dedupe was off for 10 days.)
            print(f"[advisor] WARNING: cannot read {CALLS_LOG} for dedupe "
                  f"({type(e).__name__}: {e}) — duplicate guard is OFF. "
                  f"Fix the ledger before trusting call_report.py.")
    rows = []
    for rank, c in enumerate(calls, 1):
        if (c["date"], c["symbol"]) in existing:
            continue
        rows.append({"date": c["date"], "symbol": c["symbol"],
                     "sector": c["sector"], "regime": regime, "rank": rank,
                     "alpha": round(c["alpha"], 4), "price": round(c["price"], 2),
                     "buy_at": round(c["buy_at"], 2), "target": round(c["target"], 2),
                     "stop": round(c["stop"], 2), "rr": c["rr"],
                     "s_str": c["s_str"], "r_str": c["r_str"],
                     "in_strategy_top_n": bool(c.get("in_strategy_top_n")),
                     "rsi": c.get("rsi"),
                     "fill_prob": c.get("fill_prob")})
    if rows:
        new = pd.DataFrame(rows, columns=CALL_COLUMNS)
        # Appending blind is what broke this ledger once already: when
        # CALL_COLUMNS gained a field, existing rows kept the OLD header and
        # every subsequent read raised ParserError. If the on-disk header no
        # longer matches, REWRITE the file with the union of columns (old rows
        # get NaN in the new fields, which is the truthful value) instead of
        # appending a differently-shaped row.
        header_ok = False
        if os.path.exists(CALLS_LOG):
            try:
                on_disk = list(pd.read_csv(CALLS_LOG, nrows=0).columns)
                header_ok = on_disk == CALL_COLUMNS
            except Exception:
                header_ok = False
            if not header_ok:
                try:
                    old = pd.read_csv(CALLS_LOG, header=0, names=CALL_COLUMNS)
                except Exception:
                    old = pd.read_csv(CALLS_LOG)
                new = pd.concat([old, new], ignore_index=True)
                new = new.reindex(columns=CALL_COLUMNS)
                new.to_csv(CALLS_LOG, index=False)
                print(f"[advisor] ledger header was stale — rewrote "
                      f"{CALLS_LOG} with {len(new)} rows on current schema")
                return len(rows)
        new.to_csv(CALLS_LOG, mode="a",
                   header=not os.path.exists(CALLS_LOG), index=False)
    return len(rows)


# ---------- MAIN REPORT ----------

def main():
    quiet = "--log" in sys.argv    # nightly pipeline mode: ledger + one line
    regime, top_sectors, buy_list, stale_skipped = compute_buy_calls()
    n_logged = log_calls(regime, buy_list)

    if quiet:
        print(f"advisor calls: {len(buy_list[:8])} live, {n_logged} newly logged "
              f"({regime}, sectors: {', '.join(top_sectors)})")
        if stale_skipped:
            print(f"  WARN: {len(stale_skipped)} name(s) skipped for stale data: "
                  + ", ".join(f"{s} ({d}, {g}d behind)" for s, d, g in stale_skipped[:5]))
        return

    print("\n==============================")
    print("📊 AI STOCK ADVISOR REPORT")
    print("==============================")

    print("\nMarket Regime:", regime)
    print("Top Sectors:", ", ".join(top_sectors))
    if stale_skipped:
        print(f"\n⚠️  {len(stale_skipped)} name(s) EXCLUDED — data lags the index "
              f"by more than {MAX_STALE_SESSIONS} sessions:")
        for s, d, g in stale_skipped:
            print(f"    {s}: last bar {d} ({g} sessions behind)")

    print("\n📈 BUY RECOMMENDATIONS:\n")

    if regime in ["BEAR", "HIGH_RISK"]:
        print("⚠️ Market is currently BEAR/HIGH_RISK.")
        print("Showing best available stocks anyway — use smaller position sizes.\n")

    for c in buy_list[:8]:
        print(f"{'='*42}")
        print(f"  {c['symbol']}")
        print(f"  Alpha Score:    {c['alpha']:.4f}")
        print(f"  Current Price:  ₹{c['price']:.2f}")
        # Label the SIZING BASIS explicitly. `shares` is risk-based sizing
        # (RISK_PER_TRADE of config.yaml's notional CAPITAL, divided by the
        # 2xATR stop distance) — it answers "how many shares before the stop
        # costs me 1% of capital". That is a DIFFERENT mechanism from the
        # strategy's conviction-weighted portfolio weights, and the two give
        # different numbers; printing a bare "Shares:" invited reading it as
        # the strategy's own position size.
        print(f"  Shares:         {c['shares']}   "
              f"(risk-sized: ₹{CAPITAL*RISK_PER_TRADE:,.0f} at risk "
              f"if the stop hits, on ₹{CAPITAL:,.0f} notional)")
        print(f"  Position Value: ₹{c['value']:,.0f}")
        print(f"  ─────────────────────────────────────")
        dist = (c["price"] - c["buy_at"]) / c["price"] * 100
        stop_pct = (c["stop"] / c["buy_at"] - 1) * 100
        tgt_pct = (c["target"] / c["buy_at"] - 1) * 100
        book = "  ⭐ STRATEGY TOP-N" if c.get("in_strategy_top_n") else ""
        print(f"  📥 Buy at:   ₹{c['buy_at']:.2f}  ({dist:.1f}% below last close){book}")
        if c.get("fill_prob") is not None:
            print(f"     ↳ chance price actually dips to it within "
                  f"{FILL_WINDOW_DAYS} sessions: ~{c['fill_prob']}%   "
                  f"(else no fill, no trade)")
        print(f"  🎯 Target:   ₹{c['target']:.2f}  ({tgt_pct:+.1f}%)  "
              f"[nearest resistance: {strength_label(c['r_str'])} — {c['r_str']} touches]")
        print(f"  🛑 Stop:     ₹{c['stop']:.2f}  ({stop_pct:+.1f}% from entry)")
        print(f"  ⚖️  R:R:      1:{c['rr']}"
              + (f"   ⚠️ RSI {c['rsi']:.0f} overbought" if c.get("overbought") else ""))
        if c.get("chart"):
            print(f"  📉 Chart:    {c['chart']}")
        print()

    if n_logged:
        print(f"({n_logged} call(s) appended to {CALLS_LOG} — score them with: python call_report.py)")


if __name__ == "__main__":
    main()
