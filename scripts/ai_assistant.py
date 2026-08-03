"""
Tool-calling AI trading assistant. Replaces ai_strategist.py's prompt-stuffing
(dumping every script's stdout into one giant prompt) with a real
function-calling loop: the model decides which tool(s) to call based on the
user's question, and every tool is backed directly by core.py / exit_engine.py
so the assistant can never diverge from the canonical regime/scoring/exit
logic.

Requires an Ollama model that supports tool-calling (llama3 does NOT; use
e.g. qwen2.5, llama3.1, mistral-nemo). Set MODEL_NAME below once you've
pulled one — everything else is model-agnostic.

Usage:
    python ai_assistant.py
"""
import json
import os
import numpy as np
import pandas as pd
import requests

import strategy_config as sc
import core
from exit_engine import (
    check_catastrophic_stop, check_requalification,
    is_non_strategy_holding, is_last_trading_day_of_month, load_stock as exit_load_stock,
)

# ---------- Pluggable model config ----------
MODEL_NAME = "qwen2.5:7b"     # any Ollama model that supports tool-calling
OLLAMA_URL = "http://localhost:11434/api/chat"


# ---------- Tool implementations ----------
# Each returns a small JSON-serializable dict — the model reads this back
# as the tool result and decides what to say / call next.
#
# FORMATTING RULE (2026-07-14): every return/ratio field is pre-formatted as
# an explicit percent STRING ("+220.6%"), never a raw decimal. A real user
# read HFCL's raw ret_6m of 2.206 (i.e. +220.6%) as "2%", told the model the
# return was bad, and the model capitulated and recommended a LOWER-scored
# name as "better". Raw floats invite that whole failure class.


def pct(x, digits=1, signed=True):
    if x is None:
        return None
    try:
        return f"{x:+.{digits}%}" if signed else f"{x:.{digits}%}"
    except (TypeError, ValueError):
        return None

def data_freshness():
    """Age of the downloaded data. The user runs this system irregularly —
    if the nightly pipeline silently died, advice would be based on stale
    prices without anyone noticing."""
    import pandas as pd
    dates = pd.to_datetime(
        pd.read_csv("../data/index_data/nifty50.csv")["Date"], errors="coerce").dropna()
    last = dates.max()
    age_days = (pd.Timestamp.today().normalize() - last.normalize()).days
    return last.date(), age_days


def market_status():
    regime, breadth = core.market_regime()
    n = sc.REGIME_NAMES[regime]
    exposure = sc.REGIME_EXPOSURE[regime]
    table = core.load_confidence()
    reg_stats = table["regime_stats"].get(regime, {})
    last_date, age = data_freshness()
    return {
        "data_as_of": str(last_date),
        "data_staleness_warning": (f"DATA IS {age} DAYS OLD — run the download "
                                   f"pipeline (run_daily_log.sh) before trusting "
                                   f"any number" if age > 4 else None),
        "regime": regime,
        "breadth_pct_above_200dma": None if np.isnan(breadth) else pct(float(breadth), 0, signed=False),
        "names_to_hold": n,
        "target_exposure": pct(exposure, 0, signed=False),
        "historical_win_rate_this_regime": pct(reg_stats.get("win_rate"), 0, signed=False),
        "historical_median_21d_fwd_return": pct(reg_stats.get("median_fwd")),
        "historical_n_setups": reg_stats.get("n"),
    }


def stock_status(symbol):
    if not symbol.upper().endswith(".NS"):
        symbol = symbol.upper() + ".NS"
    else:
        symbol = symbol.upper()

    df = core.load_stock(symbol)
    if df is None:
        return {"error": f"No price data for {symbol}"}
    if len(df) < core.LOOKBACK + 60:
        return {"error": f"Not enough history for {symbol} ({len(df)} rows)"}

    regime, _breadth = core.market_regime()
    r = core.compute_score(df)
    support, resistance, s_str, r_str = core.sr_levels(df, symbol=symbol)
    price = float(df["Close"].iloc[-1])

    result = {
        "symbol": symbol,
        "price": price,
        "regime": regime,
        "support": support,
        "resistance": resistance,
        "support_strength": s_str,
        "resistance_strength": r_str,
        "eligible_momentum_setup": r is not None,
    }
    ew = earnings_watch(symbol, as_of=df.index[-1])
    if ew:
        result["earnings_watch"] = ew
    if r is None:
        result["reason_not_eligible"] = ("fails momentum filter: needs positive 6m AND 3m "
                                          "returns, and price above 50DMA")
        return result

    decile_stats, regime_stats = core.confidence_for(r["score"], regime)
    result.update({
        "momentum_score": round(r["score"], 1),
        "return_6_month": pct(r["ret_6m"]),
        "return_3_month": pct(r["ret_3m"]),
        "rsi": round(r["rsi"], 1),
        "overbought": r["rsi"] > sc.RSI_OVERBOUGHT,
        "historical_win_rate_this_decile": pct(decile_stats.get("win_rate"), 0, signed=False),
        "historical_median_21d_fwd_this_decile": pct(decile_stats.get("median_fwd")),
        "historical_win_rate_this_regime": pct(regime_stats.get("win_rate"), 0, signed=False),
    })
    return result


def chart_analysis(symbol):
    """Candlestick / chart-structure read for one stock — the visual analysis a
    human does off a daily chart. DESCRIPTIVE context only; never a trade signal
    (see chart_analysis.py's design rules)."""
    if not symbol or not symbol.strip():
        return {"error": "symbol is required"}
    symbol = symbol.upper()
    if not symbol.endswith(".NS"):
        symbol += ".NS"
    df = core.load_stock(symbol)
    if df is None:
        return {"error": f"No price data for {symbol}"}
    import chart_analysis as ca
    a = ca.analyse(df, index=core.load_index())
    if "error" not in a:
        a["symbol"] = symbol
        a["summary"] = ca.summarise(a)
    return a


ANNOUNCEMENTS_DIR = "../data/announcements/"


