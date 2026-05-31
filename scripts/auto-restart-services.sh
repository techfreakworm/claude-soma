#!/usr/bin/env bash
# scripts/auto-restart-services.sh
#
# Detached helper: restart one or more claude-soma-*.service units.
# Spawned by notify_inject.sh when a RESTART REQUIRED MILESTONE is detected.
# Must be run via setsid/nohup so that restarting claude-soma-channel.service
# does not kill this subprocess.
#
# Usage:
#   setsid nohup sudo bash /opt/claude-soma/scripts/auto-restart-services.sh \
#       "claude-soma-api.service,claude-soma-channel.service" \
#       >>/tmp/auto-restart-services.log 2>&1 &
#
# Environment:
#   HERMES_AUTO_RESTART_WINDOW_UTC — Unix epoch expiry timestamp (required).
#       If the current time is past this value, the script exits without
#       restarting anything. If unset, no restart happens (opt-in design).
#
# Exit codes:
#   0 — normal exit (restarted, skipped, or window expired)
#   1 — no services argument provided

set -uo pipefail

SERVICES_RAW="${1:-}"
LOG_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"
LOCKFILE="/tmp/claude-soma-auto-restart.lock"

_log() {
    echo "${LOG_TS} auto-restart-services.sh: $1"
}

if [[ -z "$SERVICES_RAW" ]]; then
    _log "ERROR: no services argument provided"
    exit 1
fi

# Acquire lock — prevent concurrent runs (e.g. double-fire from two hook invocations)
exec 9>"$LOCKFILE"
if ! flock -n 9; then
    _log "Another restart is already in progress; exiting"
    exit 0
fi

# Validate restart window
WINDOW_UTC="${HERMES_AUTO_RESTART_WINDOW_UTC:-}"
if [[ -z "$WINDOW_UTC" ]]; then
    _log "HERMES_AUTO_RESTART_WINDOW_UTC not set; auto-restart disabled"
    exit 0
fi

NOW_EPOCH="$(date +%s 2>/dev/null)" || NOW_EPOCH=""
if [[ -z "$NOW_EPOCH" ]]; then
    _log "ERROR: cannot read current time; aborting for safety"
    exit 0
fi
if [[ "$NOW_EPOCH" -gt "$WINDOW_UTC" ]]; then
    _log "Restart window expired (window=${WINDOW_UTC}, now=${NOW_EPOCH}); skipping"
    exit 0
fi

_log "Restart window valid (expires ${WINDOW_UTC}, now ${NOW_EPOCH})"
_log "Services requested: ${SERVICES_RAW}"

# Parse comma-separated services list
IFS=',' read -ra SERVICE_ARRAY <<< "$SERVICES_RAW"
for RAW_SVC in "${SERVICE_ARRAY[@]}"; do
    SVC="${RAW_SVC// /}"  # strip spaces
    [[ -z "$SVC" ]] && continue

    # Validate service name: only allow claude-soma-<lowercase-dashes>.service
    # This prevents shell injection and restricts restarts to our own units.
    if [[ ! "$SVC" =~ ^claude-soma-[a-z][a-z0-9-]*\.service$ ]]; then
        _log "SKIP invalid service name: ${SVC}"
        continue
    fi

    _log "Restarting ${SVC}..."
    if sudo systemctl restart "$SVC"; then
        _log "OK: ${SVC} restarted"
    else
        _log "WARN: systemctl restart ${SVC} exited non-zero"
    fi
done

_log "Done"
exit 0
