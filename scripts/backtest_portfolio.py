import os
import json
import pandas as pd
import numpy as np

import strategy_config as sc
from core import momentum_score

DATA_DIR    = "../data/price_data/"
INDEX_PATH  = "../data/index_data/nifty50.csv"
SECTOR_FILE = "../data/sectors.json"

INITIAL_CAPITAL = 1_000_000
LOOKBACK        = sc.LOOKBACK
HOLD            = sc.HOLD
COST            = sc.COST

CATASTROPHIC_STOP = sc.CATASTROPHIC_STOP   # wide tail circuit breaker (-18%)

UNIVERSE_TOP_N = sc.UNIVERSE_TOP_N
UNIVERSE_TURNOVER_WINDOW = sc.UNIVERSE_TURNOVER_WINDOW


# ---------- Load aligned price matrix ----------

def load_price_matrix():
    frames = {}

    for f in os.listdir(DATA_DIR):
        if not f.endswith(".csv"):
            continue
        sym = f.replace(".csv", "")
        try:
            df = pd.read_csv(
                DATA_DIR + f,
                parse_dates=["Date"],
                low_memory=False
            )
            df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
            # A malformed/truncated CSV row (interrupted download write) can
            # leave a literal non-date string in Date that parse_dates
            # silently fails on without producing NaT — guard explicitly.
            df = df[pd.to_datetime(df["Date"], errors="coerce").notna()]
            df = df.dropna(subset=["Close"]).sort_values("Date")
            df = df.set_index("Date")["Close"]
            if len(df) > LOOKBACK + HOLD + 50:
                frames[sym] = df
        except:
            continue

    # Build matrix — all stocks on same calendar dates
    matrix = pd.DataFrame(frames).sort_index()

    # Use the FULL available history (not a trailing 3y window). A rolling
    # window silently shifts every time data is re-downloaded, which is how
    # the old documented Sharpe went stale. Full history = a stable, honest,
    # multi-regime track record. (Override with BACKTEST_YEARS env var.)
    yrs = os.environ.get("BACKTEST_YEARS")
    if yrs:
        cutoff = matrix.index[-1] - pd.DateOffset(years=int(yrs))
        matrix = matrix[matrix.index >= cutoff]

    # Forward fill up to 5 days (handles holidays/halts)
    matrix = matrix.ffill(limit=5)

    # Drop stocks missing more than 20% of dates SINCE THEIR LISTING (not
    # over the full 2015-2026 panel). The old global test measured NaN share
    # against the whole matrix's date range, so any name listed after ~2020
    # necessarily has >20% leading NaN and was dropped for its ENTIRE
    # history — silently shrinking the tradeable/backtestable universe as
    # the index ages. Verified 2026-08-01: of 185 names this dropped, 100%
    # were pure leading-NaN (recent listing), 0% had genuine interior gaps —
    # so this is a coverage bug, not a data-quality relaxation. The engines
    # were ALREADY point-in-time safe for this (momentum_score/
    # liquid_symbols_at both return None/skip on insufficient history up to
    # bar i), so a name just enters scoring once it has enough real bars.
    def _post_listing_nan_frac(col):
        first = col.first_valid_index()
        if first is None:
            return 1.0
        return col.loc[first:].isna().mean()

    keep = matrix.apply(_post_listing_nan_frac) <= 0.20
    matrix = matrix.loc[:, keep]

    return matrix


# ---------- Load aligned turnover matrix (Close x Volume, for the F&O liquidity gate) ----------
#
# Point-in-time proxy for "would this name have had listed F&O" — see
# strategy_config.py's Universe gate comment and memory fno-universe-migration.
# Survivorship-free: at each historical rebalance date, only trailing turnover
# up to that date is used (no knowledge of which names are liquid in the future).

def load_turnover_matrix(price_matrix):
    frames = {}
    for sym in price_matrix.columns:
        path = DATA_DIR + f"{sym}.csv"
        if not os.path.exists(path):
            continue
        try:
            df = pd.read_csv(path, usecols=["Date", "Close", "Volume"], parse_dates=["Date"], low_memory=False)
        except (ValueError, KeyError):
            continue
        df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce")
        df = df.dropna(subset=["Close", "Volume"]).sort_values("Date").set_index("Date")
        turnover = df["Close"] * df["Volume"]
        frames[sym] = turnover

    matrix = pd.DataFrame(frames).reindex(price_matrix.index)
    matrix = matrix.ffill(limit=5)
    return matrix


