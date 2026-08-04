import os, sys
from datetime import datetime
import numpy as np
import pandas as pd

PRICE_DIR    = "../data/price_data/"
DELIVERY_DIR = "../data/delivery_data/"
ETF_DIR      = "../data/etf_data/"
INDEX_DIR    = "../data/index_data/"

# Indices aren't stocks or ETFs — no .NS suffix, filename != tradable symbol.
# Map the friendly name used everywhere else (e.g. "NIFTY50") to its file.
INDEX_FILES = {
    "NIFTY50": "nifty50.csv",
    "INDIAVIX": "indiavix.csv",
}


# ──────────────────────────────────────────────
# DATA LOADERS
# ──────────────────────────────────────────────

def load_stock(symbol):
    if symbol.upper() in INDEX_FILES:
        path = os.path.join(INDEX_DIR, INDEX_FILES[symbol.upper()])
        if not os.path.exists(path): return None
    else:
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

def get_all_levels(df, lookback_months=24, symbol=None, fast=False, cur=None):
    """Supports/resistances around the reference price.

    cur: reference price. Defaults to the last CLOSE in df. Pass the LIVE quote
    to re-rank levels against where the stock actually is right now — price
    moving through a level intraday genuinely changes which level is "nearest
    support" and which has become resistance, and anchoring that decision to a
    stale close answers a slightly different question than the one asked.

    The PIVOTS themselves are unaffected by this: they are swing points and
    volume nodes derived from completed bars, and they do not move because the
    clock advanced. Only the above/below split and the proximity window shift.
    """
    cur = float(cur) if cur else float(df["Close"].iloc[-1])

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

    # MIN_SEPARATION (2026-08-05). Ordering used to be PURELY by proximity, so
    # S1 was whatever pivot sat closest to price even when that was 0.1% away
    # and structurally meaningless. Measured on the 2026-08-04 panel: 20/61
    # names had a level inside 0.5% and median S1 was -2.1%, with P(touch)
    # >90% — a number that is trivially true and useless for a month-horizon
    # decision ("of course price revisits a level 0.4% away").
    #
    # The `final` strength score was already being computed and then THROWN
    # AWAY by the sort. This keeps proximity ordering (S1 should still be the
    # nearest *usable* level) but drops candidates hugging spot, so S1/R1
    # describe structure rather than noise around the current tick.
    #
    # Threshold is volatility-scaled, not fixed: 1% is meaningful for a 15%-vol
    # name and pure noise for a 60%-vol one.
    #
    # Scaled to the HORIZON's sigma, not the day's. The question this subsystem
    # answers is "where might price go before month-end", so the yardstick is
    # roughly a month of movement (~21 trading days), not one session. A first
    # attempt used 0.35x DAILY sigma and was far too weak — it gave a 35%-vol
    # name only 0.77% of separation, so a level 1.9% away still survived as
    # "S1", which is the exact complaint this fix exists to answer.
    #
    # 0.25x the 21-day sigma: enough that a level must be a real fraction of a
    # month's expected range, not so much that legitimate nearby structure is
    # discarded. Floored at 1% (below that it is noise for any NSE name) and
    # capped at 6% (a very wild name should not have every level filtered out).
    ret = df["Close"].pct_change().dropna().tail(252)
    day_sigma = float(ret.std()) if len(ret) >= 30 else 0.02
    horizon_sigma = day_sigma * (21 ** 0.5)
    min_sep = min(max(horizon_sigma * 0.25, 0.01), 0.06)

    def _spread(levels, sign):
        """Keep levels at least `min_sep` from price AND from each other.

        Without the pairwise check, S1/S2/S3 can be three points inside one
        cluster — nominally three levels, one piece of information.
        """
        out = []
        for lv in sorted(levels, key=lambda x: -sign * x[0]):
            if abs(lv[0] - cur) / cur < min_sep:
                continue
            if any(abs(lv[0] - k[0]) / cur < min_sep for k in out):
                continue
            out.append(lv)
        return out

    sup_f, res_f = _spread(supports, 1), _spread(resistances, -1)

    # Never return NOTHING because the filter was strict: a far-but-real level
    # beats no level, and a caller that gets None cannot tell "no structure
    # here" from "filtered out". Fall back to the unfiltered nearest.
    if not sup_f and supports:
        sup_f = sorted(supports, key=lambda x: -x[0])
    if not res_f and resistances:
        res_f = sorted(resistances, key=lambda x: x[0])

    return sup_f[:3], res_f[:3]


