"""Candlestick / chart-structure analysis — the read a human does off a daily chart.

Built 2026-08-01. The system had momentum scores and S/R levels but nothing that
answers "how is this stock BEHAVING on the chart right now" — candle patterns,
trend structure, moving-average posture, volume behaviour, volatility state.

DESIGN RULES (this is an ADVISORY module, not a signal source):
  - Everything here is DESCRIPTIVE and returned to the human/LLM as context.
    NOTHING in this file may be wired into exit_engine, paper_trader, agent_sim
    or the momentum scorer. Candlestick patterns as a TRADING rule have not been
    walk-forward validated on this strategy, and every auxiliary overlay tested
    so far (delivery%, OI/PCR, announcements, resistance-fade, trailing stops)
    has been REJECTED. Treat this as chart literacy for the reader, not alpha.
  - Pure functions of an OHLCV DataFrame; no I/O, no state, no network.
  - Patterns are reported with the bar index/date so a claim is checkable.

Run standalone:  python chart_analysis.py TCS
"""
import sys

import numpy as np
import pandas as pd


# ---------- helpers ----------

def _body(o, c):
    return abs(c - o)


def _range(h, l):
    r = h - l
    return r if r > 0 else np.nan


def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def resample_weekly(df):
    """Daily OHLCV -> weekly bars (week ending Friday, NSE's trading week).
    Every function in this module takes a plain OHLC(V) DataFrame, so the
    SAME trend/MA/pattern logic runs unchanged on the resampled frame — no
    separate weekly-specific code path to drift out of sync with the daily one."""
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    if "Volume" in df.columns:
        agg["Volume"] = "sum"
    w = df.resample("W-FRI").agg(agg).dropna(subset=["Close"])
    return w


# ---------- candlestick patterns ----------
#
# Each detector takes the OHLC frame and an index i (the bar being classified)
# and returns a short label or None. Thresholds are the conventional ones;
# they are deliberately not tuned to this universe (tuning them on our own
# history is exactly the curve-fit this project guards against).

def _doji(o, h, l, c):
    rng = _range(h, l)
    return "DOJI" if rng and _body(o, c) <= 0.1 * rng else None


def _hammer(o, h, l, c):
    rng = _range(h, l)
    if not rng:
        return None
    body = _body(o, c)
    lower = min(o, c) - l
    upper = h - max(o, c)
    if body <= 0.35 * rng and lower >= 2 * body and upper <= 0.25 * rng:
        return "HAMMER"          # long lower wick — rejection of lower prices
    if body <= 0.35 * rng and upper >= 2 * body and lower <= 0.25 * rng:
        return "SHOOTING_STAR"   # long upper wick — rejection of higher prices
    return None


def _marubozu(o, h, l, c):
    rng = _range(h, l)
    if not rng or _body(o, c) < 0.9 * rng:
        return None
    return "MARUBOZU_BULL" if c > o else "MARUBOZU_BEAR"


def _engulfing(po, pc, o, c):
    if pc < po and c > o and c >= po and o <= pc:
        return "BULLISH_ENGULFING"
    if pc > po and c < o and c <= po and o >= pc:
        return "BEARISH_ENGULFING"
    return None


def _star(df, i):
    """3-bar morning/evening star."""
    if i < 2:
        return None
    a, b, c_ = df.iloc[i - 2], df.iloc[i - 1], df.iloc[i]
    a_body = _body(a.Open, a.Close)
    b_body = _body(b.Open, b.Close)
    c_body = _body(c_.Open, c_.Close)
    if b_body > 0.5 * a_body:
        return None                      # middle bar must be small
    if a.Close < a.Open and c_.Close > c_.Open and c_.Close > (a.Open + a.Close) / 2:
        return "MORNING_STAR"
    if a.Close > a.Open and c_.Close < c_.Open and c_.Close < (a.Open + a.Close) / 2:
        return "EVENING_STAR"
    return None


PATTERN_MEANING = {
    "DOJI": "indecision — buyers and sellers balanced",
    "HAMMER": "lower wick rejected — buyers defended the low",
    "SHOOTING_STAR": "upper wick rejected — sellers defended the high",
    "MARUBOZU_BULL": "full-body up bar — one-directional buying",
    "MARUBOZU_BEAR": "full-body down bar — one-directional selling",
    "BULLISH_ENGULFING": "up bar swallowed the prior down bar — momentum flip up",
    "BEARISH_ENGULFING": "down bar swallowed the prior up bar — momentum flip down",
    "MORNING_STAR": "3-bar bottoming sequence",
    "EVENING_STAR": "3-bar topping sequence",
}

