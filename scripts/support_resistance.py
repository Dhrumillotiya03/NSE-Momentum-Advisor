import os, sys
import numpy as np
import pandas as pd

PRICE_DIR    = "../data/price_data/"
DELIVERY_DIR = "../data/delivery_data/"
ETF_DIR      = "../data/etf_data/"


# ──────────────────────────────────────────────
# DATA LOADERS
# ──────────────────────────────────────────────

def load_stock(symbol):
    path = os.path.join(PRICE_DIR, f"{symbol}.csv")
    if not os.path.exists(path):
        # ETFs (e.g. GOLDBEES) live in etf_data/, NOT price_data/ — price_data
        # is globbed as the universe by core.market_breadth_pct and
        # core.liquid_universe, and a high-turnover ETF placed there would
        # enter the tradable top-200 and could get bought by the strategy.
        path = os.path.join(ETF_DIR, f"{symbol}.csv")
        if not os.path.exists(path): return None
    df = pd.read_csv(path)
    # Same guard as core.load_stock: a truncated download can leave a
    # malformed last row whose Date survives as a literal string (not NaT)
    # and breaks date sorting downstream (see CLAUDE.md gotcha, SUNDARMFIN).
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df[df["Date"].notna()]
    df = df.sort_values("Date").set_index("Date")
    for col in ["Open", "Close", "High", "Low"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "Volume" in df.columns:
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce").fillna(0)
    return df.dropna(subset=["Close", "High", "Low"])


def load_delivery(symbol):
    path = os.path.join(DELIVERY_DIR, f"{symbol}.csv")
    if not os.path.exists(path): return None
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df[df["Date"].notna()]
    df = df.sort_values("Date").set_index("Date")
    df["DelivQty"] = pd.to_numeric(df["DelivQty"], errors="coerce").fillna(0)
    df["DelivPer"] = pd.to_numeric(df["DelivPer"], errors="coerce").fillna(0)
    return df


# ──────────────────────────────────────────────
# TIMEFRAME RESAMPLING
# ──────────────────────────────────────────────

def _agg_dict(df):
    d = dict(High=("High","max"), Low=("Low","min"), Close=("Close","last"))
    if "Open"   in df.columns: d["Open"]   = ("Open",  "first")
    if "Volume" in df.columns: d["Volume"] = ("Volume","sum")
    return d

def to_monthly(df):
    return df.resample("ME").agg(**_agg_dict(df)).dropna(subset=["High","Low","Close"])

def to_weekly(df):
    return df.resample("W").agg(**_agg_dict(df)).dropna(subset=["High","Low","Close"])


# ──────────────────────────────────────────────
# SWING PIVOTS  (vectorised)
# ──────────────────────────────────────────────

def find_swing_pivots(df, window=3):
    hi  = df["High"].values
    lo  = df["Low"].values
    vol = df["Volume"].values if "Volume" in df.columns else np.ones(len(df))
    idx = df.index
    n   = len(df)
    lows_out, highs_out = [], []
    if n <= window * 2:
        return lows_out, highs_out
    # rolling min/max via stride tricks — faster than loop
    from numpy.lib.stride_tricks import sliding_window_view
    w  = window * 2 + 1
    if n < w:
        return lows_out, highs_out
    lo_win = sliding_window_view(lo, w)
    hi_win = sliding_window_view(hi, w)
    for j in range(len(lo_win)):
        i = j + window
        if lo[i] == lo_win[j].min():
            lows_out.append((idx[i], lo[i], vol[i]))
        if hi[i] == hi_win[j].max():
            highs_out.append((idx[i], hi[i], vol[i]))
    return lows_out, highs_out


# ──────────────────────────────────────────────
# VOLUME PROFILE  (vectorised — no iterrows)
# ──────────────────────────────────────────────

def volume_profile_levels(df, bins=40, lookback=252):
    sub = df.tail(lookback)
    if "Volume" not in sub.columns or sub["Volume"].sum() == 0:
        return []

    lo_all = sub["Low"].values
    hi_all = sub["High"].values
    vols   = sub["Volume"].values

    lo_min = lo_all.min()
    hi_max = hi_all.max()
    if hi_max <= lo_min:
        return []

    edges   = np.linspace(lo_min, hi_max, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2
    vol_bins = np.zeros(bins)

    # vectorised: for each bar broadcast across bins
    # bar_lo[i] <= centers <= bar_hi[i]
    lo_mat  = lo_all[:, None]        # (bars, 1)
    hi_mat  = hi_all[:, None]
    vol_mat = vols[:, None]
    mask    = (centers[None, :] >= lo_mat) & (centers[None, :] <= hi_mat)  # (bars, bins)
    counts  = mask.sum(axis=1, keepdims=True).clip(min=1)
    vol_bins = (mask * vol_mat / counts).sum(axis=0)

    if vol_bins.max() == 0:
        return []

    norm = vol_bins / vol_bins.max()
    # local maxima above threshold
    hvn = []
    for i in range(1, bins - 1):
        if norm[i] > norm[i-1] and norm[i] > norm[i+1] and norm[i] > 0.4:
            hvn.append((round(float(centers[i]), 2), float(norm[i])))
    return hvn


# ──────────────────────────────────────────────
# CLUSTERING  (centroid-based, weighted)
# ──────────────────────────────────────────────

def cluster_levels_weighted(price_weight_pairs, tolerance=0.015):
    if not price_weight_pairs:
        return []
    data = sorted(price_weight_pairs, key=lambda x: x[0])
    clusters = [[data[0]]]
    for item in data[1:]:
        p         = item[0]
        c_prices  = [x[0] for x in clusters[-1]]
        c_weights = [x[1] for x in clusters[-1]]
        centroid  = np.average(c_prices, weights=c_weights)
        if (p - centroid) / centroid <= tolerance:
            clusters[-1].append(item)
        else:
            clusters.append([item])
    result = []
    for cl in clusters:
        prices  = [x[0] for x in cl]
        weights = [x[1] for x in cl]
        centroid = round(np.average(prices, weights=weights), 2)
        result.append((centroid, sum(weights), len(cl)))
    return result


# ──────────────────────────────────────────────
# WICK REJECTION SCORE  (vectorised — no iterrows)
# ──────────────────────────────────────────────

def wick_rejection_score(df, level, side="support", tolerance=0.015, lookback=252):
    sub = df.tail(lookback)
    hi  = sub["High"].values
    lo  = sub["Low"].values
    cl  = sub["Close"].values
    op  = sub["Open"].values if "Open" in sub.columns else cl

    body_hi   = np.maximum(op, cl)
    body_lo   = np.minimum(op, cl)
    bar_range = hi - lo
    valid     = bar_range > 0

    if side == "support":
        near = valid & (np.abs(lo - level) / level <= tolerance)
        if near.sum() == 0: return 0.0
        wicks = (body_lo[near] - lo[near]) / bar_range[near]
    else:
        near = valid & (np.abs(hi - level) / level <= tolerance)
        if near.sum() == 0: return 0.0
        wicks = (hi[near] - body_hi[near]) / bar_range[near]

    return float(wicks.mean())


# ──────────────────────────────────────────────
# DELIVERY SCORE AT LEVEL  (vectorised)
# ──────────────────────────────────────────────

def delivery_score_at_level(symbol, df_price, level, tolerance=0.015, lookback=60):
    deliv = load_delivery(symbol)
    if deliv is None or len(deliv) < 10:
        return 0.0
    sub     = df_price.tail(lookback)
    aligned = deliv.reindex(sub.index, method="nearest").dropna()
    if aligned.empty:
        return 0.0
    near_mask = (np.abs(sub["Close"].values - level) / level) <= tolerance
    if not near_mask.any():
        return 0.0
    near_idx   = sub.index[near_mask]
    near_deliv = aligned.reindex(near_idx, method="nearest")
    baseline   = aligned["DelivPer"].mean()
    if baseline == 0:
        return 0.0
    ratio = near_deliv["DelivPer"].mean() / baseline
    return float(np.clip((ratio - 1.0) / 1.0, 0, 1))


# ──────────────────────────────────────────────
# MULTI-TIMEFRAME CONFLUENCE
# ──────────────────────────────────────────────

def mtf_confluence(m_pivots, w_pivots, d_pivots, tolerance=0.02):
    bonuses = {}
    all_prices = (
        [(p, 3.0) for _, p, _ in m_pivots] +
        [(p, 2.0) for _, p, _ in w_pivots] +
        [(p, 1.0) for _, p, _ in d_pivots]
    )
    for p, wt in all_prices:
        key = round(p, 2)
        cur = bonuses.get(key, 0.0)
        for _, mp, _ in m_pivots:
            if mp != p and abs(p - mp) / p <= tolerance:
                cur = min(cur + 1.0, 1.0); break
        for _, wp, _ in w_pivots:
            if wp != p and abs(p - wp) / p <= tolerance:
                cur = min(cur + 0.5, 1.0); break
        bonuses[key] = cur
    return bonuses


# ──────────────────────────────────────────────
# COMPOSITE LEVEL SCORING
# ──────────────────────────────────────────────

def score_level(df, level, side, symbol=None, mtf_bonus=0.0, fast=False):
    if fast:
        # backtest: mtf bonus only, skip all IO and heavy calls
        return round(mtf_bonus, 4)

    wick      = wick_rejection_score(df, level, side=side)
    hvn_bonus = 0.0
    for hvn_price, hvn_score in volume_profile_levels(df):
        if abs(hvn_price - level) / level <= 0.02:
            hvn_bonus = max(hvn_bonus, hvn_score)
    deliv = 0.0
    if symbol:
        deliv = delivery_score_at_level(symbol, df, level)

    return round(wick * 0.35 + hvn_bonus * 0.30 + deliv * 0.15 + mtf_bonus * 0.20, 4)


# ──────────────────────────────────────────────
# GET ALL LEVELS
# ──────────────────────────────────────────────

def get_all_levels(df, lookback_months=24, symbol=None, fast=False):
    cur = float(df["Close"].iloc[-1])

    monthly = to_monthly(df).tail(lookback_months)
    weekly  = to_weekly(df).tail(lookback_months * 4)
    daily_r = df.tail(126)

    # smaller window in fast/backtest mode so early snapshots still get levels
    m_win = 2 if fast else 3
    w_win = 2 if fast else 3
    m_lows,  m_highs = find_swing_pivots(monthly, window=m_win)
    w_lows,  w_highs = find_swing_pivots(weekly,  window=w_win)
    d_lows,  d_highs = find_swing_pivots(daily_r, window=10)

    d_lows  = [(d, p, v) for d, p, v in d_lows  if p < cur and (cur - p) / cur <= 0.08]
    d_highs = [(d, p, v) for d, p, v in d_highs if p > cur and (p - cur) / cur <= 0.08]

    sup_bonuses = mtf_confluence(m_lows,  w_lows,  d_lows)
    res_bonuses = mtf_confluence(m_highs, w_highs, d_highs)

    def build_raw(m, w, d):
        raw = [(p, 3.0) for _, p, _ in m]
        raw += [(p, 2.0) for _, p, _ in w]
        raw += [(p, 1.0) for _, p, _ in d]
        return raw

    raw_sups = build_raw(m_lows,  w_lows,  d_lows)
    raw_ress = build_raw(m_highs, w_highs, d_highs)

    # Volume profile only in full mode (slow path)
    if not fast:
        for hvn_price, hvn_score in volume_profile_levels(df):
            if hvn_price < cur:
                raw_sups.append((hvn_price, hvn_score * 2.0))
            else:
                raw_ress.append((hvn_price, hvn_score * 2.0))

    sup_clusters = cluster_levels_weighted(raw_sups, tolerance=0.015)
    res_clusters = cluster_levels_weighted(raw_ress, tolerance=0.015)

    supports, resistances = [], []

    for centroid, wt, touches in sup_clusters:
        if centroid >= cur: continue
        mtf_b = sup_bonuses.get(round(centroid, 2), 0.0)
        comp  = score_level(df, centroid, "support",    symbol, mtf_b, fast=fast)
        final = round(wt * 0.5 + touches * 0.3 + comp * 10, 3)
        supports.append((centroid, touches, final))

    for centroid, wt, touches in res_clusters:
        if centroid <= cur: continue
        mtf_b = res_bonuses.get(round(centroid, 2), 0.0)
        comp  = score_level(df, centroid, "resistance", symbol, mtf_b, fast=fast)
        final = round(wt * 0.5 + touches * 0.3 + comp * 10, 3)
        resistances.append((centroid, touches, final))

    hi52 = round(float(df["High"].rolling(252).max().iloc[-1]), 2)
    lo52 = round(float(df["Low"].rolling(252).min().iloc[-1]),  2)
    if not supports    and lo52 < cur: supports    = [(lo52, 1, 1.0)]
    if not resistances and hi52 > cur: resistances = [(hi52, 1, 1.0)]

    supports.sort(key=lambda x: -x[0])
    resistances.sort(key=lambda x:  x[0])

    return supports[:3], resistances[:3]


# ──────────────────────────────────────────────
# get_levels  (drop-in replacement, backtest-safe)
# ──────────────────────────────────────────────

def get_levels(df, lookback_days=504, symbol=None, fast=False):
    """
    Returns (support, resistance, s_strength, r_strength).
    Pass fast=True from sr_backtest for vectorised-only path (no vol profile,
    no delivery IO) — runs ~20x faster, negligible accuracy loss in backtest.
    """
    supports, resistances = get_all_levels(df, symbol=symbol, fast=fast)

    def pick(candidates, side):
        confirmed = [(p, t, s) for p, t, s in candidates if t >= 2]
        pool = confirmed if confirmed else candidates
        if not pool: return None, 1
        if side == "support":
            pool.sort(key=lambda x: (-x[2], -x[0]))
        else:
            pool.sort(key=lambda x: (-x[2],  x[0]))
        return pool[0][0], pool[0][1]

    support,    s_str = pick(supports,    "support")
    resistance, r_str = pick(resistances, "resistance")

    if support is None:
        support = round(float(df["Low"].rolling(252).min().iloc[-1]), 2)
        s_str   = 1
    if resistance is None:
        resistance = round(float(df["High"].rolling(252).max().iloc[-1]), 2)
        r_str      = 1

    return support, resistance, s_str, r_str


# ──────────────────────────────────────────────
# REACH PROBABILITY  (walk-forward, no lookahead)
# ──────────────────────────────────────────────

def reach_probability(df, level, direction, forward_days=21, lookback_days=504):
    closes = df["Close"].values
    highs  = df["High"].values
    lows   = df["Low"].values
    n      = len(closes)
    cur    = closes[-1]

    dist_pct = (cur - level) / cur if direction == "down" else (level - cur) / cur
    if dist_pct <= 0: return None, 0

    tolerance = max(dist_pct * 0.35, 0.008)
    start = max(0, n - lookback_days)
    end   = n - forward_days - 1

    hits = trials = 0
    for i in range(start, end):
        ref = closes[i]
        ref_dist = (ref - level) / ref if direction == "down" else (level - ref) / ref
        if ref_dist < 0: continue
        if abs(ref_dist - dist_pct) > tolerance: continue
        trials += 1
        if direction == "down":
            if lows[i+1: i+1+forward_days].min() <= level * 1.005: hits += 1
        else:
            if highs[i+1: i+1+forward_days].max() >= level * 0.995: hits += 1

    if trials < 5: return None, trials
    return int(round(hits / trials * 100)), trials


# ──────────────────────────────────────────────
# REACH PROBABILITY v2  (empirical distance × volatility base-rate table)
# ──────────────────────────────────────────────
#
# The historical-analog reach_probability() above has ~zero discriminative
# power: calibration over the full universe gave corr(pred, hit) = -0.06 and
# the 90-100% "confident" bucket actually hit only ~60%. Root cause: it
# conditions on a fixed rupee price scanned through history, counting
# consecutive days as independent trials (pseudo-replication) over a premise
# that carries no signal.
#
# v2 replaces it with an empirical lookup keyed on the two features that DO
# correlate with whether a level holds: distance-to-level and realized
# volatility. Table is built walk-forward with a time-based holdout by
# sr_build_reachtable.py (OOS corr(pred, hit) = 0.17, monotonic buckets).
# Rebuild the table when the price history is materially extended:
#     python sr_build_reachtable.py
#
# Same (prob:int|None, n:int) return signature as reach_probability() so it is
# a drop-in for the logger, analyse_table, and analyse.

import json as _json

_REACH_TABLE_PATH = "../data/sr_reach_table.json"
_REACH_TABLE = None   # lazy-loaded singleton


def _load_reach_table():
    global _REACH_TABLE
    if _REACH_TABLE is None:
        if os.path.exists(_REACH_TABLE_PATH):
            with open(_REACH_TABLE_PATH) as f:
                _REACH_TABLE = _json.load(f)
        else:
            _REACH_TABLE = {}   # missing table -> callers fall back
    return _REACH_TABLE


def _bucket(value, edges, labels):
    for i in range(len(labels)):
        if edges[i] <= value < edges[i + 1]:
            return labels[i]
    return labels[-1]


def reach_probability_v2(df, level, direction, forward_days=21):
    """
    Empirical reach probability from the (distance × volatility) table.
    Returns (prob:int|None, n:int) — n is the training-cell sample size
    (not a per-stock count). Returns (None, 0) if the level is on the wrong
    side of price or the table is unavailable.
    """
    tbl = _load_reach_table()
    if not tbl:
        return None, 0

    cur = float(df["Close"].iloc[-1])
    dist_pct = (cur - level) / cur if direction == "down" else (level - cur) / cur
    if dist_pct <= 0:
        return None, 0

    ret = df["Close"].pct_change().dropna().tail(252)
    if len(ret) < 30:
        return None, 0
    vol = float(ret.std() * np.sqrt(252) * 100)

    db = _bucket(dist_pct, tbl["dist_edges"], tbl["dist_labels"])
    vb = _bucket(vol,      tbl["vol_edges"],  tbl["vol_labels"])
    cell = tbl["table"].get(f"{db}|{vb}")
    if cell is None:
        return int(round(tbl["base_rate"])), 0
    return int(round(cell["prob"])), int(cell.get("n", 0))


# ──────────────────────────────────────────────
# DELIVERY SIGNAL
# ──────────────────────────────────────────────

def get_delivery_signal(symbol, df_price):
    deliv = load_delivery(symbol)
    if deliv is None or len(deliv) < 20: return None
    deliv = deliv[deliv.index <= df_price.index[-1]].tail(30)
    if len(deliv) < 10: return None
    recent_avg = deliv["DelivPer"].tail(5).mean()
    baseline   = deliv["DelivPer"].tail(20).mean()
    if baseline == 0: return None
    ratio = recent_avg / baseline
    if ratio >= 1.4:
        return f"📦 High delivery ({recent_avg:.0f}% vs avg {baseline:.0f}%) — institutional interest"
    elif ratio <= 0.7:
        return f"📦 Low delivery ({recent_avg:.0f}% vs avg {baseline:.0f}%) — mostly intraday"
    return None


# ──────────────────────────────────────────────
# TREND CONTEXT
# ──────────────────────────────────────────────

def get_trend_context(df):
    close = df["Close"]
    cur   = float(close.iloc[-1])
    ma10  = close.rolling(10).mean().iloc[-1]
    ma20  = close.rolling(20).mean().iloc[-1]
    ma50  = close.rolling(50).mean().iloc[-1]
    ma200 = close.rolling(200).mean().iloc[-1]
    hi52  = round(float(df["High"].rolling(252).max().iloc[-1]), 2)
    lo52  = round(float(df["Low"].rolling(252).min().iloc[-1]),  2)
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rsi   = round(float(100 - 100 / (1 + gain / loss).iloc[-1]), 1)
    above, below = [], []
    for name, ma in [("10d",ma10),("20d",ma20),("50d",ma50),("200d",ma200)]:
        if pd.isna(ma): continue
        (above if cur > ma else below).append(name)
    if   len(above) == 4: trend = "strong uptrend"
    elif len(above) >= 3: trend = "uptrend"
    elif len(below) == 4: trend = "strong downtrend"
    elif len(below) >= 3: trend = "downtrend"
    else:                 trend = "sideways"
    return {"cur": cur, "hi52": hi52, "lo52": lo52, "rsi": rsi, "trend": trend}


# ──────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────

def strength_label(n):
    if n >= 4: return "Strong"
    if n >= 2: return "Moderate"
    return "Weak"


# ──────────────────────────────────────────────
# GET TRADE LEVELS
# ──────────────────────────────────────────────

def get_trade_levels(df, symbol=None):
    cur = round(float(df["Close"].iloc[-1]), 2)
    support, resistance, s_str, r_str = get_levels(df, symbol=symbol)
    stop   = round(support * 0.97, 2)
    risk   = support - stop
    reward = resistance - support
    rr     = round(reward / risk, 2) if risk > 0 else 0
    return {
        "current":    cur,
        "buy_zone":   support,
        "target":     resistance,
        "stop":       stop,
        "rr":         rr,
        "s_strength": s_str,
        "r_strength": r_str,
    }


# ──────────────────────────────────────────────
# TABLE OUTPUT
# ──────────────────────────────────────────────

GAP = "     "

def analyse_table(symbols):
    rows = []
    for sym in symbols:
        sym = sym.strip().upper()
        if not sym.endswith(".NS"): sym += ".NS"
        df = load_stock(sym)
        if df is None or len(df) < 60: continue

        ctx        = get_trend_context(df)
        sups, ress = get_all_levels(df, symbol=sym)
        t          = get_trade_levels(df, symbol=sym)
        cur        = ctx["cur"]

        s1_prob, _ = reach_probability_v2(df, sups[0][0], "down") if sups else (None, 0)
        r1_prob, _ = reach_probability_v2(df, ress[0][0], "up")   if ress else (None, 0)

        def fmt_s(levels, i, prob=None):
            if len(levels) <= i: return "—"
            p, _, score = levels[i]
            dist = round((cur - p) / cur * 100, 1)
            flag = " ⚠" if dist > 15 else ""
            prob_tag = f" [{prob}%]" if prob is not None else ""
            return f"₹{p:,.2f} (-{dist}%){flag}{prob_tag}"

        def fmt_r(levels, i, prob=None):
            if len(levels) <= i: return "—"
            p, _, score = levels[i]
            dist = round((p - cur) / cur * 100, 1)
            flag = " ⚠" if dist > 15 else ""
            prob_tag = f" [{prob}%]" if prob is not None else ""
            return f"₹{p:,.2f} (+{dist}%){flag}{prob_tag}"

        trend_map = {
            "strong uptrend":   "⬆⬆ Strong Up",
            "uptrend":          "⬆  Uptrend",
            "sideways":         "➡  Sideways",
            "downtrend":        "⬇  Downtrend",
            "strong downtrend": "⬇⬇ Strong Down",
        }
        rsi     = ctx["rsi"]
        rsi_tag = " OB" if rsi > 70 else " OS" if rsi < 35 else ""
        deliv     = get_delivery_signal(sym, df)
        deliv_tag = "📦Hi" if deliv and "High" in deliv else ("📦Lo" if deliv and "Low" in deliv else "")

        rows.append({
            "Symbol": sym.replace(".NS",""),
            "CMP":    f"₹{cur:,.2f}",
            "S1":     fmt_s(sups, 0, s1_prob),
            "S2":     fmt_s(sups, 1),
            "R1":     fmt_r(ress, 0, r1_prob),
            "R2":     fmt_r(ress, 1),
            "RSI":    f"{rsi}{rsi_tag}",
            "Bias":   trend_map.get(ctx["trend"], ctx["trend"]),
            "R:R":    f"1:{t['rr']}",
            "Deliv":  deliv_tag,
        })

    if not rows: print("No data."); return
    cols   = ["Symbol","CMP","S1","S2","R1","R2","RSI","Bias","R:R","Deliv"]
    widths = {c: max(len(c), max(len(r[c]) for r in rows)) for c in cols}
    sep    = GAP.join("─"*widths[c] for c in cols)
    hdr    = GAP.join(c.ljust(widths[c]) for c in cols)
    print(f"\n{sep}")
    print(hdr)
    print(sep)
    for r in rows:
        print(GAP.join(str(r[c]).ljust(widths[c]) for c in cols))
    print(f"{sep}")
    print(f"\n  S1/R1 [xx%] = reach probability within 21 days")
    print(f"  ⚠ = level >15% from CMP   📦Hi/Lo = delivery volume signal\n")


# ──────────────────────────────────────────────
# VERBOSE OUTPUT
# ──────────────────────────────────────────────

def analyse(symbols):
    for sym in symbols:
        sym = sym.strip().upper()
        if not sym.endswith(".NS"): sym += ".NS"
        df = load_stock(sym)
        if df is None or len(df) < 60:
            print(f"\n❌ {sym}: no data"); continue

        ctx        = get_trend_context(df)
        sups, ress = get_all_levels(df, symbol=sym)
        t          = get_trade_levels(df, symbol=sym)
        cur        = ctx["cur"]

        s_labels = ["S1 — Immediate support","S2 — Support","S3 — Strong support"]
        r_labels = ["R1 — Immediate resistance","R2 — Resistance","R3 — Strong resistance"]

        print(f"\n{'═'*66}")
        print(f"  {sym}   CMP ₹{cur:,.2f}   52w H ₹{ctx['hi52']:,.2f}   52w L ₹{ctx['lo52']:,.2f}")
        print(f"  (Multi-timeframe S/R | Monthly + Weekly + Daily + Volume Profile)")
        print(f"{'═'*66}")

        for i, (p, t_, sc) in enumerate(reversed(ress)):
            label    = r_labels[len(ress)-1-i]
            dist     = round((p-cur)/cur*100, 1)
            prob, ns = reach_probability_v2(df, p, "up")
            prob_str = f"  [{prob}% prob, cell n={ns}]" if prob is not None else ""
            print(f"  {label:<30} [{strength_label(t_):8}] [score:{sc:.2f}]  ₹{p:,.2f}  (+{dist}%){prob_str}")

        rsi_note = "overbought" if ctx["rsi"]>70 else "oversold" if ctx["rsi"]<35 else "neutral"
        print(f"  {'─'*64}")
        print(f"  CMP ₹{cur:,.2f}  —  {ctx['trend']}   RSI {ctx['rsi']} ({rsi_note})")
        print(f"  {'─'*64}")

        for i, (p, t_, sc) in enumerate(sups):
            label    = s_labels[i] if i < len(s_labels) else f"S{i+1} — Support"
            dist     = round((cur-p)/cur*100, 1)
            marker   = "  ← buy zone" if i == 0 else ""
            prob, ns = reach_probability_v2(df, p, "down")
            prob_str = f"  [{prob}% prob, cell n={ns}]" if prob is not None else ""
            print(f"  {label:<30} [{strength_label(t_):8}] [score:{sc:.2f}]  ₹{p:,.2f}  (-{dist}%){prob_str}{marker}")

        deliv = get_delivery_signal(sym, df)
        if deliv: print(f"\n  {deliv}")

        buy  = t["buy_zone"]
        tgt  = t["target"]
        stop = t["stop"]
        rr   = t["rr"]
        print(f"\n  📥 Buy ₹{buy:,.2f}   🎯 Target ₹{tgt:,.2f} (+{round((tgt-buy)/buy*100,1)}%)")
        print(f"  🛑 Stop ₹{stop:,.2f}   ⚖️  R:R 1:{rr}")
        print(f"{'═'*66}\n")


# ──────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────

def main():
    args    = [a for a in sys.argv[1:] if not a.startswith("--")]
    verbose = "--verbose" in sys.argv
    if args:
        symbols = args
    else:
        raw     = input("Enter symbols: ")
        symbols = [s.strip() for s in raw.replace(",", " ").split() if s.strip()]
    if not symbols:
        print("No symbols."); return
    if verbose:
        analyse(symbols)
    else:
        analyse_table(symbols)


if __name__ == "__main__":
    main()