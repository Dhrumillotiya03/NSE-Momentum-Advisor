"""
Live tick-by-tick terminal dashboard — Kite Connect websocket prices plus the
strategy's own analytics (momentum score/rank, regime, RSI, S/R levels)
layered on top, so a name isn't just a flashing number but carries the same
read the advisor would give it.

Requires a cached Kite Connect access token (kite_auth.py login/exchange —
see that script's docstring for the daily refresh flow). This is a DISPLAY
tool only: it shows live prices and derives a verdict from already-validated,
already-computed signals. It does not place orders, does not compute a new
signal, and is not read by any other part of the pipeline — closing it has
zero effect on the strategy, books, or scheduled jobs.

DESIGN NOTE — why the analytics are computed ONCE at launch, not per-tick:
momentum score (core.momentum_score), regime (core.market_regime), and RSI
are all defined on DAILY CLOSES — the exact convention the backtest was
validated against (core.momentum_score docstring: windows end YESTERDAY,
excluding the evaluation bar). They cannot change intraday; recomputing them
every redraw would burn CPU for numerically identical output until tonight's
close, and using live/last-price instead of a settled close would silently
test an unvalidated definition of the signal (this project already tried "be
more intraday-reactive" — see memory staged-entry-rejected-2026-08 — and it
lost in walk-forward). What genuinely IS live and updates every tick: price,
day change %, day high/low, and distance from price to the S/R levels below.

S/R LEVELS are a different case: the underlying pivots come from completed
daily/weekly/monthly bars (unchanged either way), but WHICH pivot counts as
support vs resistance is anchored to a live quote fetched once at launch
(core.sr_levels'/support_resistance.get_levels' cur= param — see CLAUDE.md's
2026-08-04 fix and the WIPRO case it documents: a resistance already cleared
by price was mislabeled "R1 (BROKEN)" instead of correctly as support when
anchored to a stale close). Fetched once, not per-tick, on purpose — see
StaticAnalytics' docstring for the cost tradeoff. Restart the ticker to
re-anchor levels to a fresher price during the session.

WATCHLIST ONLY — no connection to portfolio_state.json / the real book. This
displays names you're watching, not your holdings; it does not know or care
what you actually own. Defaults to the S/R panel (sr_daily_logger.WATCHLIST).

Run from scripts/:
  python live_ticker.py                    # watchlist = sr_daily_logger.WATCHLIST
  python live_ticker.py RELIANCE TCS DIXON  # explicit symbols

Ctrl-C to exit, or 'q'. Scroll with arrow keys / PgUp/PgDn / Home/End.
"""
import curses
import sys
import time

import numpy as np

import kite_auth
import core
import live_quotes
import strategy_config as sc


def resolve_watchlist(explicit_symbols):
    """Symbols to display, ALPHABETICAL regardless of source. WATCHLIST
    ONLY — no connection to portfolio_state.json / the real book (deliberate:
    this is a display tool for names you're watching, not a book viewer; see
    module docstring).

    Defaults to the S/R panel (sr_daily_logger.WATCHLIST) so the ticker shows
    the same names being logged and measured — one list to maintain instead of
    two that drift apart.

    Falls back to today's top momentum names only if the panel can't be
    imported, so the ticker still works standalone.
    """
    if explicit_symbols:
        symbols = [s.upper() + ".NS" if not s.upper().endswith(".NS") else s.upper()
                   for s in explicit_symbols]
    else:
        try:
            from sr_daily_logger import WATCHLIST
            symbols = [sym.upper() for sym in WATCHLIST]
        except Exception:
            # Panel unavailable — fall back to today's top momentum names.
            try:
                results = core.scan_universe()
                top = sorted(results.items(), key=lambda kv: -kv[1]["score"])[:10]
                symbols = [sym for sym, _ in top] or ["RELIANCE.NS"]
            except Exception:
                symbols = ["RELIANCE.NS"]

    return sorted(symbols)


# ---------- Static (once-per-launch) analytics ----------