BULLISH = {"HAMMER", "MARUBOZU_BULL", "BULLISH_ENGULFING", "MORNING_STAR"}
BEARISH = {"SHOOTING_STAR", "MARUBOZU_BEAR", "BEARISH_ENGULFING", "EVENING_STAR"}


def detect_patterns(df, lookback=10):
    """Candlestick patterns on the last `lookback` bars, newest first."""
    out = []
    n = len(df)
    for i in range(max(1, n - lookback), n):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        found = []
        for fn in (_doji, _hammer, _marubozu):
            lbl = fn(row.Open, row.High, row.Low, row.Close)
            if lbl:
                found.append(lbl)
        lbl = _engulfing(prev.Open, prev.Close, row.Open, row.Close)
        if lbl:
            found.append(lbl)
        lbl = _star(df, i)
        if lbl:
            found.append(lbl)
        for lbl in found:
            out.append({
                "date": str(df.index[i].date()),
                "bars_ago": n - 1 - i,
                "pattern": lbl,
                "means": PATTERN_MEANING.get(lbl, ""),
                "bias": ("bullish" if lbl in BULLISH
                         else "bearish" if lbl in BEARISH else "neutral"),
            })
    out.reverse()
    return out


# ---------- trend structure ----------

def swing_points(df, window=5):
    """Local highs/lows: a bar higher/lower than `window` bars on both sides."""
    highs, lows = [], []
    h, l = df["High"].values, df["Low"].values
    for i in range(window, len(df) - window):
        if h[i] == max(h[i - window:i + window + 1]):
            highs.append(i)
        if l[i] == min(l[i - window:i + window + 1]):
            lows.append(i)
    return highs, lows


def trend_structure(df, window=5):
    """Higher-highs/higher-lows classification — the core of price-action reading."""
    highs, lows = swing_points(df, window)
    if len(highs) < 2 or len(lows) < 2:
        return {"structure": "INSUFFICIENT_SWINGS"}
    hh = df["High"].iloc[highs[-1]] > df["High"].iloc[highs[-2]]
    hl = df["Low"].iloc[lows[-1]] > df["Low"].iloc[lows[-2]]
    if hh and hl:
        s, meaning = "UPTREND", "higher highs and higher lows"
    elif not hh and not hl:
        s, meaning = "DOWNTREND", "lower highs and lower lows"
    else:
        s, meaning = "RANGE_OR_TRANSITION", "highs and lows disagree — no clean trend"
    return {
        "structure": s, "reading": meaning,
        "last_swing_high": round(float(df["High"].iloc[highs[-1]]), 2),
        "last_swing_high_date": str(df.index[highs[-1]].date()),
        "last_swing_low": round(float(df["Low"].iloc[lows[-1]]), 2),
        "last_swing_low_date": str(df.index[lows[-1]].date()),
    }


def ma_posture(df):
    """Where price sits vs the 20/50/200 EMAs, and whether they're stacked."""
    c = df["Close"]
    price = float(c.iloc[-1])
    out = {"price": round(price, 2)}
    mas = {}
    for span in (20, 50, 200):
        if len(c) < span:
            continue
        v = float(ema(c, span).iloc[-1])
        mas[f"ema{span}"] = round(v, 2)
        out[f"pct_vs_ema{span}"] = round((price / v - 1) * 100, 2)
    out.update(mas)
    if all(k in mas for k in ("ema20", "ema50", "ema200")):
        if mas["ema20"] > mas["ema50"] > mas["ema200"]:
            out["stack"] = "BULLISH (20>50>200)"
        elif mas["ema20"] < mas["ema50"] < mas["ema200"]:
            out["stack"] = "BEARISH (20<50<200)"
        else:
            out["stack"] = "MIXED — MAs not aligned"
    return out


