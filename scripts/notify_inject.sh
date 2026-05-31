#!/usr/bin/env bash
# scripts/notify_inject.sh
#
# UserPromptSubmit hook — injects recent unread lead lifecycle events into
# additionalContext so the bot always has current lead status in-context.
#
# Queries 127.0.0.1:9100/events?unread_only=true&limit=20, formats a compact
# block, marks the rows as hook-injected, and emits:
#   {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
#                           "additionalContext": "..."}}
#
# Fail-open: if the listener is down, curl fails, or jq is missing,
# exits 0 with no output — the bot's turn proceeds normally.
# curl to 127.0.0.1 is explicitly allowed by orchestrator_gate.sh.

set -uo pipefail

LOG_FILE="/var/log/claude-soma/notify-inject.log"
NOTIFY_PORT="${HERMES_NOTIFY_PORT:-9100}"
MAX_CHARS="${HERMES_NOTIFY_HOOK_MAX_CHARS:-2000}"
ENDPOINT="http://127.0.0.1:${NOTIFY_PORT}"

_log_error() {
    local ts
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)"
    mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null
    echo "${ts} notify_inject.sh: $1" >> "$LOG_FILE" 2>/dev/null
    true
}

# Fail-open: missing jq or curl — emit nothing and exit cleanly
command -v jq >/dev/null 2>&1 || exit 0
command -v curl >/dev/null 2>&1 || exit 0

# Query unread events from the HTTP listener
RAW_RESPONSE="$(curl -sf --max-time 3 \
    "${ENDPOINT}/events?unread_only=true&limit=20" 2>/dev/null)" || {
    # Listener down or unreachable — fail-open
    exit 0
}

# Parse events + open pending inputs
EVENTS="$(jq -r '.events // []' <<<"$RAW_RESPONSE" 2>/dev/null)" || exit 0
PENDING="$(jq -r '.open_pending_inputs // []' <<<"$RAW_RESPONSE" 2>/dev/null)" || {
    PENDING="[]"
}

EVENT_COUNT="$(jq 'length' <<<"$EVENTS" 2>/dev/null)" || exit 0
PENDING_COUNT="$(jq 'length' <<<"$PENDING" 2>/dev/null)" || PENDING_COUNT=0

# If nothing to inject: emit empty object and exit
if [[ "$EVENT_COUNT" -eq 0 && "$PENDING_COUNT" -eq 0 ]]; then
    printf '{}'
    exit 0
fi

# Collect event IDs to mark as injected after formatting
EVENT_IDS="$(jq '[.[].id]' <<<"$EVENTS" 2>/dev/null)" || EVENT_IDS="[]"

# Build the context block
BLOCK="## Recent lead events"$'\n'

# Format events (newest-first from the API, reverse for chronological display)
CHRONOLOGICAL="$(jq 'reverse' <<<"$EVENTS" 2>/dev/null)" || CHRONOLOGICAL="$EVENTS"