class StaticAnalytics:
    """Everything derived from DAILY CLOSES — momentum score/rank, regime,
    RSI, S/R levels. Computed once at launch; see module docstring for why
    this is correct rather than a shortcut.

    S/R LEVELS ARE ANCHORED TO THE LIVE PRICE AT LAUNCH, not the last close
    (2026-08-04 fix — see core.sr_levels' cur= param). Without this, level
    SELECTION (which pivot counts as support vs resistance, the proximity
    window, 52w fallbacks) was silently keyed to yesterday's close even
    though the ticker is a live tool — the exact WIPRO bug documented in
    CLAUDE.md (a resistance already cleared by the live price still showing
    as "R1 (BROKEN -0.6%)" instead of correctly as S1). Fetched ONCE at
    launch (one batched Kite call) and then left alone for the session by
    design — recomputing get_levels() on every tick would cost ~0.3s per
    redraw across a 19-symbol watchlist (~50%+ of a CPU core, permanently,
    for a display tool) for a distinction (which pivot is which) that only
    matters at day-open granularity, not tick granularity. Restart the
    ticker to re-anchor to a fresher price.
    """

    def __init__(self, symbols):
        self.regime, self.breadth = self._safe(core.market_regime, ("UNKNOWN", float("nan")))
        self.index_last, self.index_chg = self._index_snapshot()

        scan = self._safe(core.scan_universe, {})
        ranked = sorted(scan.items(), key=lambda kv: -kv[1]["score"])
        self.rank = {sym: i + 1 for i, (sym, _) in enumerate(ranked)}
        self.universe_n = len(ranked)
        self.scores = scan  # sym -> dict(score, ret_6m, ret_3m, vol_63, rsi, price)

        launch_quotes = self._safe(lambda: live_quotes.get_quotes(symbols), {})

        self.levels = {}      # sym -> (support, resistance, s_str, r_str)
        self.prev_close = {}  # sym -> yesterday's close, for day-change fallback
        for sym in symbols:
            df = core.load_stock(sym)   # one load per watchlist symbol — cheap (~7ms),
            if df is None or len(df) < 60:  # scan_universe already scored this name if
                continue                    # eligible, but doesn't expose its raw df
            self.prev_close[sym] = float(df["Close"].iloc[-1])
            launch_price = launch_quotes.get(sym, (None, True))[0]
            try:
                self.levels[sym] = core.sr_levels(df, symbol=sym, fast=True, cur=launch_price)
            except Exception:
                pass
            if sym not in self.scores:   # only score again if scan_universe skipped it
                try:                     # (not in the gated universe, or ineligible)
                    r = core.compute_score(df)
                    if r is not None:
                        self.scores[sym] = r
                except Exception:
                    pass

    @staticmethod
    def _safe(fn, default):
        try:
            return fn()
        except Exception:
            return default

    @staticmethod
    def _index_snapshot():
        try:
            idx = core.load_index()
            last = float(idx.iloc[-1])
            prev = float(idx.iloc[-2])
            return last, (last / prev - 1) * 100
        except Exception:
            return None, None


def verdict_for(sym, price, analytics: StaticAnalytics):
    """Synthesizes a short actionable read from already-validated signals —
    composition only, computes nothing new. Mirrors the same thresholds the
    advisor uses (RSI_OVERBOUGHT). No book/position dependency — this is a
    watchlist tool, not a book viewer (see module docstring).
    Tags are ordered most-urgent-first since the row's color follows tags[0]."""
    score_row = analytics.scores.get(sym)
    rank = analytics.rank.get(sym)
    support, resistance, _, _ = analytics.levels.get(sym, (None, None, None, None))

    tags = []
    if price and resistance and price >= resistance * 0.98:
        tags.append(("NEAR RESISTANCE", "yellow"))
    if price and support and price <= support * 1.02:
        tags.append(("NEAR SUPPORT", "cyan"))
    if score_row is not None and rank is not None:
        if rank <= sc.REGIME_NAMES.get(analytics.regime, 10):
            tags.append((f"IN TOP-N ({analytics.regime})", "green"))
        if score_row.get("rsi") is not None and score_row["rsi"] >= sc.RSI_OVERBOUGHT:
            tags.append(("OVERBOUGHT", "yellow"))
    elif score_row is None:
        # Fails the momentum strategy's own entry gate right now (needs
        # positive 6m AND 3m momentum + price above the 50DMA — see
        # core.momentum_score). Not a display limitation: this name simply
        # wouldn't be bought by the strategy as of last close.
        tags.append(("not in strategy universe", "dim"))

    return tags