# ──────────────────────────────────────────────
# get_levels  (drop-in replacement, backtest-safe)
# ──────────────────────────────────────────────

def get_levels(df, lookback_days=504, symbol=None, fast=False, cur=None):
    """
    Returns (support, resistance, s_strength, r_strength).
    Pass fast=True from sr_backtest for vectorised-only path (no vol profile,
    no delivery IO) — runs ~20x faster, negligible accuracy loss in backtest.
    cur: reference price; defaults to the last close. See get_all_levels.
    """
    supports, resistances = get_all_levels(df, symbol=symbol, fast=fast, cur=cur)

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

# P(TOUCH) table — the metric every consumer actually asks for. Preferred when
# present. The older sr_reach_table.json measures P(bounce | touched): its
# builder drops untouched levels entirely (sr_backtest.test_support returns
# None when never touched), so distant levels are conditioned on having been
# reached and the table comes out nearly FLAT in distance (12%+ reads ~66%,
# above 0-2%'s ~57%) while reality decays hard. See sr_build_touchtable.py.
_TOUCH_TABLE_PATH = "../data/sr_touch_table.json"
_REACH_TABLE_PATH = "../data/sr_reach_table.json"
_REACH_TABLE = None   # lazy-loaded singleton


_HORIZON_TABLES = {}   # forward_days -> table (or None if none on disk)


def _load_reach_table():
    """Prefer the P(touch) table; fall back to the legacy bounce table.

    The fallback is LOUD on purpose. The legacy table measures a different
    quantity (P(bounce | touched), OOS corr 0.173 vs the touch table's 0.529)
    and reads ~66% for levels 12%+ away that are almost never reached. Silently
    degrading to it would keep the system running while quietly answering a
    different question — the exact failure this subsystem already had once.
    """
    global _REACH_TABLE
    if _REACH_TABLE is None:
        if os.path.exists(_TOUCH_TABLE_PATH):
            with open(_TOUCH_TABLE_PATH) as f:
                _REACH_TABLE = _json.load(f)
        elif os.path.exists(_REACH_TABLE_PATH):
            with open(_REACH_TABLE_PATH) as f:
                _REACH_TABLE = _json.load(f)
            print("  ⚠️  sr_touch_table.json MISSING — falling back to the legacy "
                  "sr_reach_table.json, which measures P(bounce|touched), NOT "
                  "P(touch). Far levels will read far too high. Rebuild with: "
                  "python sr_build_touchtable.py", file=sys.stderr)
        else:
            _REACH_TABLE = {}   # missing table -> callers fall back
    return _REACH_TABLE


def _available_horizon_tables():
    """{forward_days: path} for every natively-built P(touch) table on disk."""
    out = {}
    base = "../data"
    if not os.path.isdir(base):
        return out
    for fn in os.listdir(base):
        if fn.startswith("sr_touch_table_") and fn.endswith("d.json"):
            try:
                out[int(fn[len("sr_touch_table_"):-len("d.json")])] = \
                    os.path.join(base, fn)
            except ValueError:
                continue
    if os.path.exists(_TOUCH_TABLE_PATH):
        out.setdefault(21, _TOUCH_TABLE_PATH)
    return out


def _read_horizon_table(path):
    try:
        with open(path) as f:
            return _json.load(f)
    except Exception:
        return None


def _load_horizon_table(forward_days):
    """The natively-built table matching `forward_days` EXACTLY, else None.

    Deliberately exact-match. An earlier version returned the nearest table
    within a few days and let the caller rescale from it, but that made
    P(touch) NON-MONOTONIC in horizon: at 13d it rescaled the 10d table upward
    to 94% while 14d switched to the 21d table and read 90%, so a LONGER
    horizon reported a LOWER touch probability — impossible, and visible to the
    user as a number that jumps around as the month advances.

    Interpolation between bracketing tables (see _interpolate_horizon_prob)
    handles the in-between horizons instead, which is both monotonic and more
    accurate than extrapolating from one side.
    """
    if forward_days in _HORIZON_TABLES:
        return _HORIZON_TABLES[forward_days]
    avail = _available_horizon_tables()
    tbl = _read_horizon_table(avail[forward_days]) if forward_days in avail else None
    _HORIZON_TABLES[forward_days] = tbl
    return tbl