def liquid_symbols_at(turnover_matrix, idx, window=None, top_n=None):
    """Top-N symbols by trailing median turnover, using only rows up to and
    including idx (no lookahead). idx is a positional index into turnover_matrix."""
    window = window or UNIVERSE_TURNOVER_WINDOW
    top_n = top_n or UNIVERSE_TOP_N
    start = max(0, idx - window + 1)
    trailing = turnover_matrix.iloc[start:idx + 1]
    medians = trailing.median(skipna=True)
    medians = medians.dropna()
    ranked = medians.sort_values(ascending=False)
    return set(ranked.index[:top_n])


# ---------- Sector map (for the diversification cap) ----------

def load_sector_map():
    if not os.path.exists(SECTOR_FILE):
        return {}
    with open(SECTOR_FILE) as f:
        raw = json.load(f)
    return {sym: sec for sec, syms in raw.items() for sym in syms}


def select_top_n_capped(scores, n, sector_map, max_per_sector):
    """Greedy top-N selection by descending score, skipping names once their
    sector has hit max_per_sector, then continuing further down the ranked
    list to still fill n slots. Unmapped symbols are grouped into a single
    'UNMAPPED' bucket so they can't dominate the book uncapped either."""
    ranked = sorted(scores, key=scores.get, reverse=True)
    sector_count = {}
    selected = []
    for sym in ranked:
        if len(selected) >= n:
            break
        sec = sector_map.get(sym, "UNMAPPED")
        if sector_count.get(sec, 0) >= max_per_sector:
            continue
        selected.append(sym)
        sector_count[sec] = sector_count.get(sec, 0) + 1
    return selected


# ---------- Load index ----------

def load_index():
    df = pd.read_csv(INDEX_PATH, parse_dates=["Date"], low_memory=False)
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df = df.dropna(subset=["Close"]).sort_values("Date").set_index("Date")["Close"]
    return df


# ---------- Regime ----------

def compute_breadth_series(matrix):
    """%% of universe trading above its own 200DMA, indexed by date."""
    ma200 = matrix.rolling(200).mean()
    return (matrix > ma200).mean(axis=1)


def get_regime(index, date, breadth=None):
    past = index[index.index <= date]
    if len(past) < 200:
        return "UNKNOWN"
    price = past.iloc[-1]
    ma50  = past.iloc[-50:].mean()
    ma200 = past.iloc[-200:].mean()

    if price > ma50 > ma200:
        if breadth is not None:
            past_breadth = breadth[breadth.index <= date]
            b = past_breadth.iloc[-1] if len(past_breadth) else np.nan
            # Index looks bullish but participation is weak (<50% of stocks
            # above their own 200DMA) — demote to SIDEWAYS sizing. Catches
            # narrow-leadership tops before the index MA itself rolls over.
            if not np.isnan(b) and b < sc.BREADTH_BULL_MIN:
                return "SIDEWAYS"
        return "BULL"
    if price < ma200:
        return "BEAR"
    return "SIDEWAYS"


# ---------- Regime hysteresis (RESEARCH, 2026-08-01) ----------
#
# get_regime is a same-day flip on the 50/200DMA crossing — measured
# 2026-08-01: at rebalance cadence (137 samples over full history), 11 of 45
# regime "runs" (24%) last exactly ONE rebalance period before reverting, and
# the raw daily signal flips 270 times over the full history. That's genuine
# whipsaw right at the MA boundary, not a rare edge case, so it's worth
# testing an N-day confirmation delay.
#
# NOTE: symmetric breadth-gating for BEAR/SIDEWAYS (extending the existing
# BULL-only weak-breadth demotion) was considered and DROPPED before building
# anything — measured first: breadth level has ~zero-to-slightly-NEGATIVE
# correlation with forward 21d index return within BEAR (-0.066, n=30) and
# SIDEWAYS (-0.114, n=34) periods, the opposite sign a "weak breadth confirms
# bearishness" gate would assume. Only the existing BULL-side demotion has a
# measured basis; hysteresis is the only piece worth testing.