# ---------- Live (per-tick) state ----------

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
        self.day_high = {s: None for s in symbols}
        self.day_low = {s: None for s in symbols}
        self.volume = {s: None for s in symbols}
        self.last_tick_time = {s: None for s in symbols}
        self.connected = False
        self.last_error = None
        self.scroll = 0        # first visible row; the panel exceeds a screen


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
            if ohlc.get("high"):
                state.day_high[sym] = ohlc["high"]
            if ohlc.get("low"):
                state.day_low[sym] = ohlc["low"]
            if t.get("volume_traded"):
                state.volume[sym] = t["volume_traded"]
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


# ---------- Layout ----------
#
# ONE ROW per stock — SYMBOL/LAST/CHG/HIGH/LOW/GAIN/SUPPORT/RESISTANCE/VERDICT
# left to right, fixed x-positions shared by the header and every row so
# columns line up exactly. Each position leaves a real 2-char GAP after the
# previous field's widest realistic content (not just "content width" —
# that was a real bug: adjacent headers glued together with zero separating
# space when a position was set to exactly end-of-content). No RSI column —
# the raw number wasn't worth continuous attention; RSI_OVERBOUGHT still
# feeds the OVERBOUGHT verdict tag, just without its own column.
#
# Font size itself is a terminal-emulator setting, not something a curses
# script controls — Ctrl+Shift+"+" (most GNOME/GTK terminals) or the
# terminal's Preferences > Profile > Text size zooms it; this layout just
# makes sure nothing overlaps or wastes space at whatever size is chosen.

MIN_WIDTH = 140   # X_VERDICT (104) + a readable minimum for verdict text
CARD_HEIGHT = 1    # one row per stock


def color(pair_name):
    return {
        "red": curses.color_pair(1),
        "green": curses.color_pair(2),
        "yellow": curses.color_pair(3),
        "cyan": curses.color_pair(4),
        "dim": curses.color_pair(5),
        "white": 0,
    }[pair_name]


def safe_addstr(stdscr, row, col, text, attr=0):
    """curses raises if a write would cross the bottom-right corner of the
    screen — clip defensively so a resize mid-draw can't crash the loop."""
    h, w = stdscr.getmaxyx()
    if row < 0 or row >= h or col >= w:
        return
    stdscr.addstr(row, col, text[:max(0, w - col - 1)], attr)


