#!/usr/bin/env bash
set -uo pipefail

STATE="${LISTENER_HEALTHCHECK_STATE:-/var/lib/claude-soma/listener-healthcheck.state}"
mkdir -p "$(dirname "$STATE")" 2>/dev/null || true

if curl -sf --max-time 3 http://127.0.0.1:9100/health 2>/dev/null | grep -q '"status": *"ok"'; then
    [[ -f "$STATE" ]] && rm -f "$STATE"
    exit 0
fi

# Unhealthy
[[ -f "$STATE" ]] && exit 0  # already alerted this outage

TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "$TS" > "$STATE"

if [[ -r /home/ubuntu/.claude/channels/telegram/.env ]]; then
    source /home/ubuntu/.claude/channels/telegram/.env
    curl -sX POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${HERMES_NOTIFY_CHAT_ID:-${TELEGRAM_CHAT_ID:-}}" \
        --data-urlencode "text=ALERT: hermes_api listener /health failed at $TS" \
        --max-time 5 >/dev/null 2>&1 || true
fi

exit 0