def earnings_watch(symbol, as_of=None, horizon_end_date=None):
    """Estimated next-earnings window, DISPLAY-ONLY — flags timing risk, never
    a trading signal (see CLAUDE.md: the automated announcement-driven exit
    veto was backtested and REJECTED, memory exit-announcements-rejected;
    this is descriptive awareness only, symmetric to chart_analysis's
    "descriptive not signal" status).

    horizon_end_date: the CALENDAR date the horizon ends (not a trading-day
    count — comparing a calendar-day projection against a trading-day count
    systematically undercounts the window by ~30%, which produced a false
    "outside horizon" on a real case during testing: WELCORP's earnings
    estimate landed 21 CALENDAR days out while the horizon was 17 TRADING
    days, both correctly inside the same ~25-calendar-day horizon).

    Method: NSE 'Outcome of Board Meeting' announcements whose text mentions
    financial results ARE the historical earnings-release dates (downloaded
    by download_announcements.py). Projects the next date as last_result +
    median(sane trailing gaps), where "sane" = 75-100 days (one quarter) —
    filters out backfill/pagination gaps in the announcement history (verified
    on RELIANCE: raw gaps included spurious 545-546 day values from missing
    quarters, median-of-sane-gaps still projected within 7 days of the real
    date). This is an ESTIMATE from historical cadence, not a scraped
    forward calendar — NSE doesn't reliably publish those far in advance.
    Returns {} if there's no announcement history or too few clean data
    points to estimate a cadence."""
    if not symbol or not symbol.strip():
        return {}
    symbol = symbol.upper()
    if not symbol.endswith(".NS"):
        symbol += ".NS"
    path = f"{ANNOUNCEMENTS_DIR}{symbol}.csv"
    if not os.path.exists(path):
        return {}
    try:
        df = pd.read_csv(path)
    except Exception:
        return {}
    if "desc" not in df.columns or "date" not in df.columns:
        return {}
    df["date"] = pd.to_datetime(df["date"], format="%d-%b-%Y %H:%M:%S", errors="coerce")
    df = df.dropna(subset=["date"])
    # NSE phrasing varies ("financial results" vs "financial statements", and
    # the text field is truncated mid-word in the announcements CSV) — match
    # either stem rather than one exact phrase (verified against RELIANCE's
    # real history: the April 2026 result used "financial statem[ents]"
    # phrasing and was silently missed by a "financial result"-only match).
    mask = (df["desc"] == "Outcome of Board Meeting") & \
           df["text"].astype(str).str.contains(
               "financial result|financial statem", case=False, na=False)
    hits = df.loc[mask, "date"].sort_values()
    if len(hits) < 2:
        return {}

    now = pd.Timestamp(as_of).normalize() if as_of else pd.Timestamp.today().normalize()
    last_result = hits.iloc[-1]
    gaps = hits.diff().dt.days.dropna()
    sane = gaps[(gaps >= 75) & (gaps <= 100)]
    if len(sane) == 0:
        return {"last_result_date": str(last_result.date()),
                "note": "insufficient clean quarterly cadence to project the next date"}
    cadence = float(sane.median())
    projected = last_result + pd.Timedelta(days=cadence)
    # keep rolling the projection forward by the cadence if it's already past
    # (covers a stale announcements CSV that hasn't been backfilled recently)
    while projected < now:
        projected = projected + pd.Timedelta(days=cadence)
    confidence = "estimate from historical cadence, not a confirmed date"

    # A "board meeting scheduled to consider... financial results" is a
    # SHARPER forward signal than the cadence estimate when one exists and
    # hasn't resolved into an "Outcome of Board Meeting" row yet — companies
    # announce the meeting date ~1 week ahead. Prefer it over the projection
    # if it's later than the last confirmed result and still in the future.
    sched_mask = df["text"].astype(str).str.contains(
        r"meeting.*scheduled.*financial result|meeting.*scheduled.*financial statem",
        case=False, na=False, regex=True)
    sched_rows = df.loc[sched_mask & (df["date"] > last_result)]
    if len(sched_rows):
        m = sched_rows["text"].str.extract(
            r"(?:held on|scheduled.*?on)\s+\w+,?\s+(\w+ \d{1,2},? \d{4})", expand=False)
        for txt in m.dropna():
            try:
                sched_date = pd.Timestamp(txt.replace(",", ""))
            except Exception:
                continue
            if sched_date >= now - pd.Timedelta(days=3):
                projected = sched_date
                confidence = "board meeting formally scheduled for this date (not yet confirmed as held)"
                break

    days_until = (projected - now).days
    data_age_days = (now - df["date"].max()).days
    out = {
        "last_result_date": str(last_result.date()),
        "estimated_next_result_date": str(projected.date()),
        "estimated_cadence_days": round(cadence),
        "days_until_estimated": days_until,
        "confidence": confidence,
        "announcements_data_age_days": data_age_days,
    }
    if data_age_days > 14:
        out["data_stale_warning"] = (
            f"announcements data is {data_age_days}d old — a scheduled/actual "
            f"result inside that gap may be missing from this estimate")
    if horizon_end_date is not None:
        end = pd.Timestamp(horizon_end_date).normalize()
        out["inside_stated_horizon"] = now <= projected <= end
    return out