def draw_banner(stdscr, w, analytics: StaticAnalytics, state: TickerState):
    status = "CONNECTED" if state.connected else "connecting..."
    status_attr = color("green") if state.connected else color("yellow")

    regime_attr = {"BULL": color("green"), "BEAR": color("red"),
                   "SIDEWAYS": color("yellow")}.get(analytics.regime, 0)

    idx_s = ""
    chg_attr = 0
    if analytics.index_last is not None:
        chg = analytics.index_chg
        chg_attr = color("green") if chg is not None and chg >= 0 else color("red")
        idx_s = f"NIFTY {analytics.index_last:,.1f} ({chg:+.2f}%)"

    breadth_s = f"Breadth {analytics.breadth*100:.0f}%" if analytics.breadth == analytics.breadth else ""

    safe_addstr(stdscr, 0, 0, "=" * w, color("dim"))
    safe_addstr(stdscr, 1, 2, "LIVE MARKET DASHBOARD", curses.A_BOLD)
    safe_addstr(stdscr, 1, 26, f"[{status}]", status_attr | curses.A_BOLD)
    safe_addstr(stdscr, 1, w - 14, time.strftime("%H:%M:%S IST"), 0)

    safe_addstr(stdscr, 2, 2, f"Regime: {analytics.regime}", regime_attr | curses.A_BOLD)
    col = 24
    if idx_s:
        safe_addstr(stdscr, 2, col, idx_s, chg_attr)
        col += len(idx_s) + 4
    if breadth_s:
        safe_addstr(stdscr, 2, col, breadth_s, 0)
        col += len(breadth_s) + 4
    safe_addstr(stdscr, 2, col, f"Universe n={analytics.universe_n}", color("dim"))

    if state.last_error and not state.connected:
        safe_addstr(stdscr, 3, 2, state.last_error, color("red"))

    safe_addstr(stdscr, 4, 2,
                "Support/Resistance anchored to price at launch (restart to refresh); price/high/low/gain/distance are live tick-by-tick.",
                color("dim"))
    safe_addstr(stdscr, 5, 2, "Ctrl-C to exit", color("dim"))
    safe_addstr(stdscr, 6, 0, "=" * w, color("dim"))


# Fixed x-positions for a SINGLE-LINE-per-stock table, sized to fit a
# standard 144-col terminal (the common default). Each position leaves a
# real 2-char GAP after the previous field's widest realistic content (not
# just "content width" — that was the bug that glued CHG/OPEN into HIGH's
# header with zero separating space): support/resistance ("1,234 (+12.3%)")
# need ~15 chars; 2 verdict tags together ("NEAR RESISTANCE | IN TOP-N
# (BEAR)") need ~35 (a rare 3-tag combo truncates with "..." — see draw_card).
X_SYMBOL   = 1
X_PRICE    = 15   # "12,345.60"
X_CHG      = 26   # "+12.34%"
X_HIGH     = 35
X_LOW      = 43
X_SUPPORT  = 51   # "1,234 (+12.3%)"
X_RESIST   = 68
X_VERDICT  = 85


def draw_header(stdscr, row, w):
    safe_addstr(stdscr, row, X_SYMBOL, "SYMBOL", curses.A_UNDERLINE | curses.A_BOLD)
    safe_addstr(stdscr, row, X_PRICE, "LAST", curses.A_UNDERLINE | curses.A_BOLD)
    safe_addstr(stdscr, row, X_CHG, "CHG/OPEN", curses.A_UNDERLINE | curses.A_BOLD)
    safe_addstr(stdscr, row, X_HIGH, "HIGH", curses.A_UNDERLINE | curses.A_BOLD)
    safe_addstr(stdscr, row, X_LOW, "LOW", curses.A_UNDERLINE | curses.A_BOLD)
    safe_addstr(stdscr, row, X_SUPPORT, "SUPPORT", curses.A_UNDERLINE | color("dim"))
    safe_addstr(stdscr, row, X_RESIST, "RESISTANCE", curses.A_UNDERLINE | color("dim"))
    safe_addstr(stdscr, row, X_VERDICT, "VERDICT", curses.A_UNDERLINE | color("dim"))


def fmt_num(x, decimals=2):
    return f"{x:,.{decimals}f}" if x is not None and x == x else "-"