def confirmed_regime_fn(index, breadth, confirm_days=5):
    """Returns a regime_fn(index, date, breadth) closure implementing N-day
    hysteresis: a regime change only takes effect once the RAW get_regime
    signal has agreed for `confirm_days` consecutive trading sessions,
    otherwise the prior confirmed regime persists. Precomputes the raw daily
    regime series once (vectorized) rather than recomputing get_regime's
    lookback windows on every call."""
    ma50 = index.rolling(50).mean()
    ma200 = index.rolling(200).mean()
    price = index
    raw = pd.Series("SIDEWAYS", index=index.index)
    bull_mask = (price > ma50) & (ma50 > ma200)
    bear_mask = price < ma200
    raw[bull_mask] = "BULL"
    raw[bear_mask] = "BEAR"
    if breadth is not None:
        b_aligned = breadth.reindex(index.index).ffill()
        demote = bull_mask & (b_aligned < sc.BREADTH_BULL_MIN)
        raw[demote] = "SIDEWAYS"
    if len(index) >= 200:
        raw.iloc[:199] = "UNKNOWN"
    else:
        raw[:] = "UNKNOWN"

    confirmed = raw.copy()
    current = raw.iloc[0]
    run_val = raw.iloc[0]
    run_len = 1
    out = [current]
    for v in raw.iloc[1:]:
        if v == run_val:
            run_len += 1
        else:
            run_val, run_len = v, 1
        if run_len >= confirm_days and v != current:
            current = v
        out.append(current)
    confirmed[:] = out

    def regime_fn(index_arg, date, breadth_arg):
        past = confirmed[confirmed.index <= date]
        return past.iloc[-1] if len(past) else "UNKNOWN"
    return regime_fn


# ---------- Exit simulation ----------
#
# Mirrors exit_engine.py's live logic. Profit-oriented config: NO tight
# intra-hold exits (trailing/50MA whipsaw out of momentum trends and cost
# Sharpe + CAGR). Only a WIDE catastrophic stop (-18%) as a tail circuit
# breaker for fraud/news gaps between rebalances — validated to slightly
# IMPROVE all metrics vs no exit at all. Otherwise positions run to the next
# 21-day rebalance so winners compound.

def simulate_position_exit(matrix, sym, entry_idx, entry_price, max_hold_days):
    n = len(matrix)
    col = matrix[sym]
    last_price = entry_price

    for offset in range(1, max_hold_days + 1):
        idx = entry_idx + offset
        if idx >= n:
            break
        price = col.iloc[idx]
        if pd.isna(price):
            continue
        last_price = price
        if price < entry_price * CATASTROPHIC_STOP:
            return price / entry_price - 1

    final_idx = min(entry_idx + max_hold_days, n - 1)
    final_price = col.iloc[final_idx]
    if pd.isna(final_price):
        # series terminated mid-hold (suspension/delisting/merger/halt):
        # exit at the last traded price, NOT flat (0.0 was "money back",
        # which understated terminal losses)
        return last_price / entry_price - 1
    return final_price / entry_price - 1


# ---------- Backtest ----------

