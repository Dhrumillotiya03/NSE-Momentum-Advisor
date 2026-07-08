"""
Canonical single source of truth for the momentum strategy's data access,
regime detection, scoring, confidence lookup, and S/R access.

Every advisor (recommend.py, full_advisor.py, the AI assistant tools) must
import from here instead of re-implementing its own copy. This is the fix for
the divergence bug: decision_engine.py used an abandoned 5-factor blend while
recommend.py/full_advisor.py had already moved to the validated ret_126/vol_63
formula, and nothing enforced they stay in sync.

No behavior change versus recommend.py's pre-refactor logic — this is a
consolidation, not a strategy change.
"""
import os
import json
import numpy as np
import pandas as pd

import strategy_config as sc
from confidence_table import load as load_confidence_table, confidence_for_score
from support_resistance import get_levels, strength_label, reach_probability_v2

DATA_DIR    = "../data/"
PRICE_DIR   = DATA_DIR + "price_data/"
INDEX_PATH  = DATA_DIR + "index_data/nifty50.csv"
SECTOR_FILE = DATA_DIR + "sectors.json"
STATE_PATH  = DATA_DIR + "portfolio_state.json"

LOOKBACK = sc.LOOKBACK
HOLD     = sc.HOLD
VOL_WIN  = sc.VOL_WINDOW
UNIVERSE_TOP_N = sc.UNIVERSE_TOP_N
UNIVERSE_TURNOVER_WINDOW = sc.UNIVERSE_TURNOVER_WINDOW


# ---------- Data ----------

def load_stock(symbol):
    path = PRICE_DIR + f"{symbol}.csv"
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, parse_dates=["Date"])
    # A malformed/truncated CSV row (interrupted download write) can leave a
    # literal non-date string in Date that parse_dates silently fails on
    # without producing NaT — guard explicitly (see SUNDARMFIN.NS incident).
    df = df[pd.to_datetime(df["Date"], errors="coerce").notna()]
    for col in ["Close", "High", "Low", "Open", "Volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Close"]).sort_values("Date").set_index("Date")
    return df


def load_index():
    df = pd.read_csv(INDEX_PATH, parse_dates=["Date"], low_memory=False)
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    return df.dropna(subset=["Close"]).sort_values("Date").set_index("Date")["Close"]


def load_sector_map():
    if not os.path.exists(SECTOR_FILE):
        return {}
    with open(SECTOR_FILE) as f:
        raw = json.load(f)
    return {sym: sec for sec, syms in raw.items() for sym in syms}


def load_portfolio_state():
    with open(STATE_PATH) as f:
        return json.load(f)


# ---------- Market regime (breadth-gated) ----------

def market_breadth_pct():
    """% of universe trading above its own 200DMA (right now)."""
    above, total = 0, 0
    for f in os.listdir(PRICE_DIR):
        if not f.endswith(".csv"):
            continue
        try:
            df = pd.read_csv(PRICE_DIR + f, usecols=["Date", "Close"], parse_dates=["Date"])
            close = pd.to_numeric(df["Close"], errors="coerce").dropna()
            if len(close) < 200:
                continue
            ma200 = close.rolling(200).mean().iloc[-1]
            total += 1
            if close.iloc[-1] > ma200:
                above += 1
        except Exception:
            continue
    return above / total if total else np.nan


def market_regime():
    """Returns (regime, breadth) where regime is BULL/SIDEWAYS/BEAR."""
    index = load_index()
    ma50 = index.rolling(50).mean().iloc[-1]
    ma200 = index.rolling(200).mean().iloc[-1]
    price = index.iloc[-1]

    if price > ma50 > ma200:
        breadth = market_breadth_pct()
        if not np.isnan(breadth) and breadth < sc.BREADTH_BULL_MIN:
            return "SIDEWAYS", breadth
        return "BULL", breadth
    if price < ma200:
        return "BEAR", market_breadth_pct()
    return "SIDEWAYS", market_breadth_pct()


current_regime = market_regime   # alias for callers migrated from recommend.py


# ---------- Scoring ----------

