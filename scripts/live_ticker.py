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
import subprocess
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
    two that drift apart. INDICES (e.g. "NIFTY50") are excluded from that
    panel here: they're a different instrument type (own file path via
    support_resistance.INDEX_FILES, no .NS suffix, no Kite equity quote),
    so every column for one came back blank rather than erroring — core.
    load_stock() doesn't know about INDEX_FILES and silently returns None,
    and _to_kite_symbol("NIFTY50") produces "NSE:NIFTY50", not Kite's real
    "NSE:NIFTY 50" index symbol. The banner already shows the Nifty level
    (analytics.index_last/index_chg via core.load_index()), so this isn't
    lost information — it's just not duplicated as a broken row.

    Falls back to today's top momentum names only if the panel can't be
    imported, so the ticker still works standalone.
    """
    if explicit_symbols:
        symbols = [s.upper() + ".NS" if not s.upper().endswith(".NS") else s.upper()
                   for s in explicit_symbols]
    else:
        try:
            from sr_daily_logger import WATCHLIST
            from support_resistance import INDEX_FILES
            symbols = [sym.upper() for sym in WATCHLIST if sym.upper() not in INDEX_FILES]
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
        # Index into `symbols` of the row the user has picked out to follow
        # across the table (click, or arrow keys). None = nothing selected,
        # which is the launch state — the highlight is an aid you opt into,
        # not a cursor you have to keep track of. Display-only, like every
        # other field here: selecting a row reads nothing and writes nothing.
        self.selected = None
        # Viewport geometry, republished by draw() each frame and read by the
        # mouse handler to map a screen y back to a symbol index. Defaults
        # matter: draw() returns EARLY when the terminal is too narrow, so
        # without these a click on a narrow terminal would hit an unset
        # attribute. view_capacity=0 makes any click fall outside the range
        # test and be ignored, which is the correct behaviour when no rows
        # are on screen.
        self.view_first_row = 10
        self.view_off = 0
        self.view_capacity = 0
        # Symbol whose chart is on screen, or None for the normal table. The
        # chart is a MODE rather than a separate window/loop so ticks keep
        # arriving and the price marker stays live while you study it.
        self.chart_symbol = None
        # Chart zoom/pan. chart_bars = how many sessions are on screen;
        # chart_offset = how many bars back from the newest the window ends
        # (0 = right at the latest bar). Reset whenever a chart is opened, so
        # every chart starts from the same known view rather than inheriting
        # wherever the previous symbol was left.
        self.chart_bars = CHART_BARS
        self.chart_offset = 0


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
    safe_addstr(stdscr, 5, 2,
                "Click a row (or ↑/↓) to highlight it · c = chart with S/R · "
                "g = full chart window · Esc clears · q / Ctrl-C to exit",
                color("dim"))
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


def draw_card(stdscr, row, w, sym, state: TickerState, analytics: StaticAnalytics,
              selected=False):
    """Renders one stock as a SINGLE row — every field at a fixed x-position
    matching the header, tight to its actual content (no wasted padding).
    Watchlist-only: no book/position dependency (see module docstring).

    `selected` paints the row as a solid reverse-video bar (white background,
    dark text in a normal terminal) so one stock's values can be read straight
    across the table — the spreadsheet-style row highlight. The bar is drawn
    across the FULL width first so the gaps BETWEEN columns are highlighted
    too; a bar with holes in it defeats the purpose of tracing a row.

    On a selected row the per-field colours are deliberately dropped for one
    uniform attribute. Combining A_REVERSE with each field's own colour
    "works", but renders as a patchwork of green/red/cyan/yellow BACKGROUNDS
    across a single row, which is harder to follow than the unhighlighted
    version — the opposite of what the highlight is for. The colours are
    still there the moment the row is deselected, and price direction stays
    readable from the sign of CHG/OPEN."""
    price = state.prices.get(sym)
    open_px = state.day_open.get(sym)
    high_px = state.day_high.get(sym)
    low_px = state.day_low.get(sym)
    support, resistance, _, _ = analytics.levels.get(sym, (None, None, None, None))

    if price is None:
        price = analytics.prev_close.get(sym)  # show yesterday's close until first tick

    # One uniform attribute for every field when selected; otherwise each
    # field keeps its own colour. `hl()` centralises that choice so a field
    # added later can't silently miss the highlight.
    bar = curses.A_REVERSE | curses.A_BOLD

    def hl(attr):
        return bar if selected else attr

    if selected:
        # Full-width bar UNDER the fields, so inter-column gaps highlight too.
        safe_addstr(stdscr, row, 0, " " * max(0, w - 1), bar)

    name = sym.replace(".NS", "")
    safe_addstr(stdscr, row, X_SYMBOL, f"{name:<11}", hl(curses.A_BOLD))

    safe_addstr(stdscr, row, X_PRICE, fmt_num(price), hl(curses.A_BOLD))

    if price and open_px:
        chg = (price / open_px - 1) * 100
        chg_s, chg_attr = f"{chg:+.2f}%", (color("green") if chg >= 0 else color("red"))
    else:
        chg_s, chg_attr = "-", 0
    safe_addstr(stdscr, row, X_CHG, chg_s, hl(chg_attr | curses.A_BOLD))

    safe_addstr(stdscr, row, X_HIGH, f"{high_px:,.0f}" if high_px else "-",
                hl(color("green")))
    safe_addstr(stdscr, row, X_LOW, f"{low_px:,.0f}" if low_px else "-",
                hl(color("red")))

    if support and price:
        dist = (price / support - 1) * 100
        s_s = f"{support:,.0f} ({dist:+.1f}%)"
    else:
        s_s = "-"
    safe_addstr(stdscr, row, X_SUPPORT, s_s, hl(color("cyan")))

    if resistance and price:
        dist = (resistance / price - 1) * 100
        r_s = f"{resistance:,.0f} ({dist:+.1f}%)"
    else:
        r_s = "-"
    safe_addstr(stdscr, row, X_RESIST, r_s, hl(color("yellow")))

    tags = verdict_for(sym, price, analytics)
    if tags:
        verdict_s = " | ".join(t for t, _ in tags)
        verdict_attr = color(tags[0][1]) | curses.A_BOLD
    else:
        verdict_s = "-"
        verdict_attr = color("dim")
    verdict_attr = hl(verdict_attr)
    avail = max(0, w - X_VERDICT - 1)
    if len(verdict_s) > avail:   # rare multi-tag combo — truncate cleanly, not mid-word
        verdict_s = verdict_s[:max(0, avail - 3)] + "..."
    safe_addstr(stdscr, row, X_VERDICT, verdict_s, verdict_attr)


# ---------- Chart view ----------
#
# Why an IN-TERMINAL chart rather than only shelling out to a real charting
# app: the question this answers is "is the support/resistance this system
# printed actually plausible?", and that needs THIS system's levels drawn on
# the price action. A generic broker/TradingView chart shows neither — you'd
# be eyeballing a level from one screen against a chart on another. So the
# levels are overlaid directly, and `g` still pops out a full matplotlib
# candlestick window for anything the terminal's resolution can't settle.
#
# Daily bars, not intraday: the S/R tables are calibrated on daily data over a
# ~21-day horizon (see CLAUDE.md), so a daily chart is the timeframe the
# levels actually refer to. Charting intraday here would invite judging a
# daily-calibrated level against a timeframe it says nothing about.

CHART_BARS = 90     # ~4.5 months of sessions — enough to see where levels came from
# The popout window has real pixels rather than terminal cells, so it carries a
# longer history than the in-terminal chart. 250 NSE sessions ~= 1 calendar
# year (measured on real data: 250 bars spans exactly 365 days; the 180 this
# started at was only 8.5 months, which reads as "9 months" and is an odd
# window to reason about).
POPOUT_BARS = 250
# Zoom bounds for the terminal chart. The floor keeps enough bars for the
# swing structure to still be readable; the ceiling is a practical cap —
# beyond ~2 years one terminal column per session exceeds any real screen,
# and draw_chart already clips the window to the plot width anyway.
MIN_CHART_BARS = 15
MAX_CHART_BARS = 500


def chart_series(sym, bars=CHART_BARS, offset=0):
    """Daily OHLC window for the chart. Read-only; None if unusable.

    `offset` is how many bars back from the newest the window ENDS, so the
    chart can be panned into history once zoomed in — zooming without panning
    only ever tightens the view around the latest bar, which is the half of
    the feature that matters least when you are checking whether a level held
    the last time price was there.
    """
    df = core.load_stock(sym)
    if df is None or len(df) < 10:
        return None
    cols = {c.lower(): c for c in df.columns}
    need = ("open", "high", "low", "close")
    if not all(k in cols for k in need):
        return None
    end = len(df) - max(0, int(offset))
    end = max(min(end, len(df)), 1)
    sub = df.iloc[max(0, end - bars):end]
    if len(sub) < 2:
        return None
    return {
        "dates": list(sub.index),
        "open": [float(x) for x in sub[cols["open"]]],
        "high": [float(x) for x in sub[cols["high"]]],
        "low": [float(x) for x in sub[cols["low"]]],
        "close": [float(x) for x in sub[cols["close"]]],
    }


def window_extremes(series):
    """Highest high and lowest low WITHIN the charted window, with dates.

    Deliberately the window's own extremes rather than the all-time ones: a
    chart showing a year should quote the year's range, the same way "52-week
    high" means the last 52 weeks. Quoting an all-time high on a 4-month chart
    would name a price that isn't on screen.
    """
    hi_i = max(range(len(series["high"])), key=lambda i: series["high"][i])
    lo_i = min(range(len(series["low"])), key=lambda i: series["low"][i])
    return {
        "high": series["high"][hi_i], "high_date": series["dates"][hi_i],
        "low": series["low"][lo_i], "low_date": series["dates"][lo_i],
    }


def window_label(series):
    """Human name for the charted span — '1Y', '6M' — for labelling extremes.

    Derived from the actual dates rather than the bar count, because sessions
    per month vary with holidays and a name listed mid-window has fewer bars
    than the request asked for.
    """
    days = (series["dates"][-1] - series["dates"][0]).days
    months = max(1, round(days / 30.44))
    return f"{months // 12}Y" if months >= 12 else f"{months}M"


def draw_chart(stdscr, state: TickerState, analytics: StaticAnalytics):
    """Full-screen candlestick chart for state.chart_symbol, with this
    system's own support/resistance drawn across it and the live price
    marked. One terminal column per session; each candle uses '│' for the
    high-low wick and '█' for the open-close body."""
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    sym = state.chart_symbol
    name = sym.replace(".NS", "")

    series = chart_series(sym, getattr(state, "chart_bars", CHART_BARS),
                          getattr(state, "chart_offset", 0))
    if series is None and getattr(state, "chart_offset", 0):
        # Panned past the start of this symbol's history — snap back to the
        # newest window rather than showing an empty chart the user then has
        # to guess their way out of.
        state.chart_offset = 0
        series = chart_series(sym, getattr(state, "chart_bars", CHART_BARS))
    if series is None:
        safe_addstr(stdscr, 0, 2, f"No usable price history for {name}.", color("red"))
        safe_addstr(stdscr, 2, 2, "Esc / c to go back", color("dim"))
        stdscr.refresh()
        return

    live = state.prices.get(sym) or analytics.prev_close.get(sym)
    support, resistance, s_str, r_str = analytics.levels.get(
        sym, (None, None, None, None))

    # ---- header ----
    safe_addstr(stdscr, 0, 2, f"{name}", curses.A_BOLD)
    hdr = f"last {fmt_num(live)}" if live else ""
    rank = analytics.rank.get(sym)
    sc_row = analytics.scores.get(sym) or {}
    if rank:
        hdr += f"   rank {rank}/{analytics.universe_n}"
    if sc_row.get("score") is not None:
        hdr += f"   score {sc_row['score']:.1f}"
    safe_addstr(stdscr, 0, 2 + len(name) + 3, hdr, color("dim"))
    safe_addstr(stdscr, 0, w - 34, f"{len(series['close'])} daily bars",
                color("dim"))

    # ---- plot geometry ----
    top, bottom = 2, h - 4
    plot_h = bottom - top + 1
    left = 11                       # room for the price axis labels
    plot_w = max(1, w - left - 2)
    if plot_h < 6 or plot_w < 20:
        safe_addstr(stdscr, 2, 2, "Terminal too small for the chart.", color("red"))
        safe_addstr(stdscr, 4, 2, "Esc / c to go back", color("dim"))
        stdscr.refresh()
        return

    # One terminal column per session, so the plot width is a hard ceiling on
    # how far out the chart can usefully zoom. Publish it so the zoom-out key
    # can clamp to it: without this, chart_bars keeps climbing past what is
    # drawable and the next few '-' presses (and the first few '+' presses
    # back) change nothing on screen, which reads as a broken key.
    state.chart_max_bars = plot_w
    n = min(len(series["close"]), plot_w)
    o = series["open"][-n:]
    hi = series["high"][-n:]
    lo = series["low"][-n:]
    cl = series["close"][-n:]
    dates = series["dates"][-n:]

    # Scale to include the LEVELS too, not just price: a level sitting off the
    # top of the chart is exactly the case you opened the chart to judge.
    # Build ONE list rather than min(min(lo), *extras): with no levels and no
    # live price the unpacked generator is empty, leaving min(scalar), which
    # raises TypeError. A name can legitimately have neither (levels failed to
    # compute at launch and no tick has arrived yet).
    span = list(lo) + list(hi) + [v for v in (support, resistance, live) if v]
    lo_v, hi_v = min(span), max(span)
    if hi_v <= lo_v:
        hi_v = lo_v + 1.0
    pad = (hi_v - lo_v) * 0.04
    lo_v, hi_v = lo_v - pad, hi_v + pad

    def row_for(price):
        frac = (price - lo_v) / (hi_v - lo_v)
        return int(round(bottom - frac * (bottom - top)))

    # ---- price axis ----
    for i in range(5):
        p = lo_v + (hi_v - lo_v) * i / 4
        safe_addstr(stdscr, row_for(p), 0, f"{p:>9,.1f}", color("dim"))

    # ---- horizontal level lines (drawn BEFORE candles so candles win) ----
    for lvl, pair, label in ((support, "cyan", "S"), (resistance, "yellow", "R")):
        if not lvl:
            continue
        r = row_for(lvl)
        if top <= r <= bottom:
            safe_addstr(stdscr, r, left, "┈" * plot_w, color(pair))
            dist = (lvl / live - 1) * 100 if live else None
            tag = f" {label} {lvl:,.0f}" + (f" ({dist:+.1f}%)" if dist is not None else "")
            safe_addstr(stdscr, r, left + 1, tag, color(pair) | curses.A_BOLD)

    # ---- candles ----
    for i in range(n):
        x = left + i
        if x >= w - 1:
            break
        r_hi, r_lo = row_for(hi[i]), row_for(lo[i])
        r_o, r_c = row_for(o[i]), row_for(cl[i])
        up = cl[i] >= o[i]
        attr = color("green") if up else color("red")
        for r in range(min(r_hi, r_lo), max(r_hi, r_lo) + 1):   # wick
            safe_addstr(stdscr, r, x, "│", attr)
        for r in range(min(r_o, r_c), max(r_o, r_c) + 1):       # body
            safe_addstr(stdscr, r, x, "█", attr)

    # ---- live price marker ----
    if live:
        r = row_for(live)
        if top <= r <= bottom:
            safe_addstr(stdscr, r, left + plot_w, "◄", curses.A_BOLD)

    # ---- footer: dates + the descriptive read ----
    if dates:
        safe_addstr(stdscr, bottom + 1, left, str(dates[0].date()), color("dim"))
        safe_addstr(stdscr, bottom + 1, left + plot_w - 10,
                    str(dates[-1].date()), color("dim"))

    # Window high/low — the charted span's own range, marked on the plot and
    # spelled out with dates so "how far is this from its high" is answerable
    # without squinting at the axis.
    ext = window_extremes({"high": hi, "low": lo, "dates": dates})
    wl = window_label({"dates": dates})
    for key, dkey, mark, pair in (("high", "high_date", "▲", "green"),
                                  ("low", "low_date", "▼", "red")):
        r = row_for(ext[key])
        x = left + dates.index(ext[dkey]) if ext[dkey] in dates else None
        if top <= r <= bottom and x is not None and x < w - 1:
            safe_addstr(stdscr, r, x, mark, color(pair) | curses.A_BOLD)

    # Say the DIRECTION rather than a signed "away": "+102% away" from the low
    # reads as though price were somehow further from it than it is.
    vs_high = (live / ext["high"] - 1) * 100 if live else None
    vs_low = (live / ext["low"] - 1) * 100 if live else None
    ext_s = (f"{wl} high {ext['high']:,.0f} ({ext['high_date'].date()})"
             + (f" · now {abs(vs_high):.1f}% below" if vs_high is not None else "")
             + f"    {wl} low {ext['low']:,.0f} ({ext['low_date'].date()})"
             + (f" · now {abs(vs_low):.1f}% above" if vs_low is not None else ""))
    # Row 1: the plot starts at row 2 and the date footer already owns
    # bottom+1 (= h-3), so this sits directly under the title instead.
    safe_addstr(stdscr, 1, 2, ext_s, curses.A_BOLD)

    strength = []
    if support:
        strength.append(f"S {support:,.0f} (strength {s_str})")
    if resistance:
        strength.append(f"R {resistance:,.0f} (strength {r_str})")
    strength.append("levels are from launch (restart to refresh)")
    safe_addstr(stdscr, h - 2, 2, "   ".join(strength), color("dim"))
    panned = f" · panned back {state.chart_offset}" if getattr(state, "chart_offset", 0) else ""
    safe_addstr(stdscr, h - 1, 2,
                f"+/- zoom ({len(cl)} bars{panned}) · ←/→ pan · 0 reset · "
                "g full window · Esc/c back · q quit",
                color("cyan"))
    stdscr.refresh()


def render_popout(sym, save_to=None):
    """The matplotlib candlestick chart itself — run in a CHILD process by
    popout_chart(), or directly for testing (`save_to` writes a PNG instead
    of opening a window, which is the only way to exercise this headlessly).

    Beyond price + levels this prints the level's REACH PROBABILITY scaled to
    the real horizon, because "will this level actually be reached?" is the
    question the chart is being opened to answer, and eyeballing distance
    alone systematically overrates far levels — that is precisely what the
    empirical (distance x volatility) table exists to correct.
    """
    import matplotlib
    if save_to:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    import support_resistance as sr
    import sr_horizon

    s = chart_series(sym, POPOUT_BARS)
    df = core.load_stock(sym)
    if s is None or df is None:
        return
    support, resistance, s_str, r_str = core.sr_levels(df, symbol=sym, fast=True)

    # Horizon = to month-end (the last Tuesday), the same window the S/R
    # subsystem forecasts over — NOT a flat 21 days. See sr_horizon.
    try:
        end = sr_horizon.horizon_end(df.index[-1])
        # project_calendar_forward is REQUIRED here, not optional polish:
        # nifty50.csv ends today, so counting sessions to a FUTURE month-end
        # against the raw calendar returns 0 — and reach_probability_v2 with
        # forward_days=0 returns None, silently dropping the probability off
        # the chart. sr_daily_logger already does exactly this projection.
        cal = sr_horizon.project_calendar_forward(
            sr_horizon.load_trading_calendar(), end)
        fwd = sr_horizon.trading_days_until(df.index[-1], end, cal)
    except Exception:
        end, fwd = None, None

    def prob_for(level, direction):
        if not level:
            return None
        try:
            p, _n = sr.reach_probability_v2(df, level, direction,
                                            forward_days=fwd)
            return p
        except Exception:
            return None

    fig, ax = plt.subplots(figsize=(13, 7))
    dates = s["dates"]
    for i in range(len(s["close"])):
        up = s["close"][i] >= s["open"][i]
        c = "green" if up else "red"
        ax.plot([dates[i], dates[i]], [s["low"][i], s["high"][i]], color=c, linewidth=0.8)
        ax.plot([dates[i], dates[i]], [s["open"][i], s["close"][i]], color=c, linewidth=3.5)

    for lvl, colr, label, direction in (
            (support, "c", "Support", "down"),
            (resistance, "orange", "Resistance", "up")):
        if not lvl:
            continue
        p = prob_for(lvl, direction)
        txt = f"{label} {lvl:,.0f}"
        if p is not None:
            txt += f"  —  P(touch) {p}%"
        ax.axhline(lvl, color=colr, ls="--", label=txt)

    # Window high/low, marked where they actually occurred. Drawn as thin
    # dotted greys rather than more dashed colour so they read as context
    # behind the S/R levels, which are the actionable lines on this chart.
    ext = window_extremes(s)
    wl = window_label(s)
    for key, dkey, colr, marker, label in (
            ("high", "high_date", "dimgrey", "v", f"{wl} high"),
            ("low", "low_date", "dimgrey", "^", f"{wl} low")):
        ax.axhline(ext[key], color=colr, ls=":", linewidth=1,
                   label=f"{label} {ext[key]:,.0f}  ({ext[dkey].date()})")
        ax.plot([ext[dkey]], [ext[key]], marker=marker, color=colr,
                markersize=7, zorder=5)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    fig.autofmt_xdate()
    horizon_s = (f"   horizon: {fwd} sessions to {end.date()}"
                 if end is not None and fwd else "")
    ax.set_title(f"{sym.replace('.NS','')} — daily, with this system's S/R{horizon_s}")
    ax.grid(alpha=0.3)
    ax.legend()
    plt.tight_layout()
    if save_to:
        plt.savefig(save_to)
    else:
        plt.show()


def popout_chart(sym):
    """Open the richer matplotlib candlestick window in a SEPARATE process.

    Separate process, not an inline plt.show(): the ticker's websocket and
    redraw loop must keep running while you study the chart, and plt.show()
    blocks. It also keeps matplotlib's GUI backend out of the curses process,
    where the two would fight over the terminal.
    """
    try:
        subprocess.Popen(
            [sys.executable, "-c",
             f"import sys; sys.path.insert(0, '.');"
             f"import live_ticker; live_ticker.render_popout({sym!r})"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def draw(stdscr, state: TickerState, analytics: StaticAnalytics):
    if getattr(state, "chart_symbol", None):
        draw_chart(stdscr, state, analytics)
        return
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

    # Keep the selected row on screen. Arrow keys move the SELECTION (Excel-
    # style) rather than the viewport, so the viewport has to follow it, or
    # arrowing past the last visible row would silently move an off-screen
    # cursor. Done before clamping `scroll` so both end up consistent.
    sel = getattr(state, "selected", None)
    if sel is not None:
        sel = max(0, min(sel, total - 1))
        state.selected = sel
        if sel < state.scroll:
            state.scroll = sel
        elif sel >= state.scroll + capacity:
            state.scroll = sel - capacity + 1

    off = min(max(0, getattr(state, "scroll", 0)), max_off)
    state.scroll = off
    # Remember the geometry so a mouse click can map a screen y back to a
    # symbol index without re-deriving (and possibly disagreeing with) it.
    state.view_first_row, state.view_off, state.view_capacity = first_row, off, capacity

    row = first_row
    for i, sym in enumerate(state.symbols[off:off + capacity]):
        draw_card(stdscr, row, w, sym, state, analytics,
                  selected=(state.selected == off + i))
        row += CARD_HEIGHT

    if total > capacity:
        shown_hi = min(off + capacity, total)
        sel_s = (f"   selected: {state.symbols[state.selected].replace('.NS','')}"
                 if state.selected is not None else "")
        safe_addstr(stdscr, h - 1, 0,
                    f"  {off+1}-{shown_hi} of {total}   "
                    f"↑/↓ PgUp/PgDn Home/End · click to highlight · Esc clears"
                    f"{sel_s}",
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

    # Mouse: BUTTON1_CLICKED only, deliberately NOT ALL_MOUSE_EVENTS —
    # grabbing every event (drag/move) stops the terminal's own text
    # selection working, so you could no longer copy a price out of the
    # screen. Wrapped because a terminal without mouse support raises here,
    # and the keyboard path must keep working on one.
    try:
        curses.mousemask(curses.BUTTON1_CLICKED)
        curses.mouseinterval(0)   # report clicks immediately, don't wait to detect a double-click
    except Exception:
        pass

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

            # Chart mode swallows navigation keys: while a chart is up, ↑/↓
            # must not silently move a selection you cannot see.
            if state.chart_symbol:
                if ch in (27, ord("c"), ord("C")):
                    state.chart_symbol = None
                elif ch in (ord("g"), ord("G")):
                    popout_chart(state.chart_symbol)
                # Zoom is MULTIPLICATIVE: a fixed +/-10 bars is imperceptible
                # at 250 bars and a huge jump at 20. 1.4x gives a roughly even
                # feel across the range.
                elif ch in (ord("+"), ord("=")):          # zoom in
                    state.chart_bars = max(MIN_CHART_BARS,
                                           int(state.chart_bars / 1.4))
                elif ch in (ord("-"), ord("_")):          # zoom out
                    ceiling = min(MAX_CHART_BARS,
                                  getattr(state, "chart_max_bars", MAX_CHART_BARS))
                    state.chart_bars = min(ceiling,
                                           max(state.chart_bars + 1,
                                               int(state.chart_bars * 1.4)))
                elif ch == curses.KEY_LEFT:               # pan back in time
                    state.chart_offset += max(1, state.chart_bars // 4)
                elif ch == curses.KEY_RIGHT:              # pan toward today
                    state.chart_offset = max(
                        0, state.chart_offset - max(1, state.chart_bars // 4))
                elif ch == ord("0"):                      # reset the view
                    state.chart_bars, state.chart_offset = CHART_BARS, 0
                continue

            if ch in (ord("c"), ord("C")):
                # Chart the selected row; with nothing selected, chart the
                # first visible one so the key always does something.
                idx = state.selected if state.selected is not None else state.view_off
                if 0 <= idx < len(state.symbols):
                    state.chart_symbol = state.symbols[idx]
                    state.selected = idx
                    # Fresh chart, default view — otherwise the next symbol
                    # opens wherever the previous one was zoomed and panned to,
                    # which reads as a rendering bug rather than a carried-over
                    # setting.
                    state.chart_bars, state.chart_offset = CHART_BARS, 0
            elif ch in (ord("g"), ord("G")):
                idx = state.selected if state.selected is not None else state.view_off
                if 0 <= idx < len(state.symbols):
                    popout_chart(state.symbols[idx])
            elif ch == curses.KEY_MOUSE:
                # Click a row to highlight it. getmouse() raises if the queue
                # emptied between the KEY_MOUSE signal and the read, which
                # happens on some terminals — never let that kill the loop.
                try:
                    _id, mx, my, _z, _bstate = curses.getmouse()
                except Exception:
                    my = None
                if my is not None:
                    idx = (state.view_off
                           + (my - state.view_first_row) // CARD_HEIGHT)
                    if (my >= state.view_first_row
                            and idx < state.view_off + state.view_capacity
                            and 0 <= idx < len(state.symbols)):
                        # Clicking the highlighted row again clears it, so the
                        # mouse alone can both set and unset the highlight.
                        state.selected = None if state.selected == idx else idx
            elif ch == 27:            # Esc — clear the highlight
                state.selected = None
            elif ch == curses.KEY_DOWN:
                # Arrows move the SELECTION once something is selected
                # (spreadsheet behaviour, and draw() scrolls to follow); with
                # nothing selected they scroll the list as they always did, so
                # the pre-existing muscle memory still works untouched.
                if state.selected is None:
                    state.scroll += 1
                else:
                    state.selected += 1
            elif ch == curses.KEY_UP:
                if state.selected is None:
                    state.scroll -= 1
                else:
                    state.selected -= 1
            elif ch == curses.KEY_NPAGE:
                state.scroll += 10
                if state.selected is not None:
                    state.selected += 10
            elif ch == curses.KEY_PPAGE:
                state.scroll -= 10
                if state.selected is not None:
                    state.selected -= 10
            elif ch == curses.KEY_HOME:
                state.scroll = 0
                if state.selected is not None:
                    state.selected = 0
            elif ch == curses.KEY_END:
                state.scroll = len(state.symbols)   # draw() clamps to the max
                if state.selected is not None:
                    state.selected = len(state.symbols) - 1
            # draw() clamps scroll and selection into range, so no bounds
            # logic is needed here.
    except KeyboardInterrupt:
        pass
    finally:
        kws.close()


if __name__ == "__main__":
    syms = resolve_watchlist(sys.argv[1:])
    curses.wrapper(main, syms)