def run_backtest(matrix, index, turnover_matrix=None, exposure_fn=None):
    """exposure_fn(date, regime, exp) -> exp: optional hook to modify the
    regime exposure at each rebalance (e.g. VIX overlay research). Default
    None = exact original behavior."""

    dates   = matrix.index
    n_dates = len(dates)
    capital = float(INITIAL_CAPITAL)
    equity  = []
    breadth = compute_breadth_series(matrix)
    sector_map = load_sector_map()

    if turnover_matrix is None:
        turnover_matrix = load_turnover_matrix(matrix)

    for i in range(LOOKBACK + 21, n_dates - HOLD, HOLD):

        date   = dates[i]
        regime = get_regime(index, date, breadth)

        # ---- F&O liquidity gate: only trailing-liquid names as of `date` ----
        # Intersect with matrix.columns — a sliced/windowed price matrix (e.g.
        # walk_forward.py's per-window sub_matrix) may have dropped a symbol
        # the turnover matrix still ranks (different NaN-coverage filters).
        gated_symbols = liquid_symbols_at(turnover_matrix, i) & set(matrix.columns)

        # ---- Score all stocks at this exact calendar date ----
        scores = {}
        vols   = {}

        for sym in gated_symbols:
            # Per-name score routes through the ONE canonical scorer shared
            # with the live path (core.momentum_score) — never re-inline it,
            # the two copies drifted once already (2026-07-17 unification).
            # (An older inline version also required a valid price at i+HOLD
            # — a LOOKAHEAD worth ~0.4pp phantom CAGR, removed 2026-07-12;
            # see research_survivorship.py.)
            r = momentum_score(matrix[sym].iloc[:i + 1])
            if r is None:
                continue
            scores[sym] = r["score"]
            vols[sym]   = r["vol_63"]

        # ---- Regime-based sizing (from strategy_config) ----
        n   = sc.REGIME_NAMES[regime]
        exp = sc.REGIME_EXPOSURE[regime]
        if exposure_fn is not None:
            exp = exposure_fn(date, regime, exp)

        if len(scores) < n:
            equity.append(capital)
            continue

        # Sector-capped top-N: greedy by score, skip once a sector hits
        # MAX_PER_SECTOR, backfill further down the ranked list. May return
        # fewer than n if too few distinct sectors are eligible — the rest of
        # the loop handles any length of `top` correctly.
        top = select_top_n_capped(scores, n, sector_map, sc.MAX_PER_SECTOR)
        if not top:
            equity.append(capital)
            continue

        # ---- Inverse vol weighting ----
        inv_vols    = {s: 1.0 / vols[s] for s in top}
        total_inv_v = sum(inv_vols.values())
        max_weight = sc.MAX_WEIGHT
        for s in inv_vols:
            raw_w = inv_vols[s] / total_inv_v
            inv_vols[s] = min(raw_w, max_weight) * total_inv_v
        total_inv_v = sum(inv_vols.values())
        invested    = capital * exp
        new_capital = capital - invested

        # ---- Simulate hold period (with daily exit checks) ----
        for sym in top:
            weight  = inv_vols[sym] / total_inv_v
            pos_val = invested * weight

            entry = matrix[sym].iloc[i]

            r = simulate_position_exit(matrix, sym, i, entry, HOLD)
            r -= 2 * COST
            new_capital += pos_val * (1 + r)

        # idle (unexposed) cash accrues the liquid-ETF yield over the hold
        new_capital += (capital - invested) * ((1 + sc.CASH_YIELD) ** (HOLD / 252) - 1)
        capital = new_capital
        equity.append(capital)

    return np.array(equity)


# ---------- Laggards-only rebalance ----------
#
# ADOPTED 2026-07-12 (was: hard close — sell+rebuy EVERY name every month
# even if still top-ranked). Satisfies the user's actual mandate (no
# INTER-month drift — a position silently carried 2-3 months with no
# re-evaluation) WITHOUT the pointless full round-trip on names that were
# going to be bought right back: still re-scores the ENTIRE universe every
# 21 days, still enforces the sector cap, still checks the -18% stop daily
# on every held name — only difference is a name still in the new top-N is
# rebalanced to its new target weight (cost on the DELTA only) instead of
# sold-and-rebought. See research_monthly_close_cost.py /
# monthly-close-cost-2026-07: costs ~0.8pp gross CAGR but SAVES ~3pp/yr net
# CAGR (fewer taxable events — NOT LTCG conversion, which barely fires:
# momentum's own turnover displaces names from top-N well before 365 days).

# ---------- Correlation-aware sizing (RESEARCH, 2026-08-01) ----------
#
# Production sizing is pure inverse-vol (1/vol_63, capped at MAX_WEIGHT,
# renormalized) — it treats two names as independent risk even when they move
# together. Measured 2026-08-01 on the current top-10 book: pairwise 126d
# return correlation mean 0.26 (up to 0.45), diversification ratio (avg
# single-name vol / portfolio vol) only 1.62 — real but leaves risk-reduction
# on the table relative to a sizing rule that accounts for it. This targets a
# STRUCTURAL gap (inverse-vol sizing provably ignores correlation), not a
# pattern mined from returns — the bar this project's hygiene memo asks for.
#
# risk_parity_weights: equal-risk-contribution weights from a SHRUNK
# covariance matrix. Shrinkage matters here — with ~63-126 daily returns and
# up to 10 names, the raw sample correlation matrix is noisy and a naive ERC
# solve will overreact to spurious correlation estimates. Uses Ledoit-Wolf-
# style shrinkage toward the diagonal (shrink correlations toward 0, keep
# each name's own variance), which is standard practice for exactly this
# small-sample regime.