def draw_card(stdscr, row, w, sym, state: TickerState, analytics: StaticAnalytics):
    """Renders one stock as a SINGLE row — every field at a fixed x-position
    matching the header, tight to its actual content (no wasted padding).
    Watchlist-only: no book/position dependency (see module docstring)."""
    price = state.prices.get(sym)
    open_px = state.day_open.get(sym)
    high_px = state.day_high.get(sym)
    low_px = state.day_low.get(sym)
    support, resistance, _, _ = analytics.levels.get(sym, (None, None, None, None))

    if price is None:
        price = analytics.prev_close.get(sym)  # show yesterday's close until first tick

    name = sym.replace(".NS", "")
    safe_addstr(stdscr, row, X_SYMBOL, f"{name:<11}", curses.A_BOLD)

    safe_addstr(stdscr, row, X_PRICE, fmt_num(price), curses.A_BOLD)

    if price and open_px:
        chg = (price / open_px - 1) * 100
        chg_s, chg_attr = f"{chg:+.2f}%", (color("green") if chg >= 0 else color("red"))
    else:
        chg_s, chg_attr = "-", 0
    safe_addstr(stdscr, row, X_CHG, chg_s, chg_attr | curses.A_BOLD)

    safe_addstr(stdscr, row, X_HIGH, f"{high_px:,.0f}" if high_px else "-", color("green"))
    safe_addstr(stdscr, row, X_LOW, f"{low_px:,.0f}" if low_px else "-", color("red"))

    if support and price:
        dist = (price / support - 1) * 100
        s_s = f"{support:,.0f} ({dist:+.1f}%)"
    else:
        s_s = "-"
    safe_addstr(stdscr, row, X_SUPPORT, s_s, color("cyan"))

    if resistance and price:
        dist = (resistance / price - 1) * 100
        r_s = f"{resistance:,.0f} ({dist:+.1f}%)"
    else:
        r_s = "-"
    safe_addstr(stdscr, row, X_RESIST, r_s, color("yellow"))

    tags = verdict_for(sym, price, analytics)
    if tags:
        verdict_s = " | ".join(t for t, _ in tags)
        verdict_attr = color(tags[0][1]) | curses.A_BOLD
    else:
        verdict_s = "-"
        verdict_attr = color("dim")
    avail = max(0, w - X_VERDICT - 1)
    if len(verdict_s) > avail:   # rare multi-tag combo — truncate cleanly, not mid-word
        verdict_s = verdict_s[:max(0, avail - 3)] + "..."
    safe_addstr(stdscr, row, X_VERDICT, verdict_s, verdict_attr)


