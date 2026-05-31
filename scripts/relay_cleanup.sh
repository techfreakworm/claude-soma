#!/usr/bin/env bash
# scripts/relay_cleanup.sh
#
# Delete relay files older than HERMES_RELAY_RETENTION_DAYS (default 7).
# Skips any directory that contains a .pin marker file (long-term pinned).
# Invoked daily at 04:15 UTC by claude-soma-relay-cleanup.timer.
#
# Environment:
#   HERMES_RELAY_ROOT           relay root dir (default /var/lib/claude-soma/relay)
#   HERMES_RELAY_RETENTION_DAYS days before deletion (default 7)
#   HERMES_RELAY_CLEANUP_LOG    log path (default /var/log/claude-soma/relay-cleanup.log)

set -euo pipefail

ROOT="${HERMES_RELAY_ROOT:-/var/lib/claude-soma/relay}"
RETENTION="${HERMES_RELAY_RETENTION_DAYS:-7}"
LOG="${HERMES_RELAY_CLEANUP_LOG:-/var/log/claude-soma/relay-cleanup.log}"

_log() {
    local ts event path
    ts="$(date -u +%s)"
    event="$1"
    path="$2"
    printf '%s\n' "{\"ts\":${ts},\"event\":\"${event}\",\"path\":\"${path}\"}" >> "$LOG" 2>/dev/null || true
}

if [ ! -d "$ROOT" ]; then
    echo "relay-cleanup: root does not exist: $ROOT — nothing to do"
    exit 0
fi

mkdir -p "$(dirname "$LOG")"

deleted=0
skipped_pinned=0

# Find files older than RETENTION days; process each
while IFS= read -r -d '' file; do
    dir="$(dirname "$file")"

    # Skip if the containing directory has a .pin marker
    if [ -f "${dir}/.pin" ]; then
        skipped_pinned=$((skipped_pinned + 1))
        continue
    fi

    # Attempt deletion; log per-file failures and continue (exit 0 guaranteed)
    if rm -f "$file" 2>/dev/null; then
        _log "deleted" "$file"
        deleted=$((deleted + 1))
    else
        _log "error_rm" "$file"
    fi
done < <(find "$ROOT" -type f -not -name ".*" -mtime +"${RETENTION}" -print0 2>/dev/null)

echo "relay-cleanup: deleted=${deleted} skipped_pinned=${skipped_pinned}"