def _shrunk_corr(returns, shrink=0.3):
    """Shrink the sample correlation matrix toward the identity (uncorrelated)
    by `shrink`. returns: DataFrame of daily returns, columns = symbols."""
    corr = returns.corr()
    n = len(corr)
    target = np.eye(n)
    shrunk = (1 - shrink) * corr.values + shrink * target
    return pd.DataFrame(shrunk, index=corr.index, columns=corr.columns)


def risk_parity_weights(returns, vols, names, shrink=0.3, max_iter=200):
    """Equal-risk-contribution weights over `names`, using shrunk correlation
    (see _shrunk_corr) and the SAME vol_63 estimates the rest of the engine
    uses (not returns.std(), to stay consistent with the canonical scorer).

    returns: DataFrame of daily returns for `names` (recent window, e.g. 63d).
    vols: dict {name: vol_63} — annualized-consistent per-name vol already
    computed by momentum_score.

    Falls back to plain inverse-vol (the production rule) if returns has too
    few rows/names for a stable solve, or if the iteration doesn't converge —
    silently degrading to the validated default is safer than a bad correlation
    estimate producing extreme weights.
    """
    names = list(names)
    if len(names) < 2 or returns is None or len(returns) < 20:
        inv = {s: 1.0 / vols[s] for s in names}
        tot = sum(inv.values())
        return {s: v / tot for s, v in inv.items()}

    sub = returns[names].dropna(how="all")
    if len(sub) < 20:
        inv = {s: 1.0 / vols[s] for s in names}
        tot = sum(inv.values())
        return {s: v / tot for s, v in inv.items()}
    sub = sub.fillna(0.0)

    corr = _shrunk_corr(sub, shrink=shrink).values
    sigma = np.array([vols[s] for s in names])
    cov = corr * np.outer(sigma, sigma)

    # ERC via simple multiplicative fixed-point iteration (Spinu-style):
    # start at inverse-vol, then converge so each name's risk contribution
    # (w_i * (cov @ w)_i) is equal.
    w = 1.0 / sigma
    w = w / w.sum()
    for _ in range(max_iter):
        mrc = cov @ w                      # marginal risk contribution
        port_var = w @ mrc
        if port_var <= 0 or np.any(mrc <= 0):
            inv = {s: 1.0 / vols[s] for s in names}
            tot = sum(inv.values())
            return {s: v / tot for s, v in inv.items()}
        rc = w * mrc                       # risk contribution per name
        target = port_var / len(names)
        w_new = w * (target / rc)
        w_new = np.clip(w_new, 1e-6, None)
        w_new = w_new / w_new.sum()
        if np.max(np.abs(w_new - w)) < 1e-8:
            w = w_new
            break
        w = w_new
    return dict(zip(names, w))