def _interpolate_horizon_prob(dist_pct, vol, forward_days):
    """P(touch) at `forward_days` by interpolating between the two native
    tables that bracket it. Returns (prob, n) or None if not bracketed.

    Interpolation is done in sqrt(time), matching the first-passage scaling the
    fallback uses, so the curve is smooth and monotone between anchors. This
    keeps every horizon grounded in EMPIRICAL tables rather than extrapolating
    one table across the whole month.
    """
    avail = _available_horizon_tables()
    if not avail:
        return None
    lo = max((d for d in avail if d <= forward_days), default=None)
    hi = min((d for d in avail if d >= forward_days), default=None)

    # Outside the anchor range (e.g. 1-4d when the shortest table is 5d):
    # extrapolate by sqrt-time from the NEAREST anchor, not from the 21d table.
    # Scaling from a distant anchor disagrees with the near one and produced a
    # non-monotonic seam — WIPRO S1 read 22% at 4d (scaled from 21d) then 19%
    # at 5d (native), i.e. a longer horizon looking less likely.
    if lo is None or hi is None:
        anchor_days = hi if lo is None else lo
        t = _read_horizon_table(avail[anchor_days])
        if not t:
            return None
        db = _bucket(dist_pct, t["dist_edges"], t["dist_labels"])
        vb = _bucket(vol,      t["vol_edges"],  t["vol_labels"])
        c = t["table"].get(f"{db}|{vb}")
        p = float(c["prob"]) if c else float(t.get("base_rate", 50))
        n = int(c.get("n", 0)) if c else 0
        from sr_horizon import scale_probability_to_horizon
        scaled = scale_probability_to_horizon(p, forward_days, anchor_days)
        if scaled is None:
            return None
        # Never let a short extrapolation exceed its own anchor (or fall below,
        # when extrapolating past the longest table).
        scaled = min(scaled, p) if forward_days < anchor_days else max(scaled, p)
        return int(round(scaled)), n

    if lo == hi:
        return None

    t_lo, t_hi = _read_horizon_table(avail[lo]), _read_horizon_table(avail[hi])
    if not t_lo or not t_hi:
        return None

    def cell_of(t):
        db = _bucket(dist_pct, t["dist_edges"], t["dist_labels"])
        vb = _bucket(vol,      t["vol_edges"],  t["vol_labels"])
        c = t["table"].get(f"{db}|{vb}")
        return (float(c["prob"]), int(c.get("n", 0))) if c else \
               (float(t.get("base_rate", 50)), 0)

    p_lo, n_lo = cell_of(t_lo)
    p_hi, n_hi = cell_of(t_hi)

    w = (np.sqrt(forward_days) - np.sqrt(lo)) / (np.sqrt(hi) - np.sqrt(lo))
    prob = p_lo + w * (p_hi - p_lo)
    # Monotone in horizon by construction, but clamp against inverted anchors
    # (possible from sampling noise in thin cells) so the guarantee is absolute.
    prob = min(max(prob, min(p_lo, p_hi)), max(p_lo, p_hi))
    return int(round(prob)), min(n_lo, n_hi)


def _bucket(value, edges, labels):
    for i in range(len(labels)):
        if edges[i] <= value < edges[i + 1]:
            return labels[i]
    return labels[-1]


