#!/usr/bin/env bash
# scripts/channel_clear.sh
#
# Send /clear to the persistent Claude channel pane to trim the transcript.
# Intended for weekly execution via claude-soma-channel-clear.timer, which
# keeps the --continue context from compounding across weeks of uptime.
#
# tmux convention (matches healthcheck.sh and claude-soma-channel.service):
#   session: hermes
#   pane target: hermes:0
#   default socket (no -L / -S flags)
#
# Environment overrides for testing:
#   CHANNEL_CLEAR_LOG   path to log file (default /var/log/claude-soma/channel-clear.log)
#   CHANNEL_CLEAR_SLEEP seconds to sleep after sending keys (default 3)

set -uo pipefail

LOG="${CHANNEL_CLEAR_LOG:-/var/log/claude-soma/channel-clear.log}"
TMUX_TARGET="hermes:0"
SLEEP_SECS="${CHANNEL_CLEAR_SLEEP:-3}"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

mkdir -p "$(dirname "$LOG")" 2>/dev/null || true

if ! tmux send-keys -t "$TMUX_TARGET" '/clear' 2>>"$LOG"; then
    echo "[$TS] channel-clear: send-keys /clear FAILED (session $TMUX_TARGET absent?)" >> "$LOG"
    exit 0
fi

if ! tmux send-keys -t "$TMUX_TARGET" Enter 2>>"$LOG"; then
    echo "[$TS] channel-clear: send-keys Enter FAILED" >> "$LOG"
    exit 0
fi

sleep "$SLEEP_SECS"

echo "[$TS] channel-clear: ok" >> "$LOG"
exit 0