def run_backtest_laggards_only(matrix, index, turnover_matrix=None, exposure_fn=None,
                               skip_days=0, trail_stop=None, sizing_fn=None,
                               regime_fn=None, stage_days=1):
    """Same selection/sizing/regime logic as run_backtest, but positions
    still in the new top-N carry over (rebalanced to target weight, cost on
    the delta only) instead of being sold and rebought every 21 days.

    skip_days: measure the momentum legs up to i - skip_days instead of i
    (the academic '12-2' construction — the most recent month contains
    short-term reversal, not momentum). 0 = production behavior; nonzero is
    research-only (research_skip_month.py) unless explicitly adopted.

    trail_stop: if set (e.g. 0.85), exit intra-month when price falls that
    fraction below the position's HIGH SINCE ENTRY (a ratcheting giveback
    stop), instead of only the fixed -18%-from-entry catastrophic stop.
    None = production behavior. RESEARCH-ONLY until walk-forward validated —
    tight trailing exits have twice tested negative on this strategy.

    sizing_fn: optional callable(matrix, i, top, vols) -> {sym: weight in
    [0,1] summing to 1}, replacing the production inverse-vol sizing (see
    risk_parity_weights above for a correlation-aware alternative). None =
    production behavior (plain inverse-vol, MAX_WEIGHT-capped).

    regime_fn: optional callable(index, date, breadth) -> regime string,
    replacing get_regime (see confirmed_regime_fn below for an N-day
    hysteresis alternative). None = production behavior (raw get_regime,
    no confirmation delay).

    stage_days: for a brand NEW position (not a top-up of an existing
    holding), average the fill price over the next `stage_days` closes
    starting at i (equal-thirds-style staged entry) instead of filling the
    whole position at the single close on day i. Models reduced single-print
    risk from spreading a buy over a few sessions. 1 = production behavior
    (single-close fill). RESEARCH-ONLY — see the rejection note in
    strategy_config.py before re-testing."""
    dates = matrix.index
    n_dates = len(dates)
    breadth = compute_breadth_series(matrix)
    sector_map = load_sector_map()
    if turnover_matrix is None:
        turnover_matrix = load_turnover_matrix(matrix)
    regime_of = regime_fn if regime_fn is not None else get_regime

    capital = float(INITIAL_CAPITAL)
    equity = []
    book = {}   # sym -> {entry_price, shares, last_price, cur_value}
    # idle cash accrues the liquid-ETF yield each 21d period (see
    # strategy_config.CASH_YIELD — includes the stop-proceeds approximation)
    cash_growth = (1 + sc.CASH_YIELD) ** (HOLD / 252)

    for i in range(LOOKBACK + 21, n_dates - HOLD, HOLD):
        date = dates[i]
        regime = regime_of(index, date, breadth)
        gated_symbols = liquid_symbols_at(turnover_matrix, i) & set(matrix.columns)

        scores, vols = {}, {}
        for sym in gated_symbols:
            # Canonical shared scorer — see the comment in run_backtest.
            r = momentum_score(matrix[sym].iloc[:i + 1], skip_days=skip_days)
            if r is None:
                continue
            scores[sym] = r["score"]
            vols[sym] = r["vol_63"]

        n = sc.REGIME_NAMES[regime]
        exp = sc.REGIME_EXPOSURE[regime]
        if exposure_fn is not None:
            exp = exposure_fn(date, regime, exp)

        # mark existing book to today's price
        for s, pos in book.items():
            px = matrix[s].iloc[i]
            pos["last_price"] = px if not pd.isna(px) else pos["last_price"]
            pos["cur_value"] = pos["shares"] * pos["last_price"]
        book_value = sum(p["cur_value"] for p in book.values())

        if len(scores) < n:
            capital *= cash_growth
            equity.append(capital + book_value)
            continue

        top = set(select_top_n_capped(scores, n, sector_map, sc.MAX_PER_SECTOR))
        if not top:
            capital *= cash_growth
            equity.append(capital + book_value)
            continue

        held = set(book)
        drop, keep, new_names = held - top, held & top, top - held

        for s in drop:
            pos = book.pop(s)
            proceeds = pos["cur_value"] * (1 - COST)
            capital += proceeds

        if sizing_fn is not None:
            raw_w = sizing_fn(matrix, i, top, vols)
        else:
            inv = {s: 1.0 / vols[s] for s in top}
            tot = sum(inv.values())
            raw_w = {s: v / tot for s, v in inv.items()}
        # MAX_WEIGHT cap + renormalize applies regardless of sizing method —
        # it's a separate single-name concentration control, not part of the
        # sizing rule itself.
        w = {s: min(v, sc.MAX_WEIGHT) for s, v in raw_w.items()}
        tot2 = sum(w.values())
        w = {s: v / tot2 for s, v in w.items()}

        total_equity = capital + sum(book[s]["cur_value"] for s in keep)
        invested_target = total_equity * exp

        for s in keep:
            pos = book[s]
            target_val = invested_target * w[s]
            delta = target_val - pos["cur_value"]
            capital -= delta + abs(delta) * COST
            if delta > 0 and pos["last_price"] > 0:
                new_shares = pos["shares"] + delta / pos["last_price"]
                pos["entry_price"] = (pos["shares"] * pos["entry_price"] + delta) / new_shares
                pos["shares"] = new_shares
            elif pos["last_price"] > 0:
                pos["shares"] -= (-delta) / pos["last_price"]
            pos["cur_value"] = pos["shares"] * pos["last_price"]

        for s in new_names:
            target_val = invested_target * w[s]
            if stage_days > 1:
                window = matrix[s].iloc[i:min(i + stage_days, n_dates)]
                window = window.dropna()
                window = window[window > 0]
                px = float(window.mean()) if len(window) else np.nan
            else:
                px = matrix[s].iloc[i]
            if pd.isna(px) or px <= 0:
                continue
            capital -= target_val * (1 + COST)
            book[s] = {"entry_price": px, "shares": target_val / px,
                       "last_price": px, "cur_value": target_val}

        # simulate the hold window: -18% stop can fire on any held name
        for s in list(book):
            pos = book[s]
            col = matrix[s]
            entry_ref = pos["entry_price"]
            # high-water mark carries across rebalances for a held name, so a
            # trailing stop ratchets on the whole holding period, not just the
            # current 21-day window.
            peak = pos.get("peak", entry_ref)
            stopped = False
            for off in range(1, HOLD + 1):
                idx = i + off
                if idx >= n_dates:
                    break
                p = col.iloc[idx]
                if pd.isna(p):
                    continue
                if p > peak:
                    peak = p
                hit = p < entry_ref * CATASTROPHIC_STOP
                if trail_stop is not None and p < peak * trail_stop:
                    hit = True
                if hit:
                    proceeds = pos["shares"] * p * (1 - COST)
                    capital += proceeds
                    del book[s]
                    stopped = True
                    break
            pos_still = book.get(s)
            if pos_still is not None:
                pos_still["peak"] = peak
            if not stopped:
                final_idx = min(i + HOLD, n_dates - 1)
                fp = col.iloc[final_idx]
                if not pd.isna(fp):
                    pos["last_price"] = fp
                    pos["cur_value"] = pos["shares"] * fp

        capital *= cash_growth
        equity.append(capital + sum(p["cur_value"] for p in book.values()))

    return np.array(equity)


