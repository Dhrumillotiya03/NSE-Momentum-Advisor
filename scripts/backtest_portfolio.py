import os
import json
import pandas as pd
import numpy as np

import strategy_config as sc

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

    # Drop stocks missing more than 20% of dates in this window
    matrix = matrix.loc[:, matrix.isna().mean() <= 0.20]

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

            # Need valid price now and LOOKBACK ago. (An older version also
            # required a valid price at i+HOLD — a LOOKAHEAD: it peeked 21
            # days into the future and silently excluded names that stop
            # trading mid-hold, i.e. exactly the delisting/suspension
            # blow-ups. Removing it costs ~0.4pp CAGR on the survivor panel
            # — that was phantom return. See research_survivorship.py.)
            price_now  = matrix[sym].iloc[i]
            price_past = matrix[sym].iloc[i - LOOKBACK]

            if pd.isna(price_now) or pd.isna(price_past):
                continue
            if price_past == 0:
                continue

            ret = price_now / price_past - 1

            # Both 3m and 6m momentum must be positive — filters spike-and-crash stocks
            price_3m = matrix[sym].iloc[i - 63]
            if pd.isna(price_3m) or price_3m == 0:
                continue
            ret_3m = price_now / price_3m - 1
            if ret <= 0 or ret_3m <= 0:
                continue

            # Must be above 20-day MA — confirms uptrend
            ma50 = matrix[sym].iloc[i - 50:i].mean()
            if pd.isna(ma50) or price_now < ma50:
                continue

            window = matrix[sym].iloc[i - 63:i].pct_change(fill_method=None).dropna()
            if len(window) < 40:
                continue
            vol = window.std()
            if vol == 0 or np.isnan(vol):
                continue

            scores[sym] = ret / vol
            vols[sym]   = vol

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

def run_backtest_laggards_only(matrix, index, turnover_matrix=None, exposure_fn=None,
                               skip_days=0):
    """Same selection/sizing/regime logic as run_backtest, but positions
    still in the new top-N carry over (rebalanced to target weight, cost on
    the delta only) instead of being sold and rebought every 21 days.

    skip_days: measure the momentum legs up to i - skip_days instead of i
    (the academic '12-2' construction — the most recent month contains
    short-term reversal, not momentum). 0 = production behavior; nonzero is
    research-only (research_skip_month.py) unless explicitly adopted."""
    dates = matrix.index
    n_dates = len(dates)
    breadth = compute_breadth_series(matrix)
    sector_map = load_sector_map()
    if turnover_matrix is None:
        turnover_matrix = load_turnover_matrix(matrix)

    capital = float(INITIAL_CAPITAL)
    equity = []
    book = {}   # sym -> {entry_price, shares, last_price, cur_value}
    # idle cash accrues the liquid-ETF yield each 21d period (see
    # strategy_config.CASH_YIELD — includes the stop-proceeds approximation)
    cash_growth = (1 + sc.CASH_YIELD) ** (HOLD / 252)

    for i in range(LOOKBACK + 21, n_dates - HOLD, HOLD):
        date = dates[i]
        regime = get_regime(index, date, breadth)
        gated_symbols = liquid_symbols_at(turnover_matrix, i) & set(matrix.columns)

        scores, vols = {}, {}
        for sym in gated_symbols:
            price_now = matrix[sym].iloc[i]
            price_past = matrix[sym].iloc[i - LOOKBACK]
            if pd.isna(price_now) or pd.isna(price_past) or price_past == 0:
                continue
            price_ref = price_now
            if skip_days:
                price_ref = matrix[sym].iloc[i - skip_days]
                if pd.isna(price_ref) or price_ref <= 0:
                    continue
            ret = price_ref / price_past - 1
            price_3m = matrix[sym].iloc[i - 63]
            if pd.isna(price_3m) or price_3m == 0:
                continue
            ret_3m = price_ref / price_3m - 1
            if ret <= 0 or ret_3m <= 0:
                continue
            ma50 = matrix[sym].iloc[i - 50:i].mean()
            if pd.isna(ma50) or price_now < ma50:
                continue
            window = matrix[sym].iloc[i - 63:i].pct_change(fill_method=None).dropna()
            if len(window) < 40:
                continue
            vol = window.std()
            if vol == 0 or np.isnan(vol):
                continue
            scores[sym] = ret / vol
            vols[sym] = vol

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

        inv = {s: 1.0 / vols[s] for s in top}
        tot = sum(inv.values())
        w = {s: min(v / tot, sc.MAX_WEIGHT) * tot for s, v in inv.items()}
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
            stopped = False
            for off in range(1, HOLD + 1):
                idx = i + off
                if idx >= n_dates:
                    break
                p = col.iloc[idx]
                if pd.isna(p):
                    continue
                if p < entry_ref * CATASTROPHIC_STOP:
                    proceeds = pos["shares"] * p * (1 - COST)
                    capital += proceeds
                    del book[s]
                    stopped = True
                    break
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
        engine, label = run_backtest_gold_blend, (
            f"laggards_only + {sc.GOLD_ALLOC:.0%} gold + {sc.INTL_ALLOC:.0%} intl (production default)")

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