def reach_probability_v2(df, level, direction, forward_days=None, cur=None):
    """
    Empirical reach probability from the (distance × volatility) table.
    Returns (prob:int|None, n:int) — n is the training-cell sample size
    (not a per-stock count). Returns (None, 0) if the level is on the wrong
    side of price or the table is unavailable.

    METRIC: with sr_touch_table.json present this is P(TOUCH) — the question
    every caller actually asks. The legacy sr_reach_table.json measured
    P(bounce | touched) because its builder DROPPED untouched levels, which
    made it nearly flat in distance (12%+ read ~66%, above 0-2%'s ~57%). The
    P(touch) table decays properly (94.5% -> 5.9% across distance at 25-35%
    vol) and triples OOS correlation (0.529 vs 0.173).

    forward_days: horizon in TRADING DAYS. Resolution order:
      1. a table built natively at this horizon (sr_touch_table_<N>d.json) —
         empirical, preferred;
      2. otherwise the 21d table rescaled by sqrt-of-time (approximate).
      Previously this argument was accepted and then IGNORED, so a caller
      asking for a 9-day horizon silently got the 21-day probability — a
      systematic overstatement. None/omitted = the table's native window
      (unchanged behaviour for existing callers).

    cur: override the reference price — pass the LIVE quote here so distance is
      measured from the real current price rather than the last CSV close.
    """
    # Prefer a table built natively at this horizon over rescaling the 21d one.
    native = _load_horizon_table(forward_days) if forward_days else None
    tbl = native or _load_reach_table()
    if not tbl:
        return None, 0

    cur = float(cur) if cur else float(df["Close"].iloc[-1])
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
        prob, n = int(round(tbl["base_rate"])), 0
    else:
        prob, n = int(round(cell["prob"])), int(cell.get("n", 0))

    # Horizon resolution, best source first:
    #   1. native table at exactly this horizon  -> already empirical, no scaling
    #   2. interpolation between bracketing native tables (sqrt-time weighted)
    #   3. sqrt-of-time rescale of whatever table we have (approximate)
    table_days = int(tbl.get("forward_days", 21))
    if forward_days is not None and int(forward_days) != table_days:
        interp = _interpolate_horizon_prob(dist_pct, vol, int(forward_days))
        if interp is not None:
            prob, n = interp
        else:
            from sr_horizon import scale_probability_to_horizon
            prob = scale_probability_to_horizon(prob, int(forward_days), table_days)

    return prob, n


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

def get_trade_levels(df, symbol=None, cur=None):
    cur = round(float(cur) if cur else float(df["Close"].iloc[-1]), 2)
    support, resistance, s_str, r_str = get_levels(df, symbol=symbol, cur=cur)
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