def should_i_sell(symbol, entry_price=None):
    """Exit verdict for ANY symbol — the position does NOT need to be in the
    recorded book. This is an advisory engine: the common question is "I hold
    X, when do I get out?" for a stock the system was never told about.
    entry_price is optional; supply it to enable the -18% catastrophic stop
    and the gain-since-entry figure, omit it for a pure signal read."""
    if not symbol or not symbol.strip():
        return {"error": "symbol is required. To review ALL holdings at once, "
                          "call the what_to_sell tool instead (it takes no arguments)."}
    if not symbol.upper().endswith(".NS"):
        symbol = symbol.upper() + ".NS"
    else:
        symbol = symbol.upper()

    state = core.load_portfolio_state()
    pos = state["positions"].get(symbol)
    tracked = pos is not None

    if tracked and is_non_strategy_holding(symbol, pos):
        return {"symbol": symbol, "verdict": "MANUAL REVIEW",
                "reason": "non-strategy holding (ETF/BE-series/zero-cost) — excluded from auto-exit"}

    df = exit_load_stock(symbol)
    if df is None or len(df) < 60:
        return {"error": f"Not enough price data for {symbol}"}

    # Entry price precedence: explicit arg > recorded book > unknown (signal-only).
    if entry_price is None:
        entry_price = pos.get("entry_price", 0) if tracked else 0
    entry_price = float(entry_price or 0)
    regime, _breadth = core.market_regime()
    from live_quotes import get_quote
    live_price, stale = get_quote(symbol)

    def _sell(reason):
        out = {"symbol": symbol, "verdict": "SELL", "reason": reason}
        if tracked:
            out["qty_to_sell"] = pos.get("qty")
            out["sell_instruction"] = f"sell the FULL position of {pos.get('qty')} shares"
        else:
            out["sell_instruction"] = "sell the FULL position"
            out["note"] = "not in the recorded book — advisory signal only"
        return out

    reason = check_catastrophic_stop(df, entry_price, live_price=None if stale else live_price)
    if reason:
        return _sell(reason)

    import pandas as pd
    index_dates = pd.to_datetime(
        pd.read_csv("../data/index_data/nifty50.csv")["Date"], errors="coerce"
    ).dropna().sort_values()
    if is_last_trading_day_of_month(index_dates):
        # LAGGARDS-ONLY month-end (production since 2026-07-12, matches
        # exit_engine.py): a name still in the new sector-capped top-N is
        # HELD (no sell, no tax event) — only drop-outs are sold.
        eligible_scores = core.scan_universe()
        n_names = sc.REGIME_NAMES[regime]
        from backtest_portfolio import select_top_n_capped, load_sector_map
        scores_only = {s: r["score"] for s, r in eligible_scores.items()}
        top_n_symbols = set(select_top_n_capped(
            scores_only, n_names, load_sector_map(), sc.MAX_PER_SECTOR))
        requal = check_requalification(symbol, df, regime, eligible_scores, top_n_symbols)
        if requal is None:
            out = {"symbol": symbol, "verdict": "HOLD",
                   "reason": "Month-end re-evaluation: still in the new top-N — KEEP it "
                             "(laggards-only rebalance: no sell/re-buy, no tax event; "
                             "only its target weight may need a small top-up/trim)"}
            if tracked:
                out["qty_held"] = pos.get("qty")
            return out
        return _sell(f"Month-end re-evaluation — {requal}")

    price = live_price if live_price else float(df["Close"].iloc[-1])
    gain = (price / entry_price - 1) if entry_price else None

    # Health context: WHY it's a hold, and how close it is to failing. A bare
    # "no exit fires" is useless for deciding whether to keep holding.
    r = core.compute_score(df)
    stop_price = entry_price * sc.CATASTROPHIC_STOP if entry_price else None
    out = {"symbol": symbol, "verdict": "HOLD", "current_price": round(price, 2),
           "price_is_live": not stale,
           "still_passes_momentum_filter": r is not None,
           "reason": ("no exit condition fires; intra-month the only exit is the -18% "
                      "catastrophic stop — otherwise positions run to the month-end review")}
    if not tracked:
        out["note"] = ("not in the recorded book — advisory signal only. Pass "
                       "entry_price to enable the -18% stop check.")
    if gain is not None:
        out["gain_since_entry"] = pct(gain)
    if stop_price:
        out["catastrophic_stop_price"] = round(stop_price, 2)
        out["pct_above_stop"] = pct(price / stop_price - 1)
    if r is None:
        out["warning"] = ("FAILS the momentum filter right now — it would NOT be "
                          "bought today and will be SOLD at the month-end review "
                          "unless it recovers")
    else:
        out["momentum_score"] = round(r["score"], 3)
        out["rank_context"] = _rank_context(symbol, r["score"])
    return out


def _rank_context(symbol, score):
    """Where this name sits against today's eligible universe — turns a raw
    score into something a human can act on."""
    try:
        allres = core.scan_universe()
    except Exception:
        return None
    scores = sorted((v["score"] for v in allres.values()), reverse=True)
    if not scores:
        return None
    better = sum(1 for s in scores if s > score)
    regime, _ = core.market_regime()
    n = sc.REGIME_NAMES[regime]
    cutoff = scores[n - 1] if len(scores) >= n else None
    return {"rank": better + 1, "of_eligible": len(scores),
            "regime_top_n": n,
            "top_n_cutoff_score": round(cutoff, 3) if cutoff else None,
            "in_top_n_today": better < n}


