"""
Live price quotes for the portfolio and exit checks.

Source order (2026-08-03, Kite Connect added — collaborator's paid ₹500/mo
account, see memory kite-connect-live-feed-2026-08):
  1. Kite Connect REST quote API — TRUE live/real-time NSE data, if
     kite_auth.py has a cached access token (see kite_auth.py for the daily
     login flow). Falls through silently if no token is cached, the token
     has expired (~24h), or the API call fails for any reason — this must
     NEVER be the only path, since the token needs manual daily refresh.
  2. yfinance fast_info — free, no API key, ~15-min delayed for NSE. The
     original path; kept as the automatic fallback whenever Kite is
     unavailable so nothing breaks on a day the token wasn't refreshed.
  3. Last close from ../data/price_data/<sym>.csv — flagged stale=True so
     callers can tell a live-ish quote from yesterday's close.

NSE's own quote API (and nsepython) was tried and is bot-walled from this
machine (403 / empty responses) — deliberately not used; don't re-add a
scraper that breaks every time NSE rotates its anti-bot checks.

Quotes are cached for CACHE_TTL_SECONDS so a scan over the whole book
doesn't hammer the API. Batch with get_quotes(symbols) where possible —
get_quotes batches ALL symbols into ONE Kite API call, not one per symbol.
"""
import os
import time

import pandas as pd

PRICE_DIR = "../data/price_data/"
CACHE_TTL_SECONDS = 60

_cache = {}   # symbol -> (timestamp, price, stale)
_kite_client = None      # lazy-initialized, None if unavailable
_kite_checked = False    # avoid retrying kite_auth import/init every call


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


def _to_kite_symbol(symbol):
    """RELIANCE.NS -> NSE:RELIANCE (Kite's exchange:tradingsymbol format)."""
    return f"NSE:{symbol[:-3]}" if symbol.upper().endswith(".NS") else f"NSE:{symbol}"


def _get_kite_client():
    """Lazy singleton — tries once per process, never retries kite_auth
    import/init on every quote call (that would be wasteful if the token
    genuinely isn't cached today, e.g. login hasn't been refreshed)."""
    global _kite_client, _kite_checked
    if _kite_checked:
        return _kite_client
    _kite_checked = True
    try:
        import kite_auth
        _kite_client = kite_auth.get_kite_client()
    except Exception:
        _kite_client = None
    return _kite_client


def _kite_live_batch(symbols):
    """Returns {symbol: price} for whichever symbols Kite successfully
    quoted — never raises; caller falls back to yfinance/CSV per-symbol
    for anything missing from the result."""
    kite = _get_kite_client()
    if kite is None:
        return {}
    kite_syms = [_to_kite_symbol(s) for s in symbols]
    try:
        data = kite.quote(kite_syms)
    except Exception:
        return {}
    out = {}
    for sym, ksym in zip(symbols, kite_syms):
        row = data.get(ksym)
        if row and row.get("last_price"):
            out[sym] = float(row["last_price"])
    return out


def get_quote(symbol):
    """Returns (price, stale) — stale=False for a live quote (Kite Connect
    real-time, or yfinance ~15-min delayed), stale=True when falling back
    to the last downloaded CSV close. (None, True) if nothing is available.
    For more than a couple of symbols, prefer get_quotes — it batches the
    Kite call instead of one request per symbol."""
    now = time.time()
    hit = _cache.get(symbol)
    if hit and now - hit[0] < CACHE_TTL_SECONDS:
        return hit[1], hit[2]

    kite_prices = _kite_live_batch([symbol])
    price = kite_prices.get(symbol)
    if price is None:
        price = _yf_live(symbol)
    stale = False
    if price is None:
        price = _csv_last_close(symbol)
        stale = True

    _cache[symbol] = (now, price, stale)
    return price, stale


def get_quotes(symbols):
    """Batch version: {symbol: (price, stale)}. Fetches ALL uncached symbols
    from Kite in ONE call, falling back to yfinance/CSV per-symbol only for
    whatever Kite didn't return."""
    now = time.time()
    out = {}
    need = []
    for sym in symbols:
        hit = _cache.get(sym)
        if hit and now - hit[0] < CACHE_TTL_SECONDS:
            out[sym] = (hit[1], hit[2])
        else:
            need.append(sym)
    if not need:
        return out

    kite_prices = _kite_live_batch(need)
    for sym in need:
        price = kite_prices.get(sym)
        stale = False
        if price is None:
            price = _yf_live(sym)
        if price is None:
            price = _csv_last_close(sym)
            stale = True
        _cache[sym] = (now, price, stale)
        out[sym] = (price, stale)
    return out


if __name__ == "__main__":
    import sys
    syms = [s.upper() + ".NS" if not s.upper().endswith(".NS") else s.upper()
            for s in (sys.argv[1:] or ["RELIANCE.NS"])]
    kite_ok = _get_kite_client() is not None
    print(f"[Kite Connect: {'connected' if kite_ok else 'no cached token — run kite_auth.py login/exchange'}]\n")
    kite_hits = _kite_live_batch(syms) if kite_ok else {}
    for sym in syms:
        price, stale = get_quote(sym)
        if stale:
            tag = "STALE (last CSV close)"
        elif sym in kite_hits:
            tag = "LIVE (Kite Connect, real-time)"
        else:
            tag = "live (yfinance, ~15min delayed)"
        print(f"{sym:16s} {price if price is not None else 'N/A':>12}   [{tag}]")
