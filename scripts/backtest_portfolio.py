import os
import pandas as pd
import numpy as np

import strategy_config as sc
from support_resistance import get_levels, reach_probability_v2

DATA_DIR   = "../data/price_data/"
INDEX_PATH = "../data/index_data/nifty50.csv"

INITIAL_CAPITAL = 1_000_000
LOOKBACK        = sc.LOOKBACK
HOLD            = sc.HOLD
COST            = sc.COST

CATASTROPHIC_STOP = sc.CATASTROPHIC_STOP   # wide tail circuit breaker (-18%)


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

    for offset in range(1, max_hold_days + 1):
        idx = entry_idx + offset
        if idx >= n:
            break
        price = matrix[sym].iloc[idx]
        if pd.isna(price):
            continue
        if price < entry_price * CATASTROPHIC_STOP:
            return price / entry_price - 1

    final_idx = min(entry_idx + max_hold_days, n - 1)
    final_price = matrix[sym].iloc[final_idx]
    if pd.isna(final_price):
        return 0.0
    return final_price / entry_price - 1


# ---------- OHLC cache for the early-exit simulation ----------
#
# Early exit needs High/Low (support_resistance.get_levels) and RSI, which
# the Close-only `matrix` doesn't carry. Loaded once per symbol, lazily,
# and reused across the whole backtest.

_OHLC_CACHE = {}


def _load_ohlc(sym):
    if sym in _OHLC_CACHE:
        return _OHLC_CACHE[sym]
    path = DATA_DIR + f"{sym}.csv"
    if not os.path.exists(path):
        _OHLC_CACHE[sym] = None
        return None
    df = pd.read_csv(path, parse_dates=["Date"], low_memory=False)
    for col in ["Close", "High", "Low"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Close", "High", "Low"]).sort_values("Date").set_index("Date")
    _OHLC_CACHE[sym] = df
    return df


def _rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).iloc[-1]


def _early_exit_fires(ohlc, as_of_date, entry_price):
    """Same rule as exit_engine.check_early_exit, using only data up to and
    including as_of_date (no lookahead). ohlc is the full OHLC frame; sliced
    here per call so no future bar is ever visible."""
    df_upto = ohlc[ohlc.index <= as_of_date]
    if len(df_upto) < 60:
        return False

    price = df_upto["Close"].iloc[-1]
    gain = price / entry_price - 1
    if gain < sc.EARLY_EXIT_MIN_GAIN:
        return False

    support, resistance, s_str, r_str = get_levels(df_upto, fast=True)
    if r_str < sc.EARLY_EXIT_RSTRENGTH_MIN:
        return False
    dist_to_resistance = (resistance - price) / price
    if dist_to_resistance > sc.EARLY_EXIT_NEAR_RESISTANCE:
        return False

    rsi = _rsi(df_upto["Close"])
    if pd.isna(rsi) or rsi <= sc.RSI_OVERBOUGHT:
        return False

    prob, n = reach_probability_v2(df_upto, resistance, "up")
    if prob is None or prob > sc.EARLY_EXIT_REACH_PROB_MAX:
        return False

    return True


def simulate_position_exit_with_early(matrix, sym, entry_idx, entry_price, max_hold_days):
    """Same as simulate_position_exit but also checks the early "good exit"
    take-profit rule daily (EARLY_EXIT_* in strategy_config.py), in priority
    after the catastrophic stop. Used only for the WITH-early-exit backtest
    arm; the live default (EARLY_EXIT_ENABLED=False) never calls this."""
    n = len(matrix)
    ohlc = _load_ohlc(sym)

    for offset in range(1, max_hold_days + 1):
        idx = entry_idx + offset
        if idx >= n:
            break
        price = matrix[sym].iloc[idx]
        if pd.isna(price):
            continue
        if price < entry_price * CATASTROPHIC_STOP:
            return price / entry_price - 1

        if ohlc is not None:
            as_of_date = matrix.index[idx]
            if _early_exit_fires(ohlc, as_of_date, entry_price):
                return price / entry_price - 1

    final_idx = min(entry_idx + max_hold_days, n - 1)
    final_price = matrix[sym].iloc[final_idx]
    if pd.isna(final_price):
        return 0.0
    return final_price / entry_price - 1


# ---------- Backtest ----------

def run_backtest(matrix, index, early_exit=False):

    dates   = matrix.index
    n_dates = len(dates)
    capital = float(INITIAL_CAPITAL)
    equity  = []
    breadth = compute_breadth_series(matrix)

    for i in range(LOOKBACK + 21, n_dates - HOLD, HOLD):

        date   = dates[i]
        regime = get_regime(index, date, breadth)

        # ---- Score all stocks at this exact calendar date ----
        scores = {}
        vols   = {}

        for sym in matrix.columns:

            # Need valid price now, LOOKBACK ago, and at exit
            price_now  = matrix[sym].iloc[i]
            price_past = matrix[sym].iloc[i - LOOKBACK]
            price_exit = matrix[sym].iloc[i + HOLD]

            if pd.isna(price_now) or pd.isna(price_past) or pd.isna(price_exit):
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

        if len(scores) < n:
            equity.append(capital)
            continue

        top = sorted(scores, key=scores.get, reverse=True)[:n]

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

            if early_exit:
                r = simulate_position_exit_with_early(matrix, sym, i, entry, HOLD)
            else:
                r = simulate_position_exit(matrix, sym, i, entry, HOLD)
            r -= 2 * COST
            new_capital += pos_val * (1 + r)

        capital = new_capital
        equity.append(capital)

    return np.array(equity)


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
    compare = "--compare-early-exit" in sys.argv

    print("\n==============================")
    print("📊 REALISTIC PORTFOLIO BACKTEST")
    print("==============================")

    matrix = load_price_matrix()
    index  = load_index()

    print(f"Universe: {matrix.shape[1]} stocks | {len(matrix)} trading days")

    if not compare:
        equity = run_backtest(matrix, index, early_exit=False)
        if len(equity) == 0:
            print("⚠️ Not enough data")
            return
        _print_single(equity)
        return

    print(f"\nRunning WITHOUT early exit (current live config, EARLY_EXIT_ENABLED=False)...")
    equity_base = run_backtest(matrix, index, early_exit=False)
    print(f"Running WITH early exit (EARLY_EXIT_* rule from strategy_config.py)...")
    equity_early = run_backtest(matrix, index, early_exit=True)

    if len(equity_base) == 0 or len(equity_early) == 0:
        print("⚠️ Not enough data")
        return

    perf_base  = performance(equity_base)
    perf_early = performance(equity_early)

    print(f"\n{'='*60}")
    print(f"{'Metric':<16}{'WITHOUT early exit':>22}{'WITH early exit':>22}")
    print(f"{'='*60}")
    labels = ["Total Return", "Annual Return", "Sharpe", "Max Drawdown"]
    for i, label in enumerate(labels):
        b = perf_base[i]
        e = perf_early[i]
        if label == "Sharpe":
            print(f"{label:<16}{b:>22.2f}{e:>22.2f}")
        else:
            print(f"{label:<16}{b:>22.2%}{e:>22.2%}")
    print(f"{'='*60}")
    print("EARLY_EXIT_ENABLED stays False in strategy_config.py until this")
    print("comparison is reviewed and a decision is made from the numbers above.")


def _print_single(equity):
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
