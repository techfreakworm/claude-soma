#!/usr/bin/env bash
# scripts/healthcheck.sh
#
# Verify channel session, api, and frontend are responsive. Restart channel
# if it's not. Logs to /var/log/claude-soma/healthcheck.log.

set -uo pipefail

LOG=/var/log/claude-soma/healthcheck.log
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# 1. API healthz
if ! curl -fsS --max-time 5 http://127.0.0.1:9000/api/healthz >/dev/null; then
    echo "[$TS] api: UNHEALTHY, restarting" >> "$LOG"
    sudo systemctl restart claude-soma-api.service
fi

# 2. Frontend home (should serve 200)
if ! curl -fsS --max-time 5 http://127.0.0.1:3000/ -o /dev/null; then
    echo "[$TS] frontend: UNHEALTHY, restarting" >> "$LOG"
    sudo systemctl restart claude-soma-frontend.service
fi

# 3. Channel tmux session present
# Check as user ubuntu — tmux sessions are per-user, and this script runs
# as root from systemd. A bare `tmux has-session` from root always fails
# (no /tmp/tmux-0 server), which caused a needless restart every 10 minutes
# and killed the channel + every running project-lead session with it.
if ! sudo -u ubuntu tmux has-session -t hermes 2>/dev/null; then
    echo "[$TS] channel: tmux missing, restarting" >> "$LOG"
    sudo systemctl restart claude-soma-channel.service
fi

echo "[$TS] healthcheck: ok" >> "$LOG"