# ---------- ETF sleeve blend (production default since 2026-07-13) ----------
#
# GOLD_ALLOC of total capital sits in GOLDBEES and INTL_ALLOC in MON100
# (Nasdaq-100 INR), each rebalanced back to target every rebalance; momentum
# runs on the rest. Blended at the period-return level (equivalent to
# sleeve-level capital accounting when all sleeves are marked on the same
# grid), with COST charged on the approximate inter-sleeve rebalancing
# turnover. See strategy_config.GOLD_ALLOC / INTL_ALLOC comments +
# research_lowvol_sleeve.py / research_intl_sleeve.py for the evidence and
# the exceptional-decade caveats on both sleeves' returns.

GOLD_PATH = f"../data/etf_data/{sc.GOLD_SYMBOL}.csv"


def load_etf_period_returns(path, matrix):
    """ETF marked at each rebalance grid point + HOLD (where the engines'
    equity points sit), so returns align 1:1 with equity[k+1]/equity[k]."""
    df = pd.read_csv(path, parse_dates=["Date"], low_memory=False)
    df["Close"] = pd.to_numeric(df["Close"], errors="coerce")
    df = df[pd.to_datetime(df["Date"], errors="coerce").notna()]
    df = df.dropna(subset=["Close"]).sort_values("Date").set_index("Date")["Close"]
    # Spike guard: a failed split-adjustment in a refetched CSV (seen once,
    # 2019-12-19/20 at exactly 1/100th price) injects fake -99%/+10000%
    # returns that blow up any blend using this series. Drop days deviating
    # >3x from the centered 11-day rolling median before using the series.
    med = df.rolling(11, center=True, min_periods=3).median()
    ratio = df / med
    df = df[(ratio < 3) & (ratio > 1 / 3)]
    gold = df.reindex(matrix.index).ffill()

    marks = []
    for i in range(LOOKBACK + 21, len(matrix) - HOLD, HOLD):
        marks.append(gold.iloc[min(i + HOLD, len(matrix) - 1)])
    marks = pd.Series(marks).ffill().bfill()
    return marks.values[1:] / marks.values[:-1] - 1


def load_gold_period_returns(matrix):
    return load_etf_period_returns(GOLD_PATH, matrix)