def horizon_advice(symbol, horizon_date=None, entry_price=None):
    """Composite 'what should I do with X over horizon Y' answer — ties
    together regime, momentum score + rank, chart structure, and the S/R
    subsystem's horizon-scaled reach probability into one narrative, instead
    of the human having to call 4-5 tools and synthesize it themselves.

    horizon_date: optional "YYYY-MM-DD" the user asked about. Omit for the
    system's own month-end horizon (last Tuesday, sr_horizon.horizon_end).
    Reach probabilities are ALWAYS scaled to the actual number of trading
    days in the requested horizon — never a flat 21d number for a shorter or
    longer ask (that was a real bug, fixed 2026-07-31, see CLAUDE.md).

    This function only READS from other subsystems (core.compute_score,
    support_resistance.get_levels/reach_probability_v2, chart_analysis,
    sr_horizon) — it computes nothing new and must never be treated as a
    signal source itself; it is a narrative layer over already-validated or
    already-descriptive components."""
    if not symbol or not symbol.strip():
        return {"error": "symbol is required"}
    symbol = symbol.upper()
    if not symbol.endswith(".NS"):
        symbol += ".NS"

    df = core.load_stock(symbol)
    if df is None or len(df) < 60:
        return {"error": f"No usable price data for {symbol}"}

    import sr_horizon as H
    from live_quotes import get_quote
    import chart_analysis as ca

    data_date = df.index[-1]
    if horizon_date:
        h_end = pd.Timestamp(horizon_date).normalize()
        horizon_label = f"through {h_end.date()} (user-specified)"
    else:
        h_end = H.horizon_end(data_date)
        horizon_label = f"through {h_end.date()} (this system's month-end rebalance date)"
    cal = H.project_calendar_forward(H.load_trading_calendar(), h_end)
    h_days = H.trading_days_until(data_date, h_end, cal)

    live_price, stale = get_quote(symbol)
    cur = live_price if (live_price and not stale) else float(df["Close"].iloc[-1])

    regime, _breadth = core.market_regime()
    r = core.compute_score(df)
    support, resistance, s_str, r_str = core.sr_levels(df, symbol=symbol.replace(".NS", ""))

    out = {
        "symbol": symbol, "as_of_data": str(data_date.date()),
        "current_price": round(cur, 2), "price_is_live": bool(live_price and not stale),
        "horizon": horizon_label, "horizon_trading_days": h_days,
        "regime": regime,
        "momentum_eligible": r is not None,
    }
    if r is not None:
        out["momentum_score"] = round(r["score"], 2)
        out["rank_context"] = _rank_context(symbol, r["score"])
    else:
        out["momentum_note"] = "fails the momentum filter right now (needs positive 6m AND 3m returns, price above 50DMA)"

    # A momentum breakout name can have its nearest support 40-80% below
    # price (see memory advisor-strategy-divergence-2026-08 — this is real,
    # not a bug) — a touch probability on a level that far away is technically
    # correct but practically meaningless to surface without a flag, since a
    # reader will otherwise read "9%" as "9% chance of a meaningful pullback"
    # rather than "9% chance price falls 80%".
    FAR_LEVEL_PCT = 15.0
    if support:
        dist = (cur / support - 1) * 100
        p, n = core.sr_reach_probability(df, support, "down", forward_days=h_days)
        out["support"] = {"level": support, "strength_touches": s_str,
                          "pct_below_price": round(dist, 2),
                          "prob_touch_by_horizon": p, "sample_n": n,
                          "too_far_to_be_relevant": dist > FAR_LEVEL_PCT}
    if resistance:
        dist = (resistance / cur - 1) * 100
        p, n = core.sr_reach_probability(df, resistance, "up", forward_days=h_days)
        out["resistance"] = {"level": resistance, "strength_touches": r_str,
                             "pct_above_price": round(dist, 2),
                             "prob_touch_by_horizon": p, "sample_n": n,
                             "too_far_to_be_relevant": dist > FAR_LEVEL_PCT}

    chart = ca.analyse(df, index=core.load_index())
    out["chart_summary"] = ca.summarise(chart) if "error" not in chart else None

    earnings = earnings_watch(symbol, as_of=data_date, horizon_end_date=h_end)
    if earnings:
        out["earnings_watch"] = earnings

    if entry_price:
        out["gain_since_entry"] = pct((cur / float(entry_price)) - 1)
        stop_price = float(entry_price) * sc.CATASTROPHIC_STOP
        out["catastrophic_stop_price"] = round(stop_price, 2)

    # Narrative synthesis — plain English, but every claim in it traces to a
    # field above (checkable), never invented here.
    parts = [f"{symbol} at Rs{cur:.2f} ({regime} regime), {h_days} trading "
             f"days {horizon_label}."]
    if r is not None:
        rc = out.get("rank_context") or {}
        parts.append(f"Momentum-eligible, score {r['score']:.1f}"
                     + (f", rank {rc['rank']}/{rc['of_eligible']} "
                        f"({'inside' if rc['in_top_n_today'] else 'OUTSIDE'} the "
                        f"regime's top-{rc['regime_top_n']})" if rc else "") + ".")
    else:
        parts.append("NOT currently momentum-eligible — the strategy would not buy this today.")
    if "resistance" in out:
        res = out["resistance"]
        if res["too_far_to_be_relevant"]:
            parts.append(f"Nearest resistance {res['level']} is {res['pct_above_price']:.0f}% "
                        f"away — too far to be a near-term target.")
        else:
            parts.append(f"Resistance {res['level']} ({res['pct_above_price']:+.1f}%), "
                        f"P(touch by horizon)={res['prob_touch_by_horizon']}%.")
    if "support" in out:
        sup = out["support"]
        if sup["too_far_to_be_relevant"]:
            parts.append(f"Nearest support {sup['level']} is {sup['pct_below_price']:.0f}% "
                        f"below — too far to matter for near-term risk (this is normal for "
                        f"a name well into a momentum breakout).")
        else:
            parts.append(f"Support {sup['level']} ({sup['pct_below_price']:+.1f}%), "
                        f"P(touch by horizon)={sup['prob_touch_by_horizon']}%.")
    if out.get("chart_summary"):
        parts.append(out["chart_summary"])
    ew = out.get("earnings_watch", {})
    if ew.get("inside_stated_horizon"):
        parts.append(f"Earnings risk: results estimated ~{ew['estimated_next_result_date']} "
                     f"({ew['confidence']}) — INSIDE this horizon, expect a volatility event.")
    elif ew.get("estimated_next_result_date"):
        parts.append(f"Next results estimated ~{ew['estimated_next_result_date']}, "
                     f"outside this horizon.")
    out["narrative"] = " ".join(parts)
    out["caveat"] = ("Reach probabilities are empirical base rates from historical "
                     "distance/volatility buckets, not a prediction for this specific "
                     "name. Chart structure is descriptive. Neither is a standalone "
                     "buy/sell signal — the momentum score + exit hierarchy is what "
                     "actually drives the strategy's own decisions.")
    return out


