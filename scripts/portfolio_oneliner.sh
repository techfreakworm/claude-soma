#!/usr/bin/env bash
# scripts/portfolio_oneliner.sh
#
# Compose a one-line portfolio status from local state and send it to
# the user via Telegram Bot API. Designed to be invoked by
# claude-soma-portfolio-oneliner.timer (Mon-Fri 09:00 IST = 03:30 UTC).
#
# Sources bot token from /home/ubuntu/.claude/channels/telegram/.env
# Logs to /var/log/claude-soma/portfolio_oneliner.log

set -uo pipefail

LOG=/var/log/claude-soma/portfolio_oneliner.log
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
CHAT_ID="${HERMES_NOTIFY_CHAT_ID:-${TELEGRAM_CHAT_ID:-}}"
REPO=/opt/claude-soma
REGISTRY=/opt/claude-soma/registry.sqlite
ENV_FILE=/home/ubuntu/.claude/channels/telegram/.env

log() { echo "[$TS] $*" >> "$LOG"; }

# Operator notify: Discord primary, Telegram best-effort fallback.
NOTIFY_LIB="${NOTIFY_LIB:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/notify_lib.sh}"
# shellcheck source=/dev/null
source "$NOTIFY_LIB"

# Day-of-week greeting (Mon morning / Tue morning / ...)
DOW="$(date +%a)"

# Active project-leads
if [[ -r "$REGISTRY" ]]; then
    ACTIVE_COUNT=$(sqlite3 "$REGISTRY" "SELECT COUNT(*) FROM projects WHERE status='active';" 2>/dev/null || echo "?")
else
    ACTIVE_COUNT="?"
fi

# Last commit on /opt/claude-soma — relative time + subject (truncated)
if LAST=$(git -C "$REPO" log -1 --format='%cr|%s' 2>/dev/null); then
    LAST_AGE="${LAST%%|*}"
    LAST_SUBJ="${LAST#*|}"
    if [[ ${#LAST_SUBJ} -gt 50 ]]; then
        LAST_SUBJ="${LAST_SUBJ:0:47}..."
    fi
    COMMIT_PART="last commit ${LAST_AGE}: \"${LAST_SUBJ}\""
else
    COMMIT_PART="last commit unknown"
fi

# Dirty file count in /opt/claude-soma
DIRTY_COUNT=$(git -C "$REPO" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
if [[ "$DIRTY_COUNT" -gt 0 ]]; then
    DIRTY_PART=" · ${DIRTY_COUNT} dirty"
else
    DIRTY_PART=""
fi

# Compose: e.g.
#   "Mon morning · 0 projects active · last commit 23h ago: \"V1 ship\" · 3 dirty"
MSG="${DOW} morning · ${ACTIVE_COUNT} project(s) active · ${COMMIT_PART}${DIRTY_PART}"

# Cap to 200 chars (Telegram supports much longer, but the user asked for terse)
if [[ ${#MSG} -gt 200 ]]; then
    MSG="${MSG:0:197}..."
fi

if soma_notify "$MSG"; then
    log "sent ok (${#MSG} chars): ${MSG}"
    exit 0
else
    log "notify FAILED (discord + telegram both failed) msg=${MSG}"
    exit 2
fi
