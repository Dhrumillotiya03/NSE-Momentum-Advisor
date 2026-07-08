"""
Free live(ish) price quotes for the portfolio and exit checks.

Source order:
  1. yfinance fast_info — free, no API key, ~15-min delayed for NSE. For a
     21-day-cadence system whose only intra-month trigger is a wide -18%
     catastrophic stop, 15-min delay is operationally fine.
  2. Last close from ../data/price_data/<sym>.csv — flagged stale=True so
     callers can tell a live-ish quote from yesterday's close.

NSE's own quote API (and nsepython) was tried and is bot-walled from this
machine (403 / empty responses) — deliberately not used; don't re-add a
scraper that breaks every time NSE rotates its anti-bot checks.

Quotes are cached for CACHE_TTL_SECONDS so a scan over the whole book
doesn't hammer Yahoo. Batch with get_quotes(symbols) where possible.
"""
import os
import time

import pandas as pd

PRICE_DIR = "../data/price_data/"
CACHE_TTL_SECONDS = 60

_cache = {}   # symbol -> (timestamp, price, stale)


def _csv_last_close(symbol):
    path = PRICE_DIR + f"{symbol}.csv"
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, usecols=["Close"])
        close = pd.to_numeric(df["Close"], errors="coerce").dropna()
        return float(close.iloc[-1]) if len(close) else None
    except Exception:
        return None


def _yf_live(symbol):
    try:
        import logging
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        import yfinance as yf
        price = yf.Ticker(symbol).fast_info.last_price
        if price and price > 0:
            return float(price)
    except Exception:
        pass
    return None


def get_quote(symbol):
    """Returns (price, stale) — stale=False for a live-ish (delayed) quote,
    stale=True when falling back to the last downloaded CSV close.
    (None, True) if no price is available at all."""
    now = time.time()
    hit = _cache.get(symbol)
    if hit and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1], hit[2]

    price = _yf_live(symbol)
    stale = False
    if price is None:
        price = _csv_last_close(symbol)
        stale = True

    _cache[symbol] = (now, price, stale)
    return price, stale


def get_quotes(symbols):
    """Batch version: {symbol: (price, stale)}."""
    return {sym: get_quote(sym) for sym in symbols}


if __name__ == "__main__":
    import sys
    syms = sys.argv[1:] or ["RELIANCE.NS"]
    for sym in syms:
        if not sym.upper().endswith(".NS"):
            sym = sym.upper() + ".NS"
        price, stale = get_quote(sym)
        tag = "STALE (last CSV close)" if stale else "live (~15min delayed)"
        print(f"{sym:16s} {price if price is not None else 'N/A':>12}   [{tag}]")