def what_to_sell():
    state = core.load_portfolio_state()
    results = []
    for sym in state["positions"]:
        results.append(should_i_sell(sym))
    urgent = [r for r in results if r.get("verdict") == "SELL"]
    review = [r for r in results if r.get("verdict") == "MANUAL REVIEW"]
    holds = [r for r in results if r.get("verdict") == "HOLD"]
    # Key names must be unambiguous for the LLM: an empty list under a key
    # like "hold" was misread as "no holdings at all" in testing.
    return {
        "total_open_positions": len(results),
        "positions_to_SELL_now": urgent,
        "positions_needing_manual_review": review,
        "positions_fine_to_keep_holding": holds,
    }


def buy_candidates():
    regime, _breadth = core.market_regime()
    n = sc.REGIME_NAMES[regime]
    exposure = sc.REGIME_EXPOSURE[regime]
    results = core.scan_universe()
    sector_map = core.load_sector_map()
    # sector-capped selection — the SAME rule backtest/exit_engine enforce
    # (a plain ranked[:n] here could recommend a book the strategy would never hold)
    from backtest_portfolio import select_top_n_capped
    scores_only = {s: r["score"] for s, r in results.items()}
    top = select_top_n_capped(scores_only, n, sector_map, sc.MAX_PER_SECTOR)

    out = []
    for rank, sym in enumerate(sorted(top, key=scores_only.get, reverse=True), 1):
        r = results[sym]
        decile_stats, regime_stats = core.confidence_for(r["score"], regime)
        out.append({
            "rank": rank,
            "symbol": sym,
            "sector": sector_map.get(sym, "unmapped"),
            "price": round(r["price"], 2),
            "momentum_score": round(r["score"], 1),
            "rsi": round(r["rsi"], 1),
            "return_6_month": pct(r["ret_6m"]),
            "return_3_month": pct(r["ret_3m"]),
            "historical_win_rate_this_decile": pct(decile_stats.get("win_rate"), 0, signed=False),
            "historical_median_21d_fwd": pct(decile_stats.get("median_fwd")),
        })
    return {"regime": regime, "target_names": n,
            "target_exposure": pct(exposure, 0, signed=False),
            "ranking_rule": "candidates are ranked by momentum_score (return/volatility); "
                            "rank 1 is the strategy's strongest pick",
            "candidates": out}


