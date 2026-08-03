# NSE-momentum-advisor

A local-first, self-contained quantitative trading **advisory** for NSE (India) equities — combining momentum-based stock selection, market regime detection, support/resistance analytics, chart/pattern analysis, and an AI assistant layer. Runs on free tooling by default; live real-time quotes are an **optional paid add-on** (Zerodha Kite Connect, ~₹500/mo) — the strategy, backtesting, and advisory logic all work with zero external cost using the built-in 15-min-delayed feed.

Conceptually similar to what you'd get from Wright Research (momentum model portfolios), StockEdge (S/R + delivery volume + chart analytics), and Univest (actionable trade calls) — but running entirely on your own machine against your own rules. This is a **signal/advice engine** — which stocks to buy, at what price, when to exit a position (including stocks it doesn't track), and what to do over a stated horizon — not a portfolio manager; it does not place orders and does not require or track your brokerage cash balance.

---

## What it does

- **Momentum stock selection** — ranks a liquidity-gated universe (~200 names, sized off trailing turnover as a point-in-time proxy for the NSE F&O roster) using a 126-day return / 63-day volatility score, rebalanced on a 21-trading-day cycle.
- **Regime detection** — classifies the market (BULL / SIDEWAYS / BEAR / UNKNOWN) from Nifty breadth and moving averages, and scales portfolio exposure and position count accordingly.
- **Support/Resistance engine** — swing-pivot based S/R levels (confluence-filtered, touch-count weighted, with 52-week extreme fallback), empirical touch-probability estimates per level, and NSE bhavcopy delivery-volume overlay. Levels are quoted against the **month-end rebalance date** (the last Tuesday), so the forecast horizon shrinks as the month progresses and the probabilities shrink with it. Probabilities come from a `P(touch)` lookup table keyed on (distance × realised volatility), built walk-forward with a time-based holdout at several horizons.
- **Forward measurement** — both S/R panels are logged nightly and scored against what price actually did, with windows that have not run their full length excluded rather than counted as misses. The measurement is deliberately separate from the model, so a pipeline fault and a miscalibrated model don't look alike.
- **Exit engine** — tiered exit logic: a hard catastrophic stop, an (optional, backtest-gated) early technical exit, and a month-end (last-Tuesday) re-qualification gate that only holds positions that still rank in the top-N. Exit advice works for *any* symbol, not only positions the system already tracks.
- **Chart / pattern analysis** — candlestick pattern detection (hammer, engulfing, doji, morning/evening star, marubozu...), trend structure via swing points, moving-average posture, 52-week range positioning, anchored VWAP, volume and volatility behaviour, relative strength vs Nifty, and daily/weekly multi-timeframe agreement. Descriptive context for a human read — never wired into scoring or exit decisions.
- **Horizon advice** — a composite "what should I do with this stock over the next N days/weeks" answer: regime + momentum rank + support/resistance reach-probability (correctly scaled to the requested horizon, not a flat one-size number) + chart structure + estimated earnings timing, synthesized into one narrative.
- **AI assistant** — a real tool-calling loop (via a local Ollama model) where the model chooses which analysis functions to call, all backed directly by the same canonical scoring/regime code the rest of the system uses — no separate logic path to drift out of sync.
- **Research suite** — a large set of standalone validation scripts (survivorship bias, slippage, transaction cost, VIX overlay, drawdown bootstrap, parameter robustness, statistical hygiene, correlation-aware sizing, volatility-targeted exposure, regime hysteresis, staged entry, etc.) used to stress-test the strategy before any parameter change goes live — most candidate improvements are tested and rejected, and that's treated as a useful result, not a failure.
- **Call tracking** — every advisory call the system makes is logged and later scored against what price actually did, so the system's own hit rate is measurable over time, not just backtested.
- **Optional live data** — Zerodha Kite Connect integration for true real-time NSE quotes (vs. the default ~15-min-delayed free feed) and a terminal tick-by-tick price ticker. Entirely optional; every consumer falls back transparently to the free feed if it isn't configured.

## Architecture

The strategy's parameters (lookback windows, regime exposure table, position sizing, universe gate, transaction costs, the catastrophic stop) live in a single canonical module, imported by every consumer — the backtester, the live advisor, the paper trader, the simulator, and the AI assistant's tools. This was a deliberate consolidation to close a bug class where different entry points had silently drifted onto different scoring formulas.

