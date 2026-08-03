"""
Live tick-by-tick price ticker — a terminal display like Kite's own
watchlist, where prices update in place as ticks arrive over the websocket.

Requires a cached Kite Connect access token (kite_auth.py login/exchange —
see that script's docstring for the daily refresh flow). This is a DISPLAY
tool only: it shows live prices, day change, and (for held positions)
gain/loss vs entry. It does not place orders, does not compute signals, and
is not read by any other part of the pipeline — closing it has zero effect
on the strategy, books, or scheduled jobs.

Run from scripts/:
  python live_ticker.py                    # watchlist = held positions + today's top buy candidates
  python live_ticker.py RELIANCE TCS DIXON  # explicit symbols

Ctrl-C to exit.
"""
import curses
import sys
import time

import kite_auth
import core


def resolve_watchlist(explicit_symbols):
    if explicit_symbols:
        return [s.upper() + ".NS" if not s.upper().endswith(".NS") else s.upper()
                for s in explicit_symbols]
    symbols = []
    try:
        state = core.load_portfolio_state()
        symbols.extend(state.get("positions", {}).keys())
    except Exception:
        pass
    try:
        results = core.scan_universe()
        top = sorted(results.items(), key=lambda kv: -kv[1]["score"])[:10]
        for sym, _ in top:
            if sym not in symbols:
                symbols.append(sym)
    except Exception:
        pass
    return symbols[:25] or ["RELIANCE.NS"]  # hard cap — a terminal screen only fits so many rows


def entry_prices():
    """symbol -> entry_price for held names, so the ticker can show gain/loss."""
    try:
        state = core.load_portfolio_state()
        return {s: p.get("entry_price", 0) for s, p in state.get("positions", {}).items()}
    except Exception:
        return {}


class TickerState:
    """Shared mutable state the websocket thread writes into and the curses
    redraw loop reads from — no locking needed since Python's GIL makes a
    single dict-key assignment atomic enough for a display-only tool (a torn
    read here is at worst one stale-looking price for one redraw frame)."""
    def __init__(self, symbols, token_to_symbol):
        self.symbols = symbols
        self.token_to_symbol = token_to_symbol
        self.prices = {s: None for s in symbols}       # last_price
        self.day_open = {s: None for s in symbols}      # for %change since open
        self.last_tick_time = {s: None for s in symbols}
        self.connected = False
        self.last_error = None


def make_ticker(kite, access_token, api_key, state: TickerState):
    from kiteconnect import KiteTicker
    kws = KiteTicker(api_key, access_token)

    def on_ticks(ws, ticks):
        for t in ticks:
            sym = state.token_to_symbol.get(t["instrument_token"])
            if not sym:
                continue
            state.prices[sym] = t.get("last_price")
            ohlc = t.get("ohlc") or {}
            if ohlc.get("open"):
                state.day_open[sym] = ohlc["open"]
            state.last_tick_time[sym] = time.strftime("%H:%M:%S")

    def on_connect(ws, response):
        state.connected = True
        tokens = list(state.token_to_symbol.keys())
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_FULL, tokens)

    def on_close(ws, code, reason):
        state.connected = False
        state.last_error = f"closed: {reason}"

    def on_error(ws, code, reason):
        state.last_error = f"error {code}: {reason}"

    kws.on_ticks = on_ticks
    kws.on_connect = on_connect
    kws.on_close = on_close
    kws.on_error = on_error
    return kws


def draw(stdscr, state: TickerState, entries):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    status = "CONNECTED" if state.connected else "connecting..."
    stdscr.addstr(0, 0, f"LIVE TICKER — Kite Connect ({status})   Ctrl-C to exit"[:w - 1],
                 curses.A_BOLD)
    if state.last_error and not state.connected:
        stdscr.addstr(1, 0, f"  {state.last_error}"[:w - 1], curses.color_pair(1))

    header = f"{'SYMBOL':<16}{'LAST':>12}{'CHG vs OPEN':>14}{'GAIN vs ENTRY':>16}{'TICK TIME':>12}"
    stdscr.addstr(2, 0, header[:w - 1], curses.A_UNDERLINE)

    row = 3
    for sym in state.symbols:
        if row >= h - 1:
            break
        price = state.prices.get(sym)
        open_px = state.day_open.get(sym)
        tick_t = state.last_tick_time.get(sym) or "-"
        entry = entries.get(sym)

        price_s = f"{price:,.2f}" if price else "..."
        if price and open_px:
            chg = (price / open_px - 1) * 100
            chg_s = f"{chg:+.2f}%"
            chg_attr = curses.color_pair(2) if chg >= 0 else curses.color_pair(1)
        else:
            chg_s, chg_attr = "-", 0
        if price and entry:
            gain = (price / entry - 1) * 100
            gain_s = f"{gain:+.2f}%"
            gain_attr = curses.color_pair(2) if gain >= 0 else curses.color_pair(1)
        else:
            gain_s, gain_attr = "-", 0

        stdscr.addstr(row, 0, f"{sym:<16}", 0)
        stdscr.addstr(row, 16, f"{price_s:>12}", 0)
        stdscr.addstr(row, 28, f"{chg_s:>14}", chg_attr)
        stdscr.addstr(row, 42, f"{gain_s:>16}", gain_attr)
        stdscr.addstr(row, 58, f"{tick_t:>12}", 0)
        row += 1
    stdscr.refresh()


def main(stdscr, symbols):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_RED, -1)
    curses.init_pair(2, curses.COLOR_GREEN, -1)

    kite = kite_auth.get_kite_client()
    if kite is None:
        stdscr.addstr(0, 0, "No cached Kite access token — run: python kite_auth.py login")
        stdscr.refresh()
        time.sleep(3)
        return

    # Resolve instrument tokens via the existing REST quote call (one batch
    # call, not a separate instruments-dump download) — matches how
    # live_quotes.py already does this.
    kite_syms = [f"NSE:{s[:-3]}" for s in symbols]
    quotes = kite.quote(kite_syms)
    token_to_symbol = {}
    for sym, ksym in zip(symbols, kite_syms):
        row = quotes.get(ksym)
        if row:
            token_to_symbol[row["instrument_token"]] = sym

    if not token_to_symbol:
        stdscr.addstr(0, 0, f"Could not resolve any of {symbols} via Kite — check symbols/token.")
        stdscr.refresh()
        time.sleep(3)
        return

    secrets = kite_auth.load_secrets()
    access_token = kite_auth.get_access_token()
    state = TickerState(symbols, token_to_symbol)
    entries = entry_prices()

    kws = make_ticker(kite, access_token, secrets["api_key"], state)
    kws.connect(threaded=True)

    try:
        while True:
            draw(stdscr, state, entries)
            time.sleep(0.5)   # redraw rate — ticks arrive faster, this just paces the screen
    except KeyboardInterrupt:
        pass
    finally:
        kws.close()


if __name__ == "__main__":
    syms = resolve_watchlist(sys.argv[1:])
    curses.wrapper(main, syms)