def run_backtest_gold_blend(matrix, index, turnover_matrix=None, exposure_fn=None):
    """Production engine: momentum laggards-only on
    (1 - GOLD_ALLOC - INTL_ALLOC) + GOLDBEES + MON100 sleeves, each
    rebalanced to target weight every period. (Name kept from the gold-only
    version for walk_forward.py compatibility.)"""
    eq_m = run_backtest_laggards_only(matrix, index, turnover_matrix, exposure_fn)
    if len(eq_m) < 2:
        return eq_m
    r_m = eq_m[1:] / eq_m[:-1] - 1
    sleeve_rets = [r_m]
    weights = [1.0 - sc.GOLD_ALLOC - sc.INTL_ALLOC]
    for sym, alloc in [(sc.GOLD_SYMBOL, sc.GOLD_ALLOC), (sc.INTL_SYMBOL, sc.INTL_ALLOC)]:
        if alloc > 0:
            sleeve_rets.append(load_etf_period_returns(f"../data/etf_data/{sym}.csv", matrix))
            weights.append(alloc)

    n = min(len(x) for x in sleeve_rets)
    sleeve_rets = [x[:n] for x in sleeve_rets]
    r = sum(w * x for w, x in zip(weights, sleeve_rets))
    turnover = sum(w * np.abs(x - r) for w, x in zip(weights, sleeve_rets))
    r = r - turnover * COST
    return INITIAL_CAPITAL * np.concatenate([[1.0], np.cumprod(1 + r)])


# ---------- Performance ----------

def performance(equity):
    if len(equity) < 2:
        return None
    returns       = equity[1:] / equity[:-1] - 1
    total_return  = equity[-1] / equity[0] - 1
    years         = len(equity) * HOLD / 252
    annual_return = (1 + total_return) ** (1 / years) - 1
    volatility    = np.std(returns) * np.sqrt(252 / HOLD)
    sharpe        = annual_return / volatility if volatility > 0 else 0
    peak          = np.maximum.accumulate(equity)
    drawdown      = np.max((peak - equity) / peak)
    return total_return, annual_return, sharpe, drawdown, volatility, years


# ---------- Main ----------

def main():
    import sys
    if "--hard-close" in sys.argv:
        engine, label = run_backtest, "hard_close (legacy)"
    elif "--no-gold" in sys.argv:
        engine, label = run_backtest_laggards_only, "laggards_only, momentum sleeve only"
    else:
        sleeves = (f" + {sc.GOLD_ALLOC:.0%} gold + {sc.INTL_ALLOC:.0%} intl"
                   if sc.GOLD_ALLOC + sc.INTL_ALLOC > 0 else ", momentum-only (sleeves disabled)")
        engine, label = run_backtest_gold_blend, f"laggards_only{sleeves} (production default)"

    print("\n==============================")
    print(f"📊 REALISTIC PORTFOLIO BACKTEST  (engine: {label})")
    print("==============================")

    matrix = load_price_matrix()
    index  = load_index()
    turnover_matrix = load_turnover_matrix(matrix)

    print(f"Universe: {matrix.shape[1]} stocks | {len(matrix)} trading days")
    print(f"F&O liquidity gate: top {UNIVERSE_TOP_N} by trailing {UNIVERSE_TURNOVER_WINDOW}d turnover")

    equity = engine(matrix, index, turnover_matrix)
    if len(equity) == 0:
        print("⚠️ Not enough data")
        return

    total, annual, sharpe, dd, volatility, years = performance(equity)

    print(f"\nFinal Capital:  ₹{equity[-1]:,.0f}")
    print(f"Total Return:   {total:.2%}")
    print(f"Annual Return:  {annual:.2%}")
    print(f"Sharpe Ratio:   {sharpe:.2f}")
    print(f"Max Drawdown:   {dd:.2%}")

    nifty_annual  = 0.14
    nifty_3y      = (1 + nifty_annual) ** years - 1
    alpha         = annual - nifty_annual
    info_ratio    = alpha / (volatility if volatility > 0 else 1)

    print(f"\n--- Benchmark Comparison ---")
    print(f"Nifty 50 Annual:  {nifty_annual:.2%}")
    print(f"Nifty 50 3Y:      {nifty_3y:.2%}")
    print(f"Your Alpha:       {alpha:+.2%}")
    print(f"Info Ratio:       {info_ratio:.2f}")


if __name__ == "__main__":
    main()