EVENT_LINES="$(jq -r '.[] | "• [" + .type + "] " + .lead + ": " + (
    if .type == "STARTED" then
        (.payload_json | fromjson | .description // "started")
    elif .type == "MILESTONE" then
        (.payload_json | fromjson |
            .progress +
            (if .percent != null then " (" + (.percent | tostring) + "%)" else "" end))
    elif .type == "COMPLETED" then
        (.payload_json | fromjson | .summary // "completed") | .[0:200]
    elif .type == "NEEDS_INPUT" then
        "waiting for input: " + (.payload_json | fromjson | .question // "")
    elif .type == "ERROR" then
        "ERROR: " + (.payload_json | fromjson | .error // "")
    else
        (.payload_json | fromjson | tostring | .[0:100])
    end
)' <<<"$CHRONOLOGICAL" 2>/dev/null)" || EVENT_LINES=""

if [[ -n "$EVENT_LINES" ]]; then
    BLOCK="${BLOCK}${EVENT_LINES}"$'\n'
fi

# Inject the oldest open NEEDS_INPUT question (FIFO)
if [[ "$PENDING_COUNT" -gt 0 ]]; then
    OLDEST_PI="$(jq '.[0]' <<<"$PENDING" 2>/dev/null)"
    if [[ -n "$OLDEST_PI" && "$OLDEST_PI" != "null" ]]; then
        PI_EVENT_ID="$(jq -r '.event_id' <<<"$OLDEST_PI" 2>/dev/null)"
        PI_LEAD="$(jq -r '.lead' <<<"$OLDEST_PI" 2>/dev/null)"
        PI_QUESTION="$(jq -r '.question' <<<"$OLDEST_PI" 2>/dev/null)"
        PI_OPTIONS="$(jq -r '.options | if length > 0 then " (options: " + (join(", ")) + ")" else "" end' <<<"$OLDEST_PI" 2>/dev/null)" || PI_OPTIONS=""

        BLOCK="${BLOCK}"$'\n'"OPEN NEEDS_INPUT [event_id=${PI_EVENT_ID}]: Lead <code>${PI_LEAD}</code> is waiting for your answer: \"${PI_QUESTION}\"${PI_OPTIONS}"$'\n'

        if [[ "$PENDING_COUNT" -gt 1 ]]; then
            REMAINING=$(( PENDING_COUNT - 1 ))
            BLOCK="${BLOCK}(${REMAINING} more pending question(s) will appear after this one is resolved)"$'\n'
        fi
    fi
fi

# Enforce char budget — truncate oldest events if needed
BLOCK_LEN="${#BLOCK}"
if [[ "$BLOCK_LEN" -gt "$MAX_CHARS" ]]; then
    BLOCK="${BLOCK:0:$MAX_CHARS}"
    BLOCK="${BLOCK}... (truncated — additional events in registry.sqlite)"
fi

# Auto-trigger restart if any unread MILESTONE contains "RESTART REQUIRED".
# Only fires when HERMES_AUTO_RESTART_WINDOW_UTC is set and not yet expired.
# The helper is spawned as a detached subprocess (setsid) so that restarting
# claude-soma-channel.service does not kill it mid-flight.
AUTO_RESTART_WINDOW="${HERMES_AUTO_RESTART_WINDOW_UTC:-}"
if [[ -n "$AUTO_RESTART_WINDOW" && "$EVENT_COUNT" -gt 0 ]]; then
    # Extract services list from first RESTART REQUIRED MILESTONE (if any).
    # Payload format: {"progress": "RESTART REQUIRED — ... (services: svc1, svc2)"}
    RESTART_SERVICES="$(jq -r '
        .[] |
        select(.type == "MILESTONE") |
        .payload_json | fromjson | .progress // "" |
        select(test("RESTART REQUIRED")) |
        capture("services:\\s*(?P<svcs>[^)]+)") |
        .svcs | gsub("\\s+";"")
    ' <<<"$CHRONOLOGICAL" 2>/dev/null | head -1)" || RESTART_SERVICES=""

    if [[ -n "$RESTART_SERVICES" ]]; then
        NOW_EPOCH="$(date +%s 2>/dev/null)" || NOW_EPOCH=""
        if [[ -n "$NOW_EPOCH" && "$NOW_EPOCH" -le "$AUTO_RESTART_WINDOW" ]]; then
            _log_error "auto-restart triggered: services=${RESTART_SERVICES} window=${AUTO_RESTART_WINDOW}"
            command -v setsid >/dev/null 2>&1 && \
            setsid nohup sudo bash /opt/claude-soma/scripts/auto-restart-services.sh \
                "$RESTART_SERVICES" \
                >>/tmp/auto-restart-services.log 2>&1 &
        fi
    fi
fi

# Mark events as hook-injected via the HTTP listener
if [[ "$(jq 'length' <<<"$EVENT_IDS" 2>/dev/null)" -gt 0 ]]; then
    MARK_BODY="$(jq -c '{event_ids: .}' <<<"$EVENT_IDS" 2>/dev/null)"
    curl -sf --max-time 3 \
        -X POST \
        -H "Content-Type: application/json" \
        -d "$MARK_BODY" \
        "${ENDPOINT}/mark_read" >/dev/null 2>&1 || {
        _log_error "mark_read failed (non-fatal)"
    }
fi

# Emit the hook output
jq -cn --arg ctx "$BLOCK" '{
    hookSpecificOutput: {
        hookEventName: "UserPromptSubmit",
        additionalContext: $ctx
    }
}'
exit 0
