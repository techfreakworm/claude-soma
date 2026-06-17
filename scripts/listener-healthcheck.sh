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

# Operator notify: Discord primary, Telegram best-effort fallback.
NOTIFY_LIB="${NOTIFY_LIB:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/notify_lib.sh}"
# shellcheck source=/dev/null
source "$NOTIFY_LIB"
soma_notify "ALERT: hermes_api listener /health failed at $TS" || true

exit 0
