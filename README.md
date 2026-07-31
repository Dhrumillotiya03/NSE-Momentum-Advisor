# NSE-momentum-advisor

A local-first, self-contained quantitative trading advisor for NSE (India) equities — combining momentum-based stock selection, market regime detection, support/resistance analytics, and an AI assistant layer, with **zero recurring external API cost**.

Conceptually similar to what you'd get from Wright Research (momentum model portfolios), StockEdge (S/R + delivery volume analytics), and Univest (actionable trade calls) — but running entirely on your own machine against your own rules.

---

## What it does

- **Momentum stock selection** — ranks a liquidity-gated universe (~200 names, sized off trailing turnover as a point-in-time proxy for the NSE F&O roster) using a 126-day return / 63-day volatility score, rebalanced on a 21-trading-day cycle.
- **Regime detection** — classifies the market (BULL / SIDEWAYS / BEAR / UNKNOWN) from Nifty breadth and moving averages, and scales portfolio exposure and position count accordingly.
- **Support/Resistance engine** — swing-pivot based S/R levels (confluence-filtered, touch-count weighted, with 52-week extreme fallback), empirical touch-probability estimates per level, and NSE bhavcopy delivery-volume overlay. Levels are quoted against the **month-end rebalance date** (the last Tuesday), so the forecast horizon shrinks as the month progresses and the probabilities shrink with it. Probabilities come from a `P(touch)` lookup table keyed on (distance × realised volatility), built walk-forward with a time-based holdout at several horizons.
- **Forward measurement** — both S/R panels are logged nightly and scored against what price actually did, with windows that have not run their full length excluded rather than counted as misses. The measurement is deliberately separate from the model, so a pipeline fault and a miscalibrated model don't look alike.
- **Exit engine** — tiered exit logic: a hard catastrophic stop, an (optional, backtest-gated) early technical exit, and a month-end re-qualification gate that only holds positions that still rank in the top-N.
- **AI assistant** — a real tool-calling loop (via a local Ollama model) where the model chooses which analysis functions to call, all backed directly by the same canonical scoring/regime code the rest of the system uses — no separate logic path to drift out of sync.
- **Research suite** — a large set of standalone validation scripts (survivorship bias, slippage, transaction cost, VIX overlay, drawdown bootstrap, parameter robustness, statistical hygiene, etc.) used to stress-test the strategy before any parameter change goes live.
- **Call tracking** — every advisory call the system makes is logged and later scored against what price actually did, so the system's own hit rate is measurable over time, not just backtested.

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
├── full_advisor.py          # main advisory CLI — recommendations + S/R table
├── ai_assistant.py          # tool-calling AI assistant (local LLM via Ollama)
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
- No paid API keys required for the core strategy. Zerodha Kite Connect credentials are only needed if you wire up `zerodha_sync.py` for live portfolio sync.

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
```

## Data & privacy

Personal trading data — portfolio state, trade history, paper/sim books, and the advisory/S/R logs — is **not tracked**, and `.gitignore` excludes `data/`. Populate it yourself with the `download_*.py` scripts before running the advisor or backtests.

A limited set of **impersonal reference data** is tracked deliberately, because the code needs it to run and it reveals nothing about anyone's positions: the curated `sectors.json` sector map, index constituent lists, the S/R probability tables, and the corporate-announcements / delisted-price archives used by the research scripts.

> **Historical note.** Until 2026-07-31 this section claimed the repo shipped code only. That was wrong: 567 files under `data/` — including `portfolio_state.json` with live share quantities and entry prices — had been committed before the `.gitignore` rule existed, and `.gitignore` does not untrack already-committed files. They are untracked as of that date, but **they remain in the git history**, so treat anything committed before then as public. If you are forking or reusing this repo, run `git ls-files data/` and confirm you are comfortable with what is listed.

## Status

Actively developed. Recent work: consolidating multiple advisor entry points onto the canonical `core.py`/`strategy_config.py` pair, building out the tiered exit engine, and replacing prompt-stuffed AI querying with real function-calling.

## License

MIT — see [LICENSE](LICENSE).