def compute_score(df):
    """Returns dict(score, ret_6m, ret_3m, vol_63, rsi, price) or None if ineligible.

    Eligibility: positive 6m AND 3m momentum, price above 50DMA.
    Score = ret_126 / vol_63 (validated walk-forward vs the abandoned 5-factor
    blend and vs price-only ML — see memory ml-does-not-beat-heuristic).
    """
    close = df["Close"]
    if len(close) < LOOKBACK + 1:
        return None

    ret_6m = close.iloc[-1] / close.iloc[-LOOKBACK - 1] - 1
    ret_3m = close.iloc[-1] / close.iloc[-64] - 1 if len(close) > 64 else np.nan

    if np.isnan(ret_6m) or np.isnan(ret_3m):
        return None
    if ret_6m <= 0 or ret_3m <= 0:
        return None

    ma50 = close.rolling(50).mean().iloc[-1]
    if np.isnan(ma50) or close.iloc[-1] < ma50:
        return None

    window = close.pct_change(fill_method=None).tail(VOL_WIN).dropna()
    if len(window) < 40:
        return None
    vol = window.std()
    if vol == 0 or np.isnan(vol):
        return None

    score = ret_6m / vol

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = (100 - 100 / (1 + rs)).iloc[-1]

    return {
        "score": score, "ret_6m": ret_6m, "ret_3m": ret_3m,
        "vol_63": vol, "rsi": rsi, "price": close.iloc[-1],
    }


def compute_rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).iloc[-1]


def compute_atr(df, period=14):
    high, low, close = df["High"], df["Low"], df["Close"]
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean().iloc[-1]


def liquid_universe(as_of=None):
    """Point-in-time F&O liquidity proxy: top UNIVERSE_TOP_N symbols by trailing
    median daily turnover (Close x Volume) over UNIVERSE_TURNOVER_WINDOW days,
    as of `as_of` (defaults to the latest available date per symbol — i.e. "today").
    Survivorship-free by construction: never looks at any list of F&O names,
    only at trailing liquidity, so applying this to historical dates doesn't
    leak future F&O-roster membership. See memory fno-universe-migration.

    Returns a set of symbols (without .csv). Callers scoring historical dates
    (e.g. backtest_portfolio.py) should NOT call this per-date on live CSVs —
    use the vectorized turnover-matrix approach there instead; this function is
    for the LIVE path (recommend.py, ai_assistant.py, full_advisor.py).
    """
    turnovers = {}
    for f in os.listdir(PRICE_DIR):
        if not f.endswith(".csv"):
            continue
        sym = f.replace(".csv", "")
        try:
            df = pd.read_csv(PRICE_DIR + f, usecols=["Date", "Close", "Volume"], parse_dates=["Date"])
        except (ValueError, KeyError):
            continue
        df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce")
        df = df.dropna(subset=["Close", "Volume"]).sort_values("Date")
        if as_of is not None:
            df = df[df["Date"] <= pd.Timestamp(as_of)]
        if len(df) < UNIVERSE_TURNOVER_WINDOW:
            continue
        turnover = (df["Close"] * df["Volume"]).tail(UNIVERSE_TURNOVER_WINDOW).median()
        if pd.isna(turnover) or turnover <= 0:
            continue
        turnovers[sym] = turnover

    ranked = sorted(turnovers, key=turnovers.get, reverse=True)
    return set(ranked[:UNIVERSE_TOP_N])


def scan_universe():
    """Score every stock in the F&O-liquidity-gated universe. Returns
    {symbol: score_dict} for eligible names."""
    gated = liquid_universe()
    results = {}
    for sym in gated:
        df = load_stock(sym)
        if df is None or len(df) < LOOKBACK + 60:
            continue
        r = compute_score(df)
        if r is not None:
            results[sym] = r
    return results


def position_size(capital, weight_within_book, exposure):
    """Rupee allocation for one name given regime exposure and its weight in the book."""
    return capital * exposure * weight_within_book


# ---------- Confidence (empirical base rates) ----------

def load_confidence():
    return load_confidence_table()


def confidence_for(score, regime):
    """Returns (decile_stats, regime_stats) for a given live score and regime."""
    table = load_confidence()
    decile_stats = confidence_for_score(table, score)
    regime_stats = table["regime_stats"].get(regime, {})
    return decile_stats, regime_stats


# ---------- S/R access ----------

def sr_levels(df, symbol=None, fast=False):
    """Wraps support_resistance.get_levels -> (support, resistance, s_strength, r_strength).
    Pass fast=True in any backtest loop over many stocks/dates — see CLAUDE.md gotchas,
    wick_rejection_score's iterrows path hangs without it."""
    return get_levels(df, symbol=symbol, fast=fast)


def sr_reach_probability(df, level, direction, forward_days=21):
    """Wraps support_resistance.reach_probability_v2 -> (prob:int|None, n:int)."""
    return reach_probability_v2(df, level, direction, forward_days=forward_days)