```
scripts/
├── core.py                  # canonical data access, regime detection, scoring, S/R access
├── strategy_config.py       # single source of truth for all strategy parameters
├── support_resistance.py    # swing-pivot S/R detection engine
├── sr_horizon.py            # month-end (last-Tuesday) horizon arithmetic
├── sr_build_touchtable.py   # builds the empirical P(touch) probability tables
├── sr_monthend_analysis.py  # scores logged S/R levels against actual price action
├── exit_engine.py           # tiered exit logic (stop / early exit / month-end gate)
├── chart_analysis.py        # candlestick patterns, trend structure, AVWAP, relative strength
├── full_advisor.py          # main advisory CLI — recommendations + S/R table
├── ai_assistant.py          # tool-calling AI assistant (local LLM via Ollama)
├── live_quotes.py           # live price feed (Kite Connect if configured, else delayed free feed)
├── kite_auth.py             # Kite Connect daily token refresh (optional, for live_quotes.py)
├── live_ticker.py           # optional terminal tick-by-tick price display (needs Kite Connect)
├── backtest_portfolio.py    # portfolio-level backtest harness
├── walk_forward.py          # walk-forward validation
├── paper_trader.py          # paper trading loop
├── agent_sim.py             # simulation harness
├── call_report.py           # scores logged advisory calls against actual price action
├── download_*.py            # NSE bhavcopy, F&O, announcements, index, delisted data fetchers
├── research_*.py            # standalone strategy validation / robustness studies
└── ...
data/                        # local price data, logs, portfolio state (gitignored — not in this repo)
```

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) running locally, with a tool-calling-capable model pulled (e.g. `qwen2.5`, `llama3.1`, `mistral-nemo` — **not** plain `llama3`, which doesn't support tool calling) — only needed for `ai_assistant.py`
- No paid API keys required for the core strategy, backtesting, or advisory logic — the default live-price source (`live_quotes.py`) uses yfinance's free, ~15-minute-delayed feed.
- **Optional:** a Zerodha Kite Connect subscription (~₹500/mo) for true real-time NSE quotes and the tick-by-tick terminal ticker (`live_ticker.py`). Without it, everything falls back transparently to the free feed — nothing breaks or degrades in functionality, only in quote latency. See `kite_auth.py` for the setup flow if you want this.

## Setup

```bash
git clone https://github.com/Dhrumillotiya03/nse-momentum-advisor.git
cd nse-momentum-advisor
pip install -r requirements.txt
```

Copy the example config and adjust capital/risk settings:
```bash
cp config.yaml.example config.yaml   # if you've split out an example file
```

Pull a tool-calling model for the AI assistant (optional):
```bash
ollama pull qwen2.5
```

## Usage

```bash
cd scripts

# Get today's recommendations + S/R table
python full_advisor.py

# Ask the AI assistant a trading question
python ai_assistant.py

# Run the backtest
python backtest_portfolio.py

# Score past advisory calls against what actually happened
python call_report.py

# Candlestick / trend / relative-strength read for one stock
python chart_analysis.py TCS

# Optional: refresh the live Kite Connect quote token for the day
python kite_auth.py refresh

# Optional: live tick-by-tick price display (needs Kite Connect configured)
python live_ticker.py
```

## Data & privacy

Personal trading data — portfolio state, trade history, paper/sim books, and the advisory/S/R logs — is **not tracked**, and `.gitignore` excludes `data/`. Populate it yourself with the `download_*.py` scripts before running the advisor or backtests.

A limited set of **impersonal reference data** is tracked deliberately, because the code needs it to run and it reveals nothing about anyone's positions: the curated `sectors.json` sector map, index constituent lists, the S/R probability tables, and the corporate-announcements / delisted-price archives used by the research scripts.

If you configure the optional Kite Connect integration, credentials (API key/secret, TOTP secret, cached access token) are written to `data/secrets/` — covered by the same `.gitignore` rule, with restrictive file permissions set automatically. Never commit this directory; if you fork this repo, run `git check-ignore -v data/secrets/*` to confirm your setup excludes it before pushing anywhere.

> **Historical note.** Until 2026-07-31 this section claimed the repo shipped code only. That was wrong: 567 files under `data/` — including `portfolio_state.json` with live share quantities and entry prices — had been committed before the `.gitignore` rule existed, and `.gitignore` does not untrack already-committed files. They are untracked as of that date, but **they remain in the git history**, so treat anything committed before then as public. If you are forking or reusing this repo, run `git ls-files data/` and confirm you are comfortable with what is listed.

## Status

Actively developed. Recent work: fixed a backtest universe-coverage bug that had silently excluded every stock listed after ~2020 from validation; rebuilt the advisor to match the backtested strategy's own picks (it had drifted onto a structurally anti-momentum entry model); added chart/pattern analysis, relative strength, anchored VWAP, and a composite horizon-advice tool; corrected month-end to the last Tuesday across all live consumers; and wired an optional real-time Kite Connect quote feed alongside the existing free/delayed default. Several candidate strategy improvements (correlation-aware sizing, volatility targeting, regime-detection hysteresis, staged entry) were tested via walk-forward and deliberately not adopted — each looked favorable on a single backtest run but lost on out-of-sample evidence.

## License

MIT — see [LICENSE](LICENSE).
