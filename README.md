# NSE-momentum-advisor

A local-first, self-contained quantitative trading **advisory** for NSE (India) equities — combining momentum-based stock selection, market regime detection, support/resistance analytics, chart/pattern analysis, and an AI assistant layer. The strategy, backtesting, and advisory logic all run against a local price archive at zero external cost. Keeping that archive current now uses Zerodha Kite Connect (~₹500/mo), after yfinance proved unreliable for daily updates — see [Data quality](#data-quality); real-time quotes and the tick dashboard are further optional uses of the same connection.

Conceptually similar to what you'd get from Wright Research (momentum model portfolios), StockEdge (S/R + delivery volume + chart analytics), and Univest (actionable trade calls) — but running entirely on your own machine against your own rules. This is a **signal/advice engine** — which stocks to buy, at what price, when to exit a position (including stocks it doesn't track), and what to do over a stated horizon — not a portfolio manager; it does not place orders and does not require or track your brokerage cash balance.

---

## What it does

- **Momentum stock selection** — ranks a liquidity-gated universe (~200 names, sized off trailing turnover as a point-in-time proxy for the NSE F&O roster) using a 126-day return / 63-day volatility score, rebalanced on a 21-trading-day cycle. Position sizing blends inverse-volatility with the score's own magnitude (`conviction_weights`, capped at a single-name maximum) rather than being blind to how strong a signal is — walk-forward tested across 36 overlapping 3-year windows, +2.89% mean CAGR with the 95% confidence interval fully excluding zero, and worst-case drawdown *improved*, not worsened.
- **Regime detection** — classifies the market (BULL / SIDEWAYS / BEAR / UNKNOWN) from Nifty breadth and moving averages, and scales portfolio exposure and position count accordingly.
- **Support/Resistance engine** — swing-pivot based S/R levels (confluence-filtered, touch-count weighted, with 52-week extreme fallback), empirical touch-probability estimates per level, and NSE bhavcopy delivery-volume overlay. Levels are quoted against the **month-end rebalance date** (the last Tuesday), so the forecast horizon shrinks as the month progresses and the probabilities shrink with it. Probabilities come from a `P(touch)` lookup table keyed on (distance × realised volatility), built walk-forward with a time-based holdout at several horizons. Candidate levels must fall inside a **volatility-scaled reachable band** — at least a minimum separation from spot and from each other, and no further than a maximum reach. Both bounds are sized off the *horizon's* expected movement (~21 days) rather than a single session, because the question being asked is where price might go by month-end. Without the lower bound, ordering by proximity alone made S1 whatever pivot sat closest, sometimes 0.1% away: technically the nearest support, and a level whose ~94% touch probability is trivially true and useless. Without the upper bound, a stock with no nearby structure fell through to a pivot 40-50% away, which price will not test this month. The upper bound is *measured*, not chosen: locating where `P(touch)` falls below ~20% puts it at roughly 1.2× the 21-day sigma for calm names, tightening to 0.67× for volatile ones, since sigma grows faster than reach does. When no historical pivot survives the band — a stock at 52-week highs genuinely has none below it — a level is **projected** from the containment band rather than omitted, and flagged with strength 0 so a structural level is never silently confused with a statistical one. "No pivot nearby" is not "no support exists", and refusing to answer is not rigour. When a live quote is available, it drives *level selection* as well as distance — price moving through a level intraday genuinely changes which one is the nearest support and which has flipped to resistance, so ranking that off a stale close would answer a slightly different question. The underlying pivots are unaffected: they come from completed bars and do not move because the clock advanced.
- **Monthly containment band** — a separate answer to a question `P(touch)` structurally cannot address: *"what level will this stock not fall below this month?"* Touch probability asks whether price will **reach** a level; a holder wants to know whether it will **hold**, and those are complements — a level with 94% P(touch) has roughly a 6% chance of containing price. The band is an empirical quantile of forward closing excursion per (volatility bucket, horizon), fitted on 11 years of daily history with a time-based holdout, with **floor and ceiling fitted independently** because forward excursion is genuinely asymmetric and the sign of that asymmetry flips with the fitting window. It ships as a **risk/expectation tool, not an entry signal**: trading it was tested under a frozen protocol and rejected — see [Status](#status).
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
├── containment_band.py      # monthly containment band — the "won't fall below" level
├── build_containment_table_daily.py  # fits the containment quantiles (11y daily archive)
├── fetch_intraday_kite.py   # bulk 15-min bar download → data/intraday_data/ (research only)
├── sr_daily_logger.py       # nightly S/R snapshot — fixed validation panel
├── sr_dynamic_logger.py     # nightly S/R snapshot — holdings + top momentum names
├── sr_monthend_analysis.py  # scores logged S/R levels against actual price action
├── exit_engine.py           # tiered exit logic (stop / early exit / month-end gate)
├── chart_analysis.py        # candlestick patterns, trend structure, AVWAP, relative strength
├── full_advisor.py          # main advisory CLI — recommendations + S/R table
├── ai_assistant.py          # tool-calling AI assistant (local LLM via Ollama)
├── live_quotes.py           # live price feed (Kite Connect if configured, else delayed free feed)
├── kite_auth.py             # Kite Connect daily token refresh (optional, for live_quotes.py)
├── live_ticker.py           # optional terminal dashboard: live tick-by-tick price + regime/score/S-R/verdict (needs Kite Connect)
├── backtest_portfolio.py    # portfolio-level backtest harness
├── walk_forward.py          # walk-forward validation
├── paper_trader.py          # paper trading loop
├── agent_sim.py             # simulation harness
├── call_report.py           # scores logged advisory calls against actual price action
├── update_prices_kite.py    # nightly price update from Kite (append-only; see Data quality)
├── download_*.py            # NSE bhavcopy, F&O, announcements, delisted archives (historical)
├── repair_price_gaps.py     # backstop: backfills interior/corrupt price bars from Kite
├── data_integrity_check.py  # nightly scan for bad dates, price glitches, stale series
├── research_*.py            # standalone strategy validation / robustness studies
├── PREREG_*.md              # frozen pre-registration protocols (decision rules fixed
│                            #   before data collection, results appended after)
├── test_*.py                # regression tests for invariants that have broken before
└── ...
data/                        # local price data, logs, portfolio state (gitignored — not in this repo)
```

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) running locally, with a tool-calling-capable model pulled (e.g. `qwen2.5`, `llama3.1`, `mistral-nemo` — **not** plain `llama3`, which doesn't support tool calling) — only needed for `ai_assistant.py`
- No paid API keys required for backtesting or research against an existing price archive. `live_quotes.py` tries Kite Connect first when configured, and otherwise falls back to yfinance's free, ~15-minute-delayed feed — callers can tell the two apart, so a delayed quote is never presented as real-time.
- **Kite Connect is now used for the nightly price update** (`update_prices_kite.py`), because yfinance's intermittent NaN-OHLC bars kept reappearing when the whole history was re-pulled each night (see [Data quality](#data-quality)). Without it the pipeline still runs — it simply keeps whatever price data is already on disk and says so, rather than silently reverting to the source it replaced. Note the access token expires roughly daily and refreshing it needs a browser login (`python kite_auth.py refresh`), so this is a real operational dependency, not a fire-and-forget one.
- **Optional:** a Zerodha Kite Connect subscription (~₹500/mo) for true real-time NSE quotes and the terminal dashboard (`live_ticker.py`) — live price/change/day-range per tick alongside momentum score/rank, regime, and support/resistance (levels are anchored to the live price *at launch*, then held for the session — restart to re-anchor; see the module docstring for the CPU-cost tradeoff behind that choice). Watchlist-only: it displays names you're watching (defaults to the S/R panel), with no connection to your actual holdings or `portfolio_state.json`. Without Kite Connect, everything falls back transparently to the free feed — nothing breaks or degrades in functionality, only in quote latency. See `kite_auth.py` for the setup flow if you want this.

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

# Support/resistance for one or more stocks, quoted to this month's rebalance
# date. Uses live quotes by default, falling back to the last close if the
# feed is unavailable.
python support_resistance.py RELIANCE WIPRO
python support_resistance.py RELIANCE --no-live            # force the last close
python support_resistance.py RELIANCE --as-of 2026-08-12   # past/future horizon (uses closes)

# Monthly containment band — the level price is unlikely to breach before
# month-end. Printed automatically under the S/R table above; standalone here.
# This answers "how far can this reasonably go against me", NOT "when to buy".
python containment_band.py RELIANCE
python containment_band.py RELIANCE --levels               # several confidence levels

# Monthly (after the last Tuesday): score the logged S/R panels — read-only
./sr_monthly_review.sh

# Optional: refresh the live Kite Connect quote token for the day
# (live_ticker.py also detects an expired/missing token and offers to
# refresh it interactively at launch, so this is a convenience, not a
# prerequisite)
python kite_auth.py refresh

# Optional: live terminal dashboard — price/change/S-R/verdict for a
# watchlist (needs Kite Connect configured)
python live_ticker.py
```

## Data quality

**Daily price updates come from Kite Connect** (`update_prices_kite.py`); yfinance's archive is retained as the *historical* record but is no longer used for current data.

The reason is a failure mode worth knowing about: yfinance intermittently writes a bar with a real `Volume` but `NaN` OHLC. The file's last date looks current, so nothing obvious flags it — but every consumer drops that row, leaving the series stale in practice while appearing fresh. On one check, **42 of 500 files** were affected; only 5 were detectable by date alone. Because the old downloaders re-pulled the whole history nightly, every repair was undone the next evening and the same symbols kept going stale.

Both the nightly updater and `repair_price_gaps.py` (a backstop for interior gaps predating the switch) share the same safeguards:

- They measure the last **usable** bar (one with real OHLC), not the last bar present. A `max(date)` check misses exactly the failure it is meant to catch — and a mid-session partial bar can otherwise mask an older missing session behind it.
- They **append only**, never rewriting history, and refuse to splice unless Kite agrees with the existing series **at the splice point** — the newest bar the two share. This matters because Kite's history is *unadjusted* while the yfinance CSVs are split/dividend-*adjusted*: on NATIONALUM the ratio between them drifts 1.80 (2016) → 1.41 (2019) → 1.15 (2023) → 1.00 (today). Comparing a *window* rather than the splice point is subtly wrong: when a dividend goes ex, yfinance back-adjusts every prior bar, so the window straddles a step and a perfectly safe splice gets rejected. Only bars before the ex-date shift; everything from the splice point forward already agrees.
- The splice point must itself be a bar with a **usable** close. This is a second-order version of the same bug, and it bit: when the newest shared bar was one of the `NaN`-OHLC rows above, the agreement ratio evaluated to `NaN`, and `NaN > tolerance` is `False` in Python — so the guard that exists to *reject* a bad splice silently *approved* every one. 41 files carried a price-less newest bar while the updater reported them current. Both the ratio check and the tolerance comparison now reject non-finite values explicitly rather than relying on a comparison that short-circuits.
- Neither ever writes a partial candle. Kite returns the in-progress daily bar during market hours, and writing it would put a half-formed High/Low/Close into the history.

**Kite's own historical data isn't final at market close, either.** Official NSE settlement (Bhavcopy) typically finishes processing between 19:00 and 20:00 IST, sometimes later — so a nightly run at 18:15 was pulling `historical_data()` before that day's bar had actually settled. The overlap check passed anyway, because the in-flux value was still close enough to the prior close to clear tolerance, and Kite's *own* number for that same date came back materially different the next morning. This wasn't rare: on the day it was caught, roughly 350 of 500 symbols carried a small (0.1-2%), same-direction, same-day discrepancy — not 350 independent glitches, one scheduling race repeated across the universe. The fix was to stop racing settlement rather than to detect and repair it after the fact: the price pull now runs on its own schedule after midnight, well clear of the settlement window, and the evening pipeline reads whatever it most recently wrote — a constant one-day lag behind the session that just closed, not a growing one, which nothing downstream needed anyway (S/R levels are already keyed to the last *completed* close, never a live one).

A narrower, genuine version of the same symptom remains and is handled separately: a symbol can still get its newest bar written from a Kite value that hasn't settled (a retry racing a repair script, for instance), or sit mid-adjustment for a real dividend or split for a few sessions while yfinance's own back-adjustment catches up. An auto-fix distinguishes the two by shape rather than assuming either: if every *older* bar still agrees closely and only the newest one is off, it's a stale write and gets silently corrected; if several *consecutive prior* days sit at the same constant offset — a plateau, not a blip — that's a real corporate action in progress, left untouched and reported separately rather than conflated with an actual fault. (An early version of that plateau check misfired both ways before landing here: checking "does any prior bar disagree at all" flagged the vast majority of the universe, since a small residual glitch looks superficially like the start of a plateau; scanning the full lookback window for a plateau *anywhere* caught a stale, already-resolved adjustment from weeks earlier and let it block an unrelated fresh fix on the same symbol. Both were caught by checking concrete cases individually rather than trusting a flagged-count summary.) The distinction matters operationally: a symbol behind because of a real dividend needs no action and resolves itself in a few sessions, while a symbol behind for any other reason is worth investigating — and until this fix, both cases produced an identical, unexplained "disagree" line in the log.

Replacing this with an authoritative source was investigated and explicitly not adopted: NSE publishes a corporate-actions feed with ex-dates and free-text descriptions, and its numbers check out — once the compounding math for multiple overlapping corporate actions was worked out correctly, implied and empirically observed adjustments matched to within a few hundredths of a percentage point. What didn't hold up was the text parsing. NSE's phrasing isn't a stable contract (the same problem this repo had already hit parsing earnings-announcement text elsewhere), and a small sample surfaced a real gap within the first few symbols tested — dividend amounts of exactly one rupee are worded "Re 1" instead of "Rs 1", which a straightforward regex misses. Getting a hand-picked sample of seven symbols to mostly parse correctly took three iterations; making that reliable across the full universe, and keeping it reliable as NSE's wording drifts, was judged not worth it for a problem this narrow now that the scheduling fix has already removed the bulk of it.

This is deliberately **not** a wholesale migration of the *history*. Swapping every past price would move every S/R pivot, change the momentum scorer's returns, and invalidate the probability tables and every backtest figure. Kite supplies everything from the switch forward; the adjusted archive behind it is left intact. Symbols with no Kite instrument token (delisted or renamed) are reported and left alone; their series ending is correct.

If the Kite token has expired, the nightly update aborts with a message and the pipeline continues on existing data rather than silently falling back to the source it replaced.

## Data & privacy

Personal trading data — portfolio state, trade history, paper/sim books, and the advisory/S/R logs — is **not tracked**, and `.gitignore` excludes `data/`. Populate it yourself with the `download_*.py` scripts before running the advisor or backtests.

A limited set of **impersonal reference data** is tracked deliberately, because the code needs it to run and it reveals nothing about anyone's positions: the curated `sectors.json` sector map, index constituent lists, the S/R probability tables, and the corporate-announcements / delisted-price archives used by the research scripts.

If you configure the optional Kite Connect integration, credentials (API key/secret, TOTP secret, cached access token) are written to `data/secrets/` — covered by the same `.gitignore` rule, with restrictive file permissions set automatically. Never commit this directory; if you fork this repo, run `git check-ignore -v data/secrets/*` to confirm your setup excludes it before pushing anywhere.

> **Historical note.** Until 2026-07-31 this section claimed the repo shipped code only. That was wrong: 567 files under `data/` — including `portfolio_state.json` with live share quantities and entry prices — had been committed before the `.gitignore` rule existed, and `.gitignore` does not untrack already-committed files. They are untracked as of that date, but **they remain in the git history**, so treat anything committed before then as public. If you are forking or reusing this repo, run `git ls-files data/` and confirm you are comfortable with what is listed.

## Status

Actively developed. Most recent: traced a nightly "many symbols slightly stale" pattern to a scheduling race rather than a data bug — the price pull was running before NSE's settlement finished, so ~350 of 500 symbols carried a small same-direction discrepancy overnight. Moved the price update to its own after-midnight schedule and added an auto-fix that tells a genuine stale write apart from a real corporate-action adjustment in progress, rather than reporting both as an identical unexplained "disagree" (see [Data quality](#data-quality)). A follow-up idea — replacing the corporate-action detection with NSE's own structured feed instead of inferring it from price shape — checked out numerically but was shelved once the text-parsing side proved unreliable across NSE's phrasing variety; that's recorded as a rejected approach rather than left to be rediscovered later.

Earlier work: fixed a backtest universe-coverage bug that had silently excluded every stock listed after ~2020 from validation; rebuilt the advisor to match the backtested strategy's own picks (it had drifted onto a structurally anti-momentum entry model); added chart/pattern analysis, relative strength, anchored VWAP, and a composite horizon-advice tool; corrected month-end to the last Tuesday across all live consumers; and wired an optional real-time Kite Connect quote feed alongside the existing free/delayed default. Rebuilt `live_ticker.py` from a bare price flasher into a single-line-per-stock dashboard (live price/change/day-range alongside momentum score/rank, regime, and support/resistance with live-anchored level selection); several column-width and terminal-overlap bugs were found and fixed by rendering against a mock screen with overlap detection before touching a real terminal. It also auto-detects an expired Kite token at launch and walks through the refresh interactively rather than requiring a separate manual step beforehand, and was decoupled entirely from `portfolio_state.json` — it's a watchlist tool, not a book viewer, and never reflects real holdings. Several candidate strategy improvements (correlation-aware/risk-parity sizing, volatility targeting, regime-detection hysteresis, staged entry, a price-only trend-quality second scoring factor) were tested via walk-forward and deliberately not adopted — each looked favorable on a single backtest run but lost on out-of-sample evidence.

**Position sizing upgraded to conviction-weighted (2026-08-05).** Where correlation-aware sizing failed (the sector cap already does the diversification work a covariance model would try to add), tilting weight toward the momentum score's own *magnitude* — not just inverse-volatility — was a different, previously untested question, and it cleared the bar cleanly: three tilt levels tested, all three improved mean CAGR with bootstrap confidence intervals fully excluding zero, effects broad across 31-33 of 36 walk-forward windows rather than concentrated in a few, and worst-case drawdown *improved* at every tilt rather than trading return for tail risk. Adopted the middle (most conservative significant) tilt level as the production default, wired consistently across the backtester, paper trader, and AI assistant's position-sizing tool — the three had independently hand-inlined the old inverse-vol formula, a drift risk closed at the same time.

**Support/resistance overhaul.** The reach-probability table was found to measure the wrong quantity: because its builder discarded levels that were never touched, it estimated *P(bounce | touched)* while every consumer read it as *P(touch)*. That made it nearly flat across distance — a level 12%+ away scored about the same as one 1% away — and explained its weak out-of-sample correlation. Rebuilt as a true touch table (`sr_build_touchtable.py`): out-of-sample correlation roughly tripled, and probabilities now decay properly with distance. Separately, the forward-accuracy analysis was reporting a 100% hit rate on every panel; the cause was a resolution asymmetry (a touch resolved immediately while a miss had to wait out the full window, so unresolved misses were dropped and only hits survived), not a well-calibrated model. Both are documented in the source.

**Display filters must not reach the measurement layer.** The reachable band above is a presentation concern; the `P(touch)` tables are a measurement one, and they are keyed on exactly the quantity the band filters — distance. Building the tables from capped levels leaked one into the other and was measurably costly: out-of-sample correlation fell 0.46 → 0.32 at 10d and 0.48 → 0.30 at 15d, thin cells went 2/24 → 6/24, and three cells emptied entirely (`12%+` at low volatility reached **zero** observations). The deeper failure is that a table trained only on reachable levels never *sees* an unreachable one, so it loses the ability to say a level is unreachable — the far bucket read 19.5% where uncapped data says 0.0%, reproducing the flat-in-distance signature of the `P(bounce|touched)` bug described above. The table builders and both S/R backtests therefore opt out of the band explicitly (`reachable_only=False`) and train on raw pivots, while everything user-facing keeps the cap. Restoring that separation returned all four tables to 0.617 / 0.581 / 0.548 / 0.529 with **0/24 thin cells** — better than before the band existed. This distinction had to be drawn three times in two days under three different symptoms; the general lesson is that when level *selection* changes, anything keyed on that selection needs re-checking, and a row-count check will not catch it because the count stays identical while the distribution shifts underneath.

**The S/R model is at its practical ceiling.** Rather than keep proposing tweaks, a headroom analysis was run: an *oracle* table fitted on the holdout it is scored against — one that has already seen the answers — beats the production table by only ~0.008 correlation. So the 24-cell lookup already extracts nearly everything its two features can express, and bucket or threshold retuning would be fitting noise. Consistent with that, a 16-variant sweep (alternative volatility estimators including Parkinson/Garman-Klass/Rogers-Satchell, bucket geometries, ATR-normalised distance, logistic and gradient-boosted models) and a delivery-volume "institutional flow" test all failed a pre-registered bar of +0.02 correlation across a majority of horizons; the single best candidate's bootstrap confidence interval included zero at every horizon. The bucket table is kept deliberately: it matches fitted models within noise, stays inspectable, and guarantees that probability is non-decreasing in horizon — an invariant a fitted model does not provide.

**Asking a better question instead of building a better model.** The levels were doing exactly what they were designed to do and still not answering the question a holder actually has. Ordered purely by proximity, the nearest support sat a median 2.1% below spot with a ~94% touch probability — correct, and useless: of course price revisits a level 2% away within a month. Two changes followed. Levels now must clear a volatility-scaled separation (above), which moved median S1 from −2.1% to −4.5% and cut levels sitting inside 0.5% of spot from 20-of-61 to 1-of-61. And a **containment band** was added to answer the complementary question directly, since no amount of tuning a `P(touch)` table can produce a `P(hold)` answer — selecting the *nearest* pivot is optimal for one and worst-possible for the other.

**Intraday data was scoped, and mostly said no.** Kite's historical intraday reach turned out to be ~11 years rather than the few weeks assumed (15-minute bars back to 2015, ~200 symbols × 3 years in 18 minutes), which made several previously-untestable questions testable immediately. Its clearest contribution was **fill realism**: a daily-`Low` touch test — the standard way this repo and most backtests model "price reached my level" — overstates win rate by ~2pp against a realistic 30-minute persistence rule and ~9pp against a close-only rule, because a low printed for six minutes is not a fill a non-intraday trader would ever get.

**Research: what was tested and rejected.** Trading the containment band was tested under a frozen protocol and **rejected** — holdout win rate 43.9%/49.0% against a pre-registered 55% bar, across 199 symbols and ~40,000 observations. The mechanism is adverse selection, verified rather than assumed: at a 5% band, observations that *filled* were contained 9.5% of the time versus 100% for those that did not. The fill itself is the bad news. A follow-up batch of six pre-registered hypotheses — selling into resistance, volume behaviour at the level, intraday realised volatility as the model's volatility axis, multi-method level confluence, prior touch count, and regime conditioning — was run as one family with a Holm-Bonferroni correction, since six tests at nominal *p*<0.05 carry a ~26% chance that at least one looks significant by luck. **All six were rejected.** Selling into resistance, the one with a genuine prior, came out significantly *worse* than holding (−0.62pp, 95% CI excluding zero in the wrong direction) — and it had looked like a winner on the training period (+0.63pp) before the time-based holdout reversed it, which is the clearest argument in this repo for never fitting without one. One hypothesis passed its corrected significance threshold and was still rejected: its entire effect lived in a single distance bucket with 80% of observations in one bin, which is what an effect-size floor exists to catch. A *p*-value alone is not adoption.

## License

MIT — see [LICENSE](LICENSE).