def volume_behaviour(df, window=20):
    """Is the current move backed by volume, and is volume expanding?"""
    if "Volume" not in df or len(df) < window + 1:
        return {}
    v = df["Volume"].astype(float)
    avg = float(v.iloc[-window:].mean())
    last = float(v.iloc[-1])
    up = df["Close"].iloc[-1] >= df["Open"].iloc[-1]
    # volume on up-bars vs down-bars over the window (accumulation tell)
    recent = df.iloc[-window:]
    up_v = recent.loc[recent.Close >= recent.Open, "Volume"].sum()
    dn_v = recent.loc[recent.Close < recent.Open, "Volume"].sum()
    return {
        "last_volume": int(last),
        f"avg_volume_{window}d": int(avg),
        "vs_average": round(last / avg, 2) if avg else None,
        "today_bar": "up" if up else "down",
        "surge": bool(avg and last > 1.5 * avg),
        "up_vs_down_volume_ratio": round(float(up_v / dn_v), 2) if dn_v else None,
        "reading": ("volume expanding on an up bar — participation confirms"
                    if avg and last > 1.5 * avg and up else
                    "volume expanding on a down bar — distribution risk"
                    if avg and last > 1.5 * avg else
                    "volume near average — no unusual participation"),
    }


def volatility_state(df, window=20):
    """ATR% and whether range is compressing (squeeze) or expanding."""
    if len(df) < window * 2:
        return {}
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift()).abs(),
        (df["Low"] - df["Close"].shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(window).mean()
    price = float(df["Close"].iloc[-1])
    cur = float(atr.iloc[-1])
    prev = float(atr.iloc[-window])
    return {
        "atr": round(cur, 2),
        "atr_pct_of_price": round(cur / price * 100, 2) if price else None,
        "vs_prior_period": round(cur / prev, 2) if prev else None,
        "reading": ("range COMPRESSING — squeeze, often precedes a directional move"
                    if prev and cur < 0.8 * prev else
                    "range EXPANDING — volatility rising"
                    if prev and cur > 1.25 * prev else
                    "range stable"),
    }


def relative_strength(df, index, windows=(21, 63, 126)):
    """Stock return vs Nifty return over matching windows, PLUS whether the
    RS LINE itself (stock/index price ratio) is trending — the standard
    "is this actually outperforming, or just going up because everything is"
    read. A name up 20% in a market up 18% is barely outperforming even
    though the raw return looks strong; the RS line catches that where a raw
    return number alone doesn't.

    index: the Nifty Close series (core.load_index()), aligned to df's dates.
    Returns {} if there's insufficient overlapping history."""
    idx = index.reindex(df.index).ffill()
    out = {}
    for w in windows:
        if len(df) < w + 1 or idx.isna().iloc[-w-1:].any():
            continue
        stock_ret = df["Close"].iloc[-1] / df["Close"].iloc[-w-1] - 1
        index_ret = idx.iloc[-1] / idx.iloc[-w-1] - 1
        out[f"{w}d"] = {
            "stock_return": round(stock_ret * 100, 2),
            "nifty_return": round(index_ret * 100, 2),
            "relative_strength_pct": round((stock_ret - index_ret) * 100, 2),
        }
    if not out:
        return {}

    # RS line trend: is stock/index ratio itself making new highs, i.e. is
    # the OUTPERFORMANCE accelerating, not just the raw price.
    rs_line = (df["Close"] / idx).dropna()
    rs_trend = "INSUFFICIENT_DATA"
    if len(rs_line) >= 42:
        recent_high = rs_line.iloc[-21:].max()
        prior_high = rs_line.iloc[-42:-21].max()
        rs_trend = "IMPROVING" if recent_high > prior_high else "WEAKENING"

    primary = out.get("63d") or next(iter(out.values()))
    lead = primary["relative_strength_pct"] > 0
    out["rs_line_trend"] = rs_trend
    out["reading"] = (
        f"{'outperforming' if lead else 'underperforming'} Nifty by "
        f"{abs(primary['relative_strength_pct']):.1f}pp over the primary window, "
        f"RS line {rs_trend.lower()}"
        + (" — outperformance accelerating" if rs_trend == "IMPROVING" and lead else
           " — leadership fading" if rs_trend == "WEAKENING" and lead else
           " — still catching up" if rs_trend == "IMPROVING" and not lead else
           " — falling further behind" if rs_trend == "WEAKENING" and not lead else ""))
    return out


def position_in_range(df, window=252):
    """Where price sits in its 52-week range — momentum names live near the top."""
    w = df.iloc[-window:] if len(df) >= window else df
    hi, lo = float(w["High"].max()), float(w["Low"].min())
    price = float(df["Close"].iloc[-1])
    if hi == lo:
        return {}
    pos = (price - lo) / (hi - lo) * 100
    return {
        "range_high": round(hi, 2), "range_low": round(lo, 2),
        "pct_of_range": round(pos, 1),
        "pct_below_high": round((price / hi - 1) * 100, 2),
        "reading": ("at/near 52w highs — breakout territory" if pos > 90 else
                    "upper third of range" if pos > 66 else
                    "middle of range" if pos > 33 else
                    "lower third — weak" ),
    }


# ---------- anchored VWAP ----------
#
# Different construct from support_resistance.py's volume_profile_levels
# (a fixed 252-day HISTOGRAM of volume-by-price, used for S/R levels — that
# module is separately owned, already tuned, and not touched here). An
# ANCHORED VWAP is a running volume-weighted average price computed FROM a
# specific reference bar forward — the standard reading is "average cost
# basis of everyone who bought since that reference point"; price above it
# means the move since the anchor is, on net, profitable for participants.

def anchored_vwap(df, anchor_idx):
    """Volume-weighted average of typical price (H+L+C)/3 from anchor_idx to
    the last bar. Returns None if there's no Volume column or it's all zero
    over that span (illiquid/missing data — an unweighted average would be
    misleading, not just less precise)."""
    if "Volume" not in df.columns:
        return None
    sub = df.iloc[anchor_idx:]
    vol = sub["Volume"].astype(float)
    if vol.sum() <= 0:
        return None
    typical = (sub["High"] + sub["Low"] + sub["Close"]) / 3.0
    return float((typical * vol).sum() / vol.sum())


def anchored_vwap_from_last_swing_low(df, window=5):
    """AVWAP anchored at the most recent swing low — reads as 'average price
    paid by everyone who has bought since this trend attempt began'."""
    _, lows = swing_points(df, window)
    if not lows:
        return {}
    anchor_idx = lows[-1]
    avwap = anchored_vwap(df, anchor_idx)
    if avwap is None:
        return {}
    price = float(df["Close"].iloc[-1])
    pct = (price / avwap - 1) * 100
    return {
        "anchor_date": str(df.index[anchor_idx].date()),
        "anchor_reason": "most recent swing low",
        "avwap": round(avwap, 2),
        "pct_vs_avwap": round(pct, 2),
        "reading": (f"price is {pct:+.1f}% vs the volume-weighted average "
                   f"price since the last swing low ({df.index[anchor_idx].date()}) — "
                   + ("participants who bought since then are net profitable"
                      if pct > 0 else
                      "participants who bought since then are net underwater")),
    }


# ---------- top-level ----------

def _weekly_read(df, window=252 * 5 // 7):
    """Same trend/MA/range read as the daily one, on weekly-resampled bars.
    Needs ~60 weekly bars (~14 months) for trend_structure's swing points and
    ma_posture's 50-week EMA to be meaningful; returns None below that, so a
    recently-listed name just gets a daily-only read rather than a noisy one."""
    w = resample_weekly(df)
    if len(w) < 60:
        return None
    return {
        "as_of": str(w.index[-1].date()),
        "bars": len(w),
        "trend_structure": trend_structure(w),
        "moving_averages": ma_posture(w),
        "position_in_52w_range": position_in_range(w, window=window),
    }


def _timeframe_agreement(daily_ts, weekly_ts):
    """Do daily and weekly trend structure agree? The core of 'multi-timeframe
    confirmation' — a daily uptrend inside a weekly downtrend is a much weaker
    setup than one where both agree, even though this module makes no claim
    about win rates (that would need its own walk-forward study)."""
    d, w = daily_ts.get("structure"), weekly_ts.get("structure")
    if d is None or w is None or "INSUFFICIENT" in (d, w):
        return {"status": "INSUFFICIENT_DATA"}
    if d == w:
        return {"status": "ALIGNED", "reading": f"daily and weekly both {d}"}
    if {d, w} == {"UPTREND", "DOWNTREND"}:
        return {"status": "CONFLICTING",
                "reading": f"daily {d} inside a weekly {w} — counter-trend "
                          f"bounce/pullback, not confirmed by the higher timeframe"}
    return {"status": "MIXED", "reading": f"daily {d}, weekly {w}"}


def analyse(df, lookback_patterns=10, index=None):
    """Full chart read for one stock. DESCRIPTIVE ONLY — never a trade signal.

    index: optional Nifty Close series (core.load_index()) to enable
    relative_strength(). Omit to skip that section (this module otherwise
    does no I/O of its own — the caller supplies external data)."""
    if df is None or len(df) < 60:
        return {"error": "need at least 60 bars for a chart read"}
    df = df.copy()
    pats = detect_patterns(df, lookback_patterns)
    recent = [p for p in pats if p["bars_ago"] <= 3]
    bull = sum(1 for p in recent if p["bias"] == "bullish")
    bear = sum(1 for p in recent if p["bias"] == "bearish")

    daily_ts = trend_structure(df)
    weekly = _weekly_read(df)
    out = {
        "as_of": str(df.index[-1].date()),
        "trend_structure": daily_ts,
        "moving_averages": ma_posture(df),
        "position_in_52w_range": position_in_range(df),
        "anchored_vwap": anchored_vwap_from_last_swing_low(df),
        "relative_strength": relative_strength(df, index) if index is not None else {},
        "volume": volume_behaviour(df),
        "volatility": volatility_state(df),
        "candlestick_patterns_last_%dd" % lookback_patterns: pats,
        "recent_candle_bias": ("bullish" if bull > bear else
                               "bearish" if bear > bull else "neutral/mixed"),
        "caveat": ("Descriptive chart context for a human read. Candlestick "
                   "patterns are NOT a validated signal on this strategy and "
                   "must never drive automated entries or exits."),
    }
    if weekly is not None:
        out["weekly"] = weekly
        out["timeframe_agreement"] = _timeframe_agreement(daily_ts, weekly["trend_structure"])
    else:
        out["weekly"] = {"error": "insufficient history for a weekly read (need ~14 months)"}
    return out


def summarise(a):
    """One-paragraph plain-English read, for the LLM/CLI."""
    if "error" in a:
        return a["error"]
    ts = a["trend_structure"]
    ma = a["moving_averages"]
    rng = a.get("position_in_52w_range", {})
    parts = [f"Structure: {ts.get('structure')} ({ts.get('reading')})."]
    if "stack" in ma:
        parts.append(f"MAs {ma['stack']}; price {ma.get('pct_vs_ema50', 0):+.1f}% vs 50EMA.")
    if rng:
        parts.append(f"{rng['reading']} ({rng['pct_of_range']}% of 52w range, "
                     f"{rng['pct_below_high']:+.1f}% vs high).")
    avwap = a.get("anchored_vwap", {})
    if avwap.get("reading"):
        parts.append(avwap["reading"].capitalize() + ".")
    rs = a.get("relative_strength", {})
    if rs.get("reading"):
        parts.append(rs["reading"].capitalize() + ".")
    if a.get("volume", {}).get("reading"):
        parts.append(a["volume"]["reading"].capitalize() + ".")
    if a.get("volatility", {}).get("reading"):
        parts.append(a["volatility"]["reading"].capitalize() + ".")
    recent = [p for p in a.get("candlestick_patterns_last_10d", []) if p["bars_ago"] <= 3]
    if recent:
        parts.append("Recent candles: " + ", ".join(
            f"{p['pattern']} ({p['bars_ago']}d ago)" for p in recent[:3]) + ".")
    tf = a.get("timeframe_agreement", {})
    if tf.get("reading"):
        parts.append(f"Weekly check: {tf['reading']}.")
    return " ".join(parts)


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: python chart_analysis.py SYMBOL")
    from core import load_stock
    sym = sys.argv[1].upper()
    if not sym.endswith(".NS"):
        sym += ".NS"
    df = load_stock(sym)
    if df is None:
        sys.exit(f"no price data for {sym}")
    a = analyse(df)
    import json
    print(f"\n=== CHART ANALYSIS — {sym} ({a.get('as_of')}) ===\n")
    print(summarise(a))
    print()
    print(json.dumps(a, indent=1, default=str))


if __name__ == "__main__":
    main()