def analyse_table(symbols, as_of=None, live_prices=None, live_sources=None):
    """Horizon-aware S/R table.

    The reported levels and probabilities answer: "from `as_of` (default today)
    until this month's REBALANCE DATE — the last Tuesday — will price touch
    this level?" The horizon shrinks as the month progresses, and the
    probabilities shrink with it.

    live_prices: optional {SYMBOL: price} from the live quote API. When a
    symbol is present, that price is used as CMP and as the reference for
    distance/probability; otherwise the last CSV close is used.
    """
    import sr_horizon as H

    as_of = pd.Timestamp(as_of).normalize() if as_of is not None \
        else pd.Timestamp.today().normalize()
    end = H.horizon_end(as_of)
    cal = H.project_calendar_forward(H.load_trading_calendar(), end)
    horizon_days = H.trading_days_until(as_of, end, cal)

    rows = []
    trimmed = []
    for sym in symbols:
        sym = sym.strip().upper()
        if not sym.endswith(".NS"): sym += ".NS"
        df = load_stock(sym)
        if df is None or len(df) < 60: continue

        # Never build levels on a partial (mid-session) candle — see
        # drop_partial_candle. Matters most on exactly the mid-session intraday
        # runs this path is used for.
        n_before = len(df)
        df = drop_partial_candle(df)
        if len(df) < n_before:
            trimmed.append(sym.replace(".NS", ""))
        if len(df) < 60: continue

        ctx = get_trend_context(df)

        # Live quote overrides the last close as the reference price, and it is
        # threaded into LEVEL SELECTION too — not just distance/probability.
        # Price moving through a level intraday genuinely changes which level
        # is the nearest support and which has flipped to resistance; ranking
        # that off a stale close answers a slightly different question.
        # The pivots themselves are unchanged (completed bars only) — what
        # shifts is the above/below split and the proximity window.
        live = (live_prices or {}).get(sym.replace(".NS", "")) or \
               (live_prices or {}).get(sym)
        cur  = round(float(live), 2) if live else ctx["cur"]

        sups, ress = get_all_levels(df, symbol=sym, cur=cur)
        t          = get_trade_levels(df, symbol=sym, cur=cur)

        s1_prob, _ = reach_probability_v2(df, sups[0][0], "down", horizon_days, cur) \
            if sups else (None, 0)
        r1_prob, _ = reach_probability_v2(df, ress[0][0], "up", horizon_days, cur) \
            if ress else (None, 0)
        # S2/R2 now carry a meaningful probability too. Under the old
        # P(bounce|touched) table their numbers were near-constant and
        # uninformative; under P(touch) a distant S2 correctly reads low, which
        # is exactly what a user needs to see before planning around it.
        s2_prob, _ = reach_probability_v2(df, sups[1][0], "down", horizon_days, cur) \
            if len(sups) > 1 else (None, 0)
        r2_prob, _ = reach_probability_v2(df, ress[1][0], "up", horizon_days, cur) \
            if len(ress) > 1 else (None, 0)

        # A level can end up on the WRONG side of price — especially with live
        # intraday quotes, where a support computed from history gets breached
        # mid-session. That is real information (the level flipped), so label
        # it BROKEN rather than printing a nonsense "--0.4%". reach_probability
        # returns None in that case by design, so no probability is shown.
        def fmt_s(levels, i, prob=None):
            if len(levels) <= i: return "—"
            p, _, score = levels[i]
            if p > cur:
                return f"₹{p:,.2f} (BROKEN +{round((p-cur)/cur*100,1)}%)"
            dist = round((cur - p) / cur * 100, 1)
            flag = " ⚠" if dist > 15 else ""
            prob_tag = f" [{prob}%]" if prob is not None else ""
            return f"₹{p:,.2f} (-{dist}%){flag}{prob_tag}"

        def fmt_r(levels, i, prob=None):
            if len(levels) <= i: return "—"
            p, _, score = levels[i]
            if p < cur:
                return f"₹{p:,.2f} (BROKEN -{round((cur-p)/cur*100,1)}%)"
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
            "S2":     fmt_s(sups, 1, s2_prob),
            "R1":     fmt_r(ress, 0, r1_prob),
            "R2":     fmt_r(ress, 1, r2_prob),
            "RSI":    f"{rsi}{rsi_tag}",
            "Bias":   trend_map.get(ctx["trend"], ctx["trend"]),
            "R:R":    f"1:{t['rr']}",
            "Deliv":  deliv_tag,
        })

    if not rows: print("No data."); return
    srcs = live_sources or {}
    n_kite = sum(1 for v in srcs.values() if v == "kite")
    n_yf = sum(1 for v in srcs.values() if v == "yfinance")
    if n_kite or n_yf:
        parts = []
        if n_kite:
            parts.append(f"{n_kite} real-time (Kite)")
        if n_yf:
            parts.append(f"{n_yf} ~15-min delayed (yfinance)")
        cmp_src = " + ".join(parts)
    else:
        cmp_src = "last CSV close"
    print(f"\n  Horizon: {as_of.date()} → {end.date()} (last Tue) — "
          f"{horizon_days} trading days")
    print(f"  CMP source: {cmp_src}")
    if trimmed:
        print(f"  ⓘ Mid-session run: today's PARTIAL candle excluded from level "
              f"detection for {len(trimmed)} symbol(s) — levels reflect data "
              f"through the last CLOSED session, while CMP is live.")
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
    print(f"\n  S1/R1 [xx%] = P(touch) by {end.date()} "
          f"({horizon_days} trading days)")
    # Describe how this horizon was actually resolved — exact native table,
    # interpolation between two empirical tables, or a sqrt rescale. Saying
    # "rescaled" when the number came from interpolated empirical tables
    # understates it; saying "empirical" when it was rescaled overstates it.
    _avail = sorted(_available_horizon_tables())
    if horizon_days in _avail:
        print(f"  ⓘ From the P(touch) table built natively at {horizon_days}d "
              f"(empirical).")
    elif _avail and min(_avail) <= horizon_days <= max(_avail):
        lo = max(d for d in _avail if d <= horizon_days)
        hi = min(d for d in _avail if d >= horizon_days)
        print(f"  ⓘ Interpolated (sqrt-time) between the empirical {lo}d and "
              f"{hi}d P(touch) tables.")
    else:
        print(f"  ⓘ Extrapolated by sqrt-of-time from the nearest empirical "
              f"table — approximate; direction and rough size are right, the "
              f"exact value is not calibrated at this horizon.")
    print(f"  ⚠ = level >15% from CMP   📦Hi/Lo = delivery volume signal")

    # ── MONTHLY CONTAINMENT BAND ──────────────────────────────────────────
    # S1/R1 above answer "will price REACH this level" (P(touch)). That is the
    # wrong quantity for "give me a level it won't dip below this month" — a
    # 94% P(touch) support has a ~6% chance of HOLDING. The band below answers
    # the containment question directly. Both are printed so the distinction is
    # visible at the point of use rather than left to memory.
    try:
        from containment_band import containment_band
        bands = []
        for sym in symbols:
            s = sym if sym.endswith(".NS") else sym + ".NS"
            df_b = load_stock(s)
            if df_b is None:
                continue
            cur_b = (live_prices or {}).get(sym)
            b = containment_band(df_b, horizon=horizon_days, cur=cur_b)
            if b:
                bands.append((sym.replace(".NS", ""), b))
        if bands:
            print(f"\n  {'─'*66}")
            print(f"  MONTHLY CONTAINMENT BAND — {int((1-bands[0][1]['alpha'])*100)}%"
                  f" confidence, {horizon_days}d horizon")
            print(f"  {'─'*66}")
            print(f"  {'Symbol':<12}{'FLOOR':>11}{'CMP':>11}{'CEILING':>11}"
                  f"{'floor%':>9}{'ceil%':>8}")
            for s, b in bands:
                print(f"  {s:<12}{b['floor']:>11.2f}{b['price']:>11.2f}"
                      f"{b['ceiling']:>11.2f}"
                      f"{-b['floor_width']*100:>8.1f}%{b['ceiling_width']*100:>7.1f}%")
            src = bands[0][1]["source"]
            print(f"\n  Price stays inside this band in ~{int((1-bands[0][1]['alpha'])*100)}%"
                  f" of months. Breach expected ~{int(bands[0][1]['alpha']*100)}%.")
            print(f"  source: {src}")
            print("  ⚠ This is a RISK/EXPECTATION band, NOT an entry signal.")
            print("    Buying the floor when it fills tested NEGATIVE-expectancy")
            print("    on holdout — fills cluster in downtrends (adverse selection).")
            print("  ⚠ Width is REGIME-DEPENDENT (quarterly 15th-pct floor ranged")
            print("    6.0%-14.8% on 2024-26 data). A typical month, not a floor.")
    except Exception as e:
        print(f"\n  (containment band unavailable: {str(e)[:60]})")
    print()


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