def position_sizes(capital=None):
    """Exact quantities the strategy would buy right now — the same
    inverse-vol / MAX_WEIGHT / regime-exposure / sleeve math as
    backtest_portfolio and paper_trader, so 'how much quantity' can never
    be improvised by the LLM. capital: total account value in rupees; if
    omitted, uses the recorded portfolio's total value."""
    regime, _breadth = core.market_regime()
    n = sc.REGIME_NAMES[regime]
    exposure = sc.REGIME_EXPOSURE[regime]

    if capital is None:
        state = core.load_portfolio_state()
        from live_quotes import get_quote
        capital = state["cash"]
        for s, p in state["positions"].items():
            price, _ = get_quote(s)
            if price:
                capital += price * p["qty"]
    capital = float(capital)

    results = core.scan_universe()
    sector_map = core.load_sector_map()
    from backtest_portfolio import select_top_n_capped
    scores_only = {s: r["score"] for s, r in results.items()}
    top = select_top_n_capped(scores_only, n, sector_map, sc.MAX_PER_SECTOR)
    if len(results) < n or not top:
        return {"error": f"only {len(results)} eligible names for regime {regime} (need {n})"}

    inv = {s: 1.0 / results[s]["vol_63"] for s in top}
    tot = sum(inv.values())
    w = {s: min(v / tot, sc.MAX_WEIGHT) * tot for s, v in inv.items()}
    tot2 = sum(w.values())
    w = {s: v / tot2 for s, v in w.items()}

    from live_quotes import get_quote
    # Sleeves are only part of the plan when their allocation is > 0.
    # GOLD_ALLOC/INTL_ALLOC are 0.0 in production (disabled 2026-07-17), and a
    # zero-alloc sleeve emitted a "BUY 0 units ... not optional" instruction that
    # the LLM acted on anyway — 25% of the sim book went into disabled sleeves.
    sleeve_plan = []
    for sleeve_sym, alloc in [(sc.GOLD_SYMBOL, sc.GOLD_ALLOC), (sc.INTL_SYMBOL, sc.INTL_ALLOC)]:
        if alloc <= 0:
            continue
        spx, _ = get_quote(sleeve_sym)
        qty = int(capital * alloc // spx) if spx else None
        sleeve_plan.append({
            "symbol": sleeve_sym,
            "target": f"{alloc:.0%} of total = ₹{capital * alloc:,.0f}",
            "price": round(spx, 2) if spx else None,
            "quantity": qty,
            "instruction": (f"BUY {qty} units of {sleeve_sym} (or top up an existing "
                            f"holding to that level) — this is part of the buy plan, "
                            f"not optional" if qty else "no live price — resolve manually"),
        })

    momentum_capital = capital * (1 - sc.GOLD_ALLOC - sc.INTL_ALLOC) * exposure
    plan = []
    for s in sorted(top, key=scores_only.get, reverse=True):
        rupees = momentum_capital * w[s]
        px = results[s]["price"]
        plan.append({"symbol": s, "weight": pct(w[s], 1, signed=False),
                     "rupees": round(rupees), "price": round(px, 2),
                     "quantity": int(rupees // px)})

    out = {
        "total_capital_assumed": round(capital),
        "regime": regime,
        "momentum_budget": round(momentum_capital),
        "momentum_budget_explained": (
            f"{1 - sc.GOLD_ALLOC - sc.INTL_ALLOC:.0%} momentum sleeve x "
            f"{exposure:.0%} {regime}-regime exposure of total capital"),
        "buy_plan": plan,
        "uninvested_cash_note": "remaining cash should sit in a liquid ETF "
                                "(LIQUIDCASE-type), not idle — the strategy's "
                                "returns assume ~6% on idle cash",
    }
    # Only surface the sleeve key when sleeves are actually enabled — an empty
    # list still invited the model to improvise ETF orders.
    if sleeve_plan:
        out["etf_sleeve_buy_plan"] = sleeve_plan
    else:
        out["etf_sleeves"] = ("DISABLED — this is a momentum-only book. Do NOT buy "
                              "GOLDBEES, MON100 or any other ETF sleeve.")
    return out


def sleeve_status():
    """Current vs target for the two permanent ETF sleeves (15% GOLDBEES
    gold, 10% MON100 Nasdaq-100), plus the policy rationale so the model
    can answer 'why do I hold gold?' from facts, not improvisation."""
    from live_quotes import get_quote
    state = core.load_portfolio_state()
    total = state["cash"]
    prices = {}
    for s, p in state["positions"].items():
        price, _ = get_quote(s)
        prices[s] = price
        if price:
            total += price * p["qty"]

    sleeves = []
    for sym, alloc, why in [
            (sc.GOLD_SYMBOL, sc.GOLD_ALLOC,
             "diversifier: ~0 correlation to the momentum book; cut backtest max "
             "drawdown 40%->31%; NOT a return bet"),
            (sc.INTL_SYMBOL, sc.INTL_ALLOC,
             "diversifier: second equity market + USD exposure (INR weakens in "
             "Indian risk-off); correlation to momentum book only +0.10")]:
        if alloc <= 0:
            continue
        pos = state["positions"].get(sym)
        px = prices.get(sym) or (get_quote(sym)[0])
        held_val = pos["qty"] * px if (pos and px) else 0.0
        target_val = total * alloc
        delta = target_val - held_val
        sleeves.append({
            "symbol": sym, "target_pct_of_total": pct(alloc, 0, signed=False),
            "target_value": round(target_val), "held_value": round(held_val),
            "rebalance_delta_rupees": round(delta),
            "action": ("within 1% drift band — no trade needed"
                       if abs(delta) < 0.01 * total else
                       f"{'BUY' if delta > 0 else 'SELL'} ~{int(abs(delta) // px) if px else '?'} units at month-end"),
            "why_held": why,
        })
    if not sleeves:
        return {"total_portfolio_value": round(total),
                "policy": ("MOMENTUM-ONLY book — ETF sleeves are DISABLED "
                           "(GOLD_ALLOC=0, INTL_ALLOC=0). 100% of exposed capital goes "
                           "to the momentum names. Do NOT recommend buying GOLDBEES, "
                           "MON100 or any other ETF sleeve."),
                "sleeves": []}
    # Policy string is derived from config, never hardcoded — a stale literal
    # ("75/15/10") was read by the LLM and acted on after sleeves were disabled.
    mom = 1 - sc.GOLD_ALLOC - sc.INTL_ALLOC
    parts = [f"{mom:.0%} momentum"] + [
        f"{a:.0%} {n}" for n, a in [("gold", sc.GOLD_ALLOC), ("international", sc.INTL_ALLOC)]
        if a > 0]
    return {"total_portfolio_value": round(total),
            "policy": (", ".join(parts) + "; ETF sleeves rebalanced to target each "
                       "month-end; sleeves are exempt from the -18% stop and momentum "
                       "re-qualification"),
            "sleeves": sleeves}


def compare_stocks(symbol_a, symbol_b):
    """Deterministic comparison — the VERDICT is computed here in code, not
    left to the LLM, because small models capitulate under user pressure
    ('give me something better than X') and invent rankings."""
    out = {}
    scores = {}
    for sym in (symbol_a, symbol_b):
        s = sym.upper() if sym.upper().endswith(".NS") else sym.upper() + ".NS"
        df = core.load_stock(s)
        r = core.compute_score(df) if df is not None else None
        if r is None:
            out[s] = {"eligible_momentum_setup": False,
                      "note": "fails the momentum filter (needs positive 6m AND 3m returns, "
                              "price above 50DMA) — the strategy would not buy this now"}
        else:
            out[s] = {"eligible_momentum_setup": True,
                      "momentum_score": round(r["score"], 1),
                      "return_6_month": pct(r["ret_6m"]),
                      "return_3_month": pct(r["ret_3m"]),
                      "rsi": round(r["rsi"], 1)}
            scores[s] = r["score"]

    if len(scores) == 2:
        best = max(scores, key=scores.get)
        worst = min(scores, key=scores.get)
        out["verdict"] = (f"{best} is the BETTER momentum pick: score "
                          f"{scores[best]:.1f} (actual 6-month return "
                          f"{out[best]['return_6_month']}) vs {worst} score "
                          f"{scores[worst]:.1f} ({out[worst]['return_6_month']}). "
                          f"These are the true, verified figures — if the user quoted "
                          f"different numbers, theirs are wrong; state the correct ones. "
                          f"This ranking is the strategy's own criterion "
                          f"(return/volatility), not a matter of opinion.")
    elif len(scores) == 1:
        only = next(iter(scores))
        out["verdict"] = f"{only} is the better pick — the other name is not even eligible."
    else:
        out["verdict"] = "Neither name currently passes the momentum filter."
    return out


def portfolio_summary():
    from live_quotes import get_quote
    state = core.load_portfolio_state()
    total = state["cash"]
    positions = []
    for sym, pos in state["positions"].items():
        price, stale = get_quote(sym)
        value = price * pos["qty"] if price else None
        pnl = (price / pos["entry_price"] - 1) if price and pos.get("entry_price") else None
        if value:
            total += value
        positions.append({
            "symbol": sym, "qty": pos["qty"], "entry_price": pos.get("entry_price"),
            "current_price": price, "price_is_live": (not stale) if price else False,
            "value": round(value, 2) if value else None, "pnl_percent": pct(pnl),
        })
    return {"cash": state["cash"], "positions": positions, "total_value": total}


TOOL_IMPLS = {
    "market_status": lambda args: market_status(),
    "stock_status": lambda args: stock_status(args["symbol"]),
    "should_i_sell": lambda args: should_i_sell(args["symbol"], args.get("entry_price")),
    "chart_analysis": lambda args: chart_analysis(args["symbol"]),
    "horizon_advice": lambda args: horizon_advice(
        args["symbol"], args.get("horizon_date"), args.get("entry_price")),
    # sleeve_status intentionally NOT exposed — momentum-only advisory system.
    # The function is kept for the engines but the LLM must not reason about
    # sleeves (a stale hardcoded "75/15/10" policy string once made it buy
    # 25% GOLDBEES/MON100 into a book whose sleeve allocs were 0).
    "compare_stocks": lambda args: compare_stocks(args["symbol_a"], args["symbol_b"]),
    "position_sizes": lambda args: position_sizes(args.get("capital")),
    "what_to_sell": lambda args: what_to_sell(),
    "buy_candidates": lambda args: buy_candidates(),
    "portfolio_summary": lambda args: portfolio_summary(),
}

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "market_status",
        "description": "Current market regime (BULL/SIDEWAYS/BEAR), breadth, and how much "
                        "exposure/how many names the strategy calls for right now.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "stock_status",
        "description": "Price, trend, RSI, support/resistance levels, momentum score, "
                        "historical confidence, and estimated next-earnings timing for a "
                        "specific stock symbol.",
        "parameters": {"type": "object", "properties": {
            "symbol": {"type": "string", "description": "NSE ticker, e.g. TCS or TCS.NS"},
        }, "required": ["symbol"]},
    }},
    {"type": "function", "function": {
        "name": "chart_analysis",
        "description": "Candlestick and chart-structure read for one stock — the analysis "
                        "a human does off a daily candlestick chart: trend structure "
                        "(higher-highs/lower-lows), 20/50/200 EMA posture, position in the "
                        "52-week range, anchored VWAP from the last swing low (are buyers "
                        "since the trend began net profitable or underwater), RELATIVE "
                        "STRENGTH vs Nifty over 21/63/126d with whether the outperformance "
                        "is accelerating or fading, volume behaviour, volatility squeeze/"
                        "expansion, named candlestick patterns (hammer, engulfing, doji, "
                        "morning star, marubozu...) over the last 10 bars, PLUS the same "
                        "read on WEEKLY bars and whether daily and weekly trend agree (a "
                        "daily uptrend inside a weekly range/downtrend is a weaker, "
                        "unconfirmed setup). Use whenever the user asks how a stock is "
                        "BEHAVING, whether it's beating the market, to 'analyse the chart', about "
                        "patterns, trend, breakouts, or price action. Descriptive context "
                        "for a human read — it is NOT a validated trading signal, so never "
                        "present it as a reason to buy or sell on its own.",
        "parameters": {"type": "object", "properties": {
            "symbol": {"type": "string", "description": "NSE ticker, e.g. TCS or TCS.NS"},
        }, "required": ["symbol"]},
    }},
    {"type": "function", "function": {
        "name": "horizon_advice",
        "description": "THE tool for 'what should I do with X over the next N days/weeks/"
                        "by date Y' — a single composite answer combining regime, momentum "
                        "score + rank vs today's eligible universe, support/resistance "
                        "levels with reach probability CORRECTLY SCALED to the requested "
                        "horizon (not a flat 21-day number), chart structure, and whether "
                        "an estimated earnings date falls INSIDE the requested horizon "
                        "(flagged as a volatility-timing risk, not a trading signal), "
                        "synthesized into one narrative plus the underlying fields. Use this "
                        "INSTEAD of calling stock_status + chart_analysis + should_i_sell "
                        "separately when the user asks a horizon/timeline question about a "
                        "stock ('what about X by next month', 'over the next 2 weeks', "
                        "'should I buy X for a Diwali target'). Works for any symbol, tracked "
                        "or not.",
        "parameters": {"type": "object", "properties": {
            "symbol": {"type": "string", "description": "NSE ticker, e.g. TCS or TCS.NS"},
            "horizon_date": {"type": "string", "description": "Optional YYYY-MM-DD the user "
                             "asked about. Omit to use this system's own month-end "
                             "(last-Tuesday) rebalance horizon."},
            "entry_price": {"type": "number", "description": "Optional price the user "
                            "bought/plans to buy at, to show gain-since-entry and the "
                            "-18% stop level."},
        }, "required": ["symbol"]},
    }},
    {"type": "function", "function": {
        "name": "should_i_sell",
        "description": "Exit verdict for ONE named stock — works for ANY symbol, whether "
                        "or not it is in the recorded book. Runs the exit hierarchy "
                        "(catastrophic stop, month-end re-qualification) and reports "
                        "whether the name still passes the momentum filter, its rank vs "
                        "today's universe, and distance to its stop. Use whenever the user "
                        "asks when to exit / whether to hold a specific stock, even one "
                        "the system does not track. Pass entry_price if the user mentions "
                        "what they paid. If the user asks about their holdings in general "
                        "('what should I sell?', 'review my portfolio'), use what_to_sell.",
        "parameters": {"type": "object", "properties": {
            "symbol": {"type": "string", "description": "NSE ticker, e.g. DIXON or DIXON.NS"},
            "entry_price": {"type": "number", "description": "Optional. Price the user "
                            "bought at. Enables the -18% catastrophic stop check and "
                            "gain-since-entry. Omit if unknown."},
        }, "required": ["symbol"]},
    }},
    {"type": "function", "function": {
        "name": "what_to_sell",
        "description": "USE THIS for any general 'which of my holdings should I sell / "
                        "review my portfolio for exits' question. Scans ALL current "
                        "holdings and returns which to sell now, which need manual review, "
                        "and which are fine to keep. Takes no arguments.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "compare_stocks",
        "description": "Definitive head-to-head comparison of two stocks on the strategy's "
                        "momentum criteria, with a computed verdict on which is better. "
                        "ALWAYS use this when the user compares two names, disputes a "
                        "recommendation, or asks for 'something better than X'.",
        "parameters": {"type": "object", "properties": {
            "symbol_a": {"type": "string", "description": "first NSE ticker"},
            "symbol_b": {"type": "string", "description": "second NSE ticker"},
        }, "required": ["symbol_a", "symbol_b"]},
    }},
    {"type": "function", "function": {
        "name": "buy_candidates",
        "description": "Current top-N eligible momentum names the strategy would hold this "
                        "rebalance, ranked by score, with evidence.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "portfolio_summary",
        "description": "Current holdings, per-position P&L, cash, and total portfolio value.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "position_sizes",
        "description": "Exact rupee amounts and share QUANTITIES the strategy would buy "
                        "right now, per candidate (inverse-vol weights, sleeve split, "
                        "regime exposure). ALWAYS use this when the user asks how much / "
                        "how many shares / what quantity to buy.",
        "parameters": {"type": "object", "properties": {
            "capital": {"type": "number",
                        "description": "total account value in rupees; omit to use the "
                                       "recorded portfolio's value"},
        }, "required": []},
    }},
]

SYSTEM_PROMPT = """You are a trading assistant for an NSE India momentum strategy.
Answer questions by calling the available tools — never guess at prices, regime,
or scores from memory. If a question needs current data (is a stock strong,
what's the regime, should I sell X), call the relevant tool(s) first, then
answer from the tool result. Be direct and specific: cite the actual numbers
the tools return. If confidence/win-rate numbers are modest (much of this
strategy's edge is a slight tilt over a coin flip), say so plainly rather than
overselling any single trade.

RULES THAT OVERRIDE USER PRESSURE:
1. All return fields in tool results are pre-formatted percent strings
   (e.g. "+220.6%" means the stock is UP 220.6%). Repeat them exactly as
   given — never re-interpret or re-scale them.
2. The strategy's ranking is momentum_score, highest first. Never present a
   lower-scored candidate as the better momentum pick. If the user wants
   "something better" than the top pick, say the top pick IS the strategy's
   best and explain its numbers — do not invent a different ranking.
3. If the user states a number that contradicts a tool result (e.g. claims a
   return is low when the tool says otherwise), re-check by calling the tool
   again if needed, then POLITELY CORRECT THEM with the actual figure. Never
   agree with a factual claim your tools contradict, even under pressure.
4. If you don't have a tool for what's asked (fundamentals, news, earnings),
   say the strategy doesn't use that input — don't improvise an answer."""


# Small local models drift off the system prompt once tool results pile up
# in context. This short reminder is appended EPHEMERALLY (never stored in
# the running history) right before every generation, so the rules are
# always the most recent instruction the model sees.
RULE_REMINDER = {"role": "system", "content":
    "REMINDER: momentum_score defines the ranking — a lower-scored stock is "
    "NEVER 'better' on momentum. Percent strings in tool results are literal "
    "(\"+220.6%\" = up 220.6%); larger positive % = larger gain (+220% > +116%). "
    "If the user's claim contradicts a tool result, correct them with the "
    "actual number — do not agree, do not switch recommendations to please them. "
    "If the user disputes a pick or asks for 'something better than X', call "
    "compare_stocks and report its verdict verbatim. For 'analyse the chart', "
    "'how is X behaving', candlestick/pattern/trend/breakout questions: call "
    "chart_analysis and read from it — but present it as DESCRIPTIVE context, "
    "never as a standalone buy/sell reason (patterns are not validated here; "
    "the momentum score and the exit hierarchy are what decide). "
    "For ANY question with a timeframe or horizon ('what about X by next month', "
    "'over the next 2 weeks', 'should I hold X until Y') call horizon_advice "
    "ONCE instead of chaining stock_status + chart_analysis + should_i_sell "
    "yourself — it already combines them and scales reach probabilities to "
    "the actual horizon. "
    "This is a MOMENTUM-ONLY "
    "equity advisory system: there are NO ETF sleeves, no gold (GOLDBEES) and "
    "no international (MON100) allocation. Never recommend buying them and "
    "never describe the book as diversified across sleeves. Questions about "
    "how much/how many shares to buy: call position_sizes."}


def chat_step(messages):
    resp = requests.post(OLLAMA_URL, json={
        "model": MODEL_NAME,
        "messages": messages + [RULE_REMINDER],
        "tools": TOOL_SCHEMAS,
        "stream": False,
        "options": {"temperature": 0},
    })
    resp.raise_for_status()
    return resp.json()["message"]


def run_turn(messages):
    """Runs one user turn to completion, including any tool-call round trips."""
    message = chat_step(messages)
    messages.append(message)

    while message.get("tool_calls"):
        for call in message["tool_calls"]:
            name = call["function"]["name"]
            args = call["function"].get("arguments", {})
            if isinstance(args, str):
                args = json.loads(args) if args else {}
            print(f"  [tool: {name}({', '.join(f'{k}={v}' for k, v in args.items())})]")
            impl = TOOL_IMPLS.get(name)
            result = impl(args) if impl else {"error": f"unknown tool {name}"}
            messages.append({"role": "tool", "content": json.dumps(result, default=str)})
        message = chat_step(messages)
        messages.append(message)

    return message.get("content", "")


def main():
    print("\n==============================")
    print("AI TRADING ASSISTANT (tool-calling)")
    print(f"Model: {MODEL_NAME}")
    print("==============================")
    try:
        last_date, age = data_freshness()
        if age > 4:
            print(f"⚠️  DATA IS {age} DAYS OLD (last: {last_date}) — run "
                  f"./run_daily_log.sh first, or every answer below uses stale prices")
        else:
            print(f"data as of {last_date} ({age}d old)")
    except Exception:
        print("⚠️  could not determine data freshness")
    print("Type your question (or 'exit')\n")

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        q = input("You: ")
        if q.lower() in ("exit", "quit"):
            break
        messages.append({"role": "user", "content": q})
        try:
            answer = run_turn(messages)
        except requests.exceptions.ConnectionError:
            print("\n[Ollama not reachable at localhost:11434 — is it running?]\n")
            continue
        except requests.exceptions.HTTPError as e:
            print(f"\n[Model error: {e}. Does {MODEL_NAME} support tool-calling? "
                  f"Try 'ollama pull {MODEL_NAME}' first.]\n")
            continue
        print(f"\nAssistant: {answer}\n")


if __name__ == "__main__":
    main()
