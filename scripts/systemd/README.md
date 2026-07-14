# stock_ai daily-pipeline scheduling

Installed copies live in `~/.config/systemd/user/`. To (re)install:

    cp stockai-daily.service stockai-daily.timer ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now stockai-daily.timer
    loginctl enable-linger dhrumil   # timers fire even before desktop login

Fires weekdays 18:15 IST. `Persistent=true` = if the machine was OFF at
18:15, the run fires at the next boot instead; run_daily_log.sh's
market-hours guard defers a during-market-hours catch-up to the evening.
Days the machine never powers on are skipped — paper_trader/agent_sim are
idempotent per index date and agent_sim self-heals a missed month-end
(late rotation at current prices).