def is_market_open(now=None):
    """True during NSE cash-session hours (Mon-Fri 09:15-15:30 IST).

    Shared so every caller uses ONE definition. Written 2026-08-05 after a real
    corruption of sr_daily_log.csv: the logger fetched a "live" quote at any
    hour, and Kite keeps serving a last-traded price after 15:30 that need not
    equal the official close. That wrote ABCAPITAL CMP 420.00 on a day whose
    close was 417.00 and whose prior close was 424.35 — matching neither
    session. Since CMP drives level selection, the resulting S1/R1 duplicated
    the previous day's and looked like the pipeline had run twice.

    Deliberately does NOT consult a holiday calendar: this gates whether to ASK
    for a live price, and on a holiday the fetch simply returns nothing usable
    and the caller falls back to the last close. Adding a calendar here would
    couple quote-fetching to calendar data for no behavioural gain.
    """
    now = now or datetime.now()
    if now.weekday() > 4:
        return False
    mins = now.hour * 60 + now.minute
    return 9 * 60 + 15 <= mins <= 15 * 60 + 30


def drop_partial_candle(df, now=None):
    """Drop the last bar if it is TODAY and the session hasn't closed yet.

    A mid-session bar is a PARTIAL candle: its High/Low/Close are whatever has
    printed so far, not the day's. Feeding it to the pivot detector invents
    swing points that will not survive the close, so every level built on it is
    provisional in a way nothing downstream would flag.

    sr_daily_logger has carried this guard for a while, but the interactive
    path (analyse / analyse_table, i.e. exactly what an intraday run
    uses) did not — so the tool most likely to be run mid-session was the one
    least protected. Shared here so both use one implementation.

    NSE closes 15:30 IST; 16:00 is used as the cutoff to stay clear of the
    closing-auction settle, matching sr_daily_logger.
    """
    if df is None or len(df) == 0:
        return df
    now = now or datetime.now()
    if now.weekday() <= 4 and now.hour < 16 and df.index[-1].date() == now.date():
        return df.iloc[:-1]
    return df


