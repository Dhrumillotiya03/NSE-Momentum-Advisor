# stock_ai daily-pipeline scheduling

Installed copies live in `~/.config/systemd/user/`. To (re)install the live
schedule:

    cp stockai-price-update.service stockai-price-update.timer ~/.config/systemd/user/
    cp stockai-intraday.service stockai-intraday.timer ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now stockai-price-update.timer
    systemctl --user enable --now stockai-intraday.timer
    loginctl enable-linger dhrumil   # timers fire even before desktop login

## Live schedule

**`stockai-price-update`** (Tue-Sat 00:30 IST) runs `run_price_update.sh` —
the FULL pipeline: price update (after Kite's Bhavcopy settlement, typically
19:00-20:00+ IST the prior evening) through S/R logging, exit engine, paper
trader, advisor, news watchdog, agent-sim. `Persistent=true` = if the
machine was OFF at 00:30, the run fires at the next boot instead;
run_price_update.sh is safe to run at any hour (a during-market-hours run
writes today's partial candle, then trims it, so downstream steps only ever
see completed sessions). Days the machine never powers on are skipped —
paper_trader/agent_sim are idempotent per index date and agent_sim
self-heals a missed month-end (late rotation at current prices).

**`stockai-intraday`** — unchanged, separate cadence (see its own service
file), runs `intraday_watch.py` / `market_scanner.py` during market hours.

## Retired

**`stockai-daily`** (weekdays 18:15 IST, ran `run_daily_log.sh`) is
DISABLED as of 2026-08-07 — its steps were folded into
`stockai-price-update` above (two schedules collapsed into one; nothing
downstream ever needed same-day prices or evening-specific timing). The
unit files are kept here for reference/rollback, and `run_daily_log.sh`
itself still works standalone if run by hand (e.g. the Desktop launcher),
it just has no independent schedule anymore. Do NOT re-enable this timer
alongside `stockai-price-update` — that would re-introduce the two-pipeline
ordering question this merge removed, with both writing to the same
`cron_daily_log.log`.
