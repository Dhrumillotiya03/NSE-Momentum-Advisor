#!/bin/bash
# git_autocommit.sh — commit CODE changes locally. Never pushes.
#
# Deliberately does NOT push. This repo is public and the working tree carries
# live trading data; an automatic push is one mis-scoped `git add` away from
# publishing a book. Review with `git log` / `git show`, then push yourself:
#     git push
#
# Scope is an explicit ALLOW-LIST of code paths, not `git add -A`. A blanket
# add would re-stage anything a future script drops into the tree, which is how
# personal data got published here in the first place.
#
# Usage:
#     ./git_autocommit.sh                 # commit code changes, if any
#     ./git_autocommit.sh "message"       # with a custom message

cd "$(dirname "$0")/.." || exit 1

# Code + docs only. Never data/.
PATHS=(scripts/*.py scripts/*.sh CLAUDE.md README.md .gitignore requirements.txt config.yaml.example)

# Refuse to run if personal data has somehow become tracked again — that is a
# bug worth stopping for, not working around.
LEAKS=$(git ls-files data/ 2>/dev/null | grep -E \
    'portfolio_state|trade_history|paper_state|paper_equity|book_peak|held_close_snapshot|_agent_sim/|_quarantine/|sr_daily_log|sr_dynamic_log|advisor_calls_log|scanner_log|intraday_watch_log' \
    || true)
if [ -n "$LEAKS" ]; then
    echo "ABORT: personal data is tracked again:" >&2
    echo "$LEAKS" | sed 's/^/  /' >&2
    echo "Untrack it first:  git rm --cached <path>" >&2
    exit 1
fi

# Only pass paths that exist. `git add` aborts the WHOLE invocation on a
# single missing pathspec ("fatal: pathspec ... did not match any files"), so
# one absent optional file (config.yaml.example) silently staged nothing at
# all. Errors are no longer discarded either — a failed add must be loud.
EXISTING=()
for p in "${PATHS[@]}"; do
    [ -e "$p" ] && EXISTING+=("$p")
done
if [ ${#EXISTING[@]} -eq 0 ]; then
    echo "Nothing to stage." >&2
    exit 0
fi
git add -- "${EXISTING[@]}" || { echo "git add failed" >&2; exit 1; }

if git diff --cached --quiet; then
    echo "No code changes to commit."
    exit 0
fi

echo "Staged:"
git diff --cached --stat

MSG="${1:-chore: sync code changes $(date '+%Y-%m-%d %H:%M')}"
git commit -q -m "$MSG"
echo
echo "Committed locally: $(git log --oneline -1)"

AHEAD=$(git rev-list --count @{u}..HEAD 2>/dev/null || echo "?")
echo "$AHEAD commit(s) ahead of origin. Push when ready:  git push"