def fetch_live_prices(symbols):
    """({SYMBOL: price}, {SYMBOL: source}) from live_quotes.py, for use as CMP.

    Only genuinely live quotes are returned — live_quotes falls back to the
    last CSV close and flags it stale, and a stale "live" price is just the
    close wearing a costume. Dropping those makes analyse_table fall through
    to its normal close-based path instead of implying a freshness it lacks.

    The SOURCE is returned alongside because "live" is not one thing: a Kite
    Connect tick is real-time, while yfinance is ~15 minutes behind. Both
    report stale=False, so a caller that only checks staleness silently treats
    a quarter-hour-old price as current. The display layer marks the delayed
    ones rather than hiding the difference.
    """
    try:
        from live_quotes import (get_quotes_detail, SOURCE_KITE, SOURCE_YF,
                                 SOURCE_CSV)
    except Exception as e:
        print(f"  ⚠️  live quotes unavailable ({e}) — using last CSV close.")
        return None, {}

    syms = [s.strip().upper() if s.strip().upper().endswith(".NS")
            else s.strip().upper() + ".NS" for s in symbols]
    out, sources, stale = {}, {}, []
    for sym, (price, is_stale, source) in get_quotes_detail(syms).items():
        plain = sym.replace(".NS", "")
        if price is None:
            continue
        if is_stale or source == SOURCE_CSV:
            stale.append(plain)
            continue
        out[plain] = float(price)
        sources[plain] = source
    if stale:
        print(f"  ⚠️  stale quote (using CSV close) for: {', '.join(sorted(stale))}")
    delayed = sorted(s for s, src in sources.items() if src == SOURCE_YF)
    if delayed:
        print(f"  ⚠️  ~15-min DELAYED quote (yfinance, Kite unavailable) for: "
              f"{', '.join(delayed)}")
    return (out or None), sources


# ──────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────

def main():
    argv    = sys.argv[1:]
    verbose = "--verbose" in argv

    # --as-of YYYY-MM-DD: pretend the run happened on that date (testing the
    # shrinking horizon without waiting for the calendar).
    as_of = None
    if "--as-of" in argv:
        i = argv.index("--as-of")
        as_of = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]

    # Live quotes are the DEFAULT (2026-08-04). The tool answers "where is this
    # stock relative to its levels, right now", so the last CSV close is the
    # wrong reference whenever a real quote is obtainable. --no-live forces the
    # close, and `--live` is still accepted so existing muscle memory and any
    # scripts that pass it keep working.
    #
    # Live is suppressed automatically under --as-of: that flag asks about a
    # DIFFERENT date, and stamping today's price onto a past or future horizon
    # would silently mix two points in time.
    use_live = "--no-live" not in argv and as_of is None
    argv = [a for a in argv if a not in ("--live", "--no-live")]

    args = [a for a in argv if not a.startswith("--")]
    if args:
        symbols = args
    else:
        raw     = input("Enter symbols: ")
        symbols = [s.strip() for s in raw.replace(",", " ").split() if s.strip()]
    if not symbols:
        print("No symbols."); return

    live_prices, live_sources = None, {}
    if use_live:
        live_prices, live_sources = fetch_live_prices(symbols)
    elif as_of is not None and "--no-live" not in sys.argv:
        print(f"  ⓘ --as-of {as_of}: using that date's close, not a live quote.")

    if verbose:
        analyse(symbols)
    else:
        analyse_table(symbols, as_of=as_of, live_prices=live_prices,
                      live_sources=live_sources)


if __name__ == "__main__":
    main()