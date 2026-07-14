"""
Chart pack for the agent-sim month — visual analysis of the model's calls.

Generates PNGs into data/_agent_sim/charts/ (regenerated nightly by
run_daily_log.sh; also run on demand):

  equity_vs_nifty.png   — sim equity curve vs Nifty, both normalized to 100
                          at the sim's first session
  <SYMBOL>.png          — one candlestick chart per symbol the sim has ever
                          traded: last ~120 sessions of OHLC, entry marker
                          (green ▲ at fill price), sell markers (red ▼),
                          the -18% catastrophic-stop line for momentum
                          names (sleeves have no stop), and title P&L

Pure matplotlib (mplfinance is not installed); candles drawn manually.
Read-only over the sim sandbox — never touches any books.

Run from scripts/:  python sim_charts.py
"""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

import strategy_config as sc

SIM_DIR = "../data/_agent_sim/"
CHART_DIR = SIM_DIR + "charts/"
LOOKBACK_SESSIONS = 120

SLEEVES = {sc.GOLD_SYMBOL, sc.INTL_SYMBOL}


def load_ohlc(sym):
    for base in ("../data/price_data/", "../data/etf_data/"):
        path = base + f"{sym}.csv"
        if not os.path.exists(path):
            continue
        df = pd.read_csv(path, low_memory=False)
        df = df[pd.to_datetime(df["Date"], errors="coerce").notna()]
        df["Date"] = pd.to_datetime(df["Date"])
        for c in ("Open", "High", "Low", "Close"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.dropna(subset=["Open", "High", "Low", "Close"]).sort_values("Date")
    return None


def draw_candles(ax, df):
    x = mdates.date2num(df["Date"])
    width = 0.65
    for xi, (_, r) in zip(x, df.iterrows()):
        up = r["Close"] >= r["Open"]
        color = "#26a269" if up else "#c01c28"
        ax.plot([xi, xi], [r["Low"], r["High"]], color=color, linewidth=0.8, zorder=2)
        lo, hi = sorted((r["Open"], r["Close"]))
        ax.bar(xi, max(hi - lo, 1e-9), bottom=lo, width=width, color=color,
               edgecolor=color, linewidth=0.4, zorder=3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.grid(True, alpha=0.25)


def chart_symbol(sym, fills):
    df = load_ohlc(sym)
    if df is None or len(df) < 10:
        return False
    df = df.tail(LOOKBACK_SESSIONS)
    fig, ax = plt.subplots(figsize=(11, 5.5))
    draw_candles(ax, df)

    buys = [f for f in fills if f["action"] == "BUY"]
    sells = [f for f in fills if f["action"] == "SELL"]
    for f in buys:
        d = pd.Timestamp(f["date"])
        if d >= df["Date"].iloc[0]:
            ax.scatter([mdates.date2num(d)], [f["price"]], marker="^", s=140,
                       color="#26a269", edgecolor="black", zorder=5,
                       label=f"BUY {f['qty']} @ ₹{f['price']:.2f}")
    for f in sells:
        d = pd.Timestamp(f["date"])
        if d >= df["Date"].iloc[0]:
            ax.scatter([mdates.date2num(d)], [f["price"]], marker="v", s=140,
                       color="#c01c28", edgecolor="black", zorder=5,
                       label=f"SELL {f['qty']} @ ₹{f['price']:.2f}")

    if buys and sym not in SLEEVES:
        # stop off the most recent entry price (mirrors exit_engine)
        stop = buys[-1]["price"] * sc.CATASTROPHIC_STOP
        ax.axhline(stop, color="#c01c28", linestyle="--", linewidth=1.2, alpha=0.8,
                   label=f"-18% stop ₹{stop:.2f}")

    last = df["Close"].iloc[-1]
    title = f"{sym} — last ₹{last:.2f}"
    if buys:
        entry = buys[-1]["price"]
        title += f" | entry ₹{entry:.2f} ({last / entry - 1:+.1%})"
    if sym in SLEEVES:
        title += "  [ETF sleeve — no stop]"
    ax.set_title(title)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(CHART_DIR + sym.replace(".NS", "") + ".png", dpi=110)
    plt.close(fig)
    return True


def chart_equity():
    eq_path = SIM_DIR + "equity.csv"
    if not os.path.exists(eq_path):
        return False
    eq = pd.read_csv(eq_path, parse_dates=["date"])
    if len(eq) < 2:
        return False
    idx = pd.read_csv("../data/index_data/nifty50.csv", low_memory=False)
    idx["Date"] = pd.to_datetime(idx["Date"], errors="coerce")
    idx["Close"] = pd.to_numeric(idx["Close"], errors="coerce")
    idx = idx.dropna(subset=["Date", "Close"]).sort_values("Date")
    idx = idx[idx["Date"] >= eq["date"].iloc[0]]

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(eq["date"], eq["equity"] / eq["equity"].iloc[0] * 100,
            label="agent-sim book", linewidth=2)
    if len(idx) > 1:
        ax.plot(idx["Date"], idx["Close"] / idx["Close"].iloc[0] * 100,
                label="Nifty 50", linewidth=1.5, alpha=0.8)
    ax.set_ylabel("normalized (start = 100)")
    ax.set_title("Agent-sim equity vs Nifty")
    ax.legend()
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(CHART_DIR + "equity_vs_nifty.png", dpi=110)
    plt.close(fig)
    return True


def main():
    journal_path = SIM_DIR + "trade_history.csv"
    if not os.path.exists(journal_path):
        print("[charts] no sim trades yet")
        return
    os.makedirs(CHART_DIR, exist_ok=True)
    jr = pd.read_csv(journal_path)

    made = 0
    for sym in sorted(jr["symbol"].unique()):
        fills = jr[jr["symbol"] == sym].to_dict("records")
        if chart_symbol(sym, fills):
            made += 1
    eq_done = chart_equity()
    print(f"[charts] {made} symbol chart(s)"
          + (" + equity curve" if eq_done else " (equity curve needs 2+ sessions)")
          + f" -> {CHART_DIR}")


if __name__ == "__main__":
    main()