def draw(stdscr, state: TickerState, analytics: StaticAnalytics):
    stdscr.erase()
    h, w = stdscr.getmaxyx()

    if w < MIN_WIDTH:
        safe_addstr(stdscr, 0, 0, f"Terminal too narrow ({w} cols) — widen to at least {MIN_WIDTH}.", color("red"))
        stdscr.refresh()
        return

    draw_banner(stdscr, w, analytics, state)
    draw_header(stdscr, 8, w)

    # The panel is larger than most terminals (60+ names), so the list scrolls
    # rather than silently truncating — with a 61-symbol watchlist the old
    # `break` at screen bottom hid roughly a third of it with no indication
    # anything was missing.
    first_row, last_row = 10, h - 2
    capacity = max(1, (last_row - first_row + 1) // CARD_HEIGHT)
    total = len(state.symbols)
    max_off = max(0, total - capacity)
    off = min(max(0, getattr(state, "scroll", 0)), max_off)
    state.scroll = off

    row = first_row
    for sym in state.symbols[off:off + capacity]:
        draw_card(stdscr, row, w, sym, state, analytics)
        row += CARD_HEIGHT

    if total > capacity:
        shown_hi = min(off + capacity, total)
        safe_addstr(stdscr, h - 1, 0,
                    f"  {off+1}-{shown_hi} of {total}   "
                    f"↑/↓ PgUp/PgDn Home/End to scroll, q to quit",
                    color("cyan"))
    stdscr.refresh()


def ensure_kite_client(stdscr):
    """Returns a validated KiteConnect client, refreshing the token
    interactively if it's missing or expired — so you don't have to
    separately remember to run `kite_auth.py refresh` before launching the
    ticker. kite_auth.cmd_refresh() needs a real terminal (it opens a
    browser, prints a TOTP, and calls input() to collect the pasted-back
    request_token) — none of that works with curses holding the screen, so
    this drops out of curses (endwin), runs the refresh in plain terminal
    mode, then re-enters curses. Returns None (caller should exit) only if
    the user declines to refresh or the refresh itself fails.

    This can ONLY ever be a per-launch, human-in-the-loop prompt — Zerodha
    provides no programmatic password login, so full automation was
    considered and rejected (see kite_auth.py's module docstring and memory
    kite-connect-live-feed-2026-08). This does not change that; it just
    surfaces the same manual step at the moment you'd actually want it,
    instead of requiring you to remember it beforehand."""
    kite = kite_auth.get_kite_client()
    if kite is not None:
        try:
            kite.profile()   # cheap authenticated call — confirms the cached token still works
            return kite
        except Exception:
            kite = None   # cached token exists but Kite rejected it (expired/invalid)

    # Everything from here down is plain-terminal I/O (print/input, and
    # cmd_refresh's own browser-open + TOTP print + paste-back prompt) — all
    # of it must happen and finish BEFORE curses resumes, or bare print()
    # calls after re-entering curses mode will corrupt the display.
    curses.endwin()
    result = None
    try:
        if kite_auth.get_access_token() is None:
            print("No cached Kite access token.\n")
        else:
            print("Cached Kite access token has expired or is invalid.\n")
        answer = input("Refresh it now? [Y/n] ").strip().lower()
        if answer not in ("", "y", "yes"):
            print("Skipping — live_ticker.py is Kite-only (no delayed-feed fallback). Exiting.")
            time.sleep(2)
        else:
            kite_auth.cmd_refresh()
            kite = kite_auth.get_kite_client()
            if kite is None:
                print("Refresh did not produce a usable token.")
                time.sleep(3)
            else:
                try:
                    kite.profile()
                    result = kite
                except Exception as e:
                    print(f"New token still failing validation: {e}")
                    time.sleep(3)
    except Exception as e:
        print(f"Refresh failed: {e}")
        time.sleep(3)
    finally:
        # curses.wrapper only sets up/tears down once around main() as a
        # whole — resuming mid-function after endwin() is on us. refresh()
        # is the standard idiom (same one pagers/editors use after shelling
        # out): it forces curses to redraw and re-take the terminal.
        stdscr.refresh()

    return result


def main(stdscr, symbols):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_RED, -1)
    curses.init_pair(2, curses.COLOR_GREEN, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_CYAN, -1)
    curses.init_pair(5, curses.COLOR_WHITE, -1)   # dim substitute (color_pair 5 used sparingly, A_DIM varies by terminal)

    kite = ensure_kite_client(stdscr)
    if kite is None:
        return

    stdscr.addstr(0, 0, "Loading momentum score / regime / S-R levels (one-time, ~5-10s)...")
    stdscr.refresh()
    analytics = StaticAnalytics(symbols)

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

    kws = make_ticker(kite, access_token, secrets["api_key"], state)
    kws.connect(threaded=True)

    # Non-blocking input so scroll keys are handled without stalling the
    # redraw. halfdelay() paces the loop the way the old sleep(0.5) did, while
    # still waking immediately on a keypress.
    curses.halfdelay(5)   # tenths of a second
    try:
        while True:
            draw(stdscr, state, analytics)
            try:
                ch = stdscr.getch()
            except Exception:
                ch = -1
            if ch in (ord("q"), ord("Q")):
                break
            elif ch == curses.KEY_DOWN:
                state.scroll += 1
            elif ch == curses.KEY_UP:
                state.scroll -= 1
            elif ch == curses.KEY_NPAGE:
                state.scroll += 10
            elif ch == curses.KEY_PPAGE:
                state.scroll -= 10
            elif ch == curses.KEY_HOME:
                state.scroll = 0
            elif ch == curses.KEY_END:
                state.scroll = len(state.symbols)   # draw() clamps to the max
            # draw() clamps scroll into range, so no bounds logic is needed here.
    except KeyboardInterrupt:
        pass
    finally:
        kws.close()


if __name__ == "__main__":
    syms = resolve_watchlist(sys.argv[1:])
    curses.wrapper(main, syms)
