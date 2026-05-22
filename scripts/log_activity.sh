#!/usr/bin/env bash
# scripts/log_activity.sh
#
# Appends a JSON line to ~/.claude-soma/activity.jsonl on every PostToolUse.
# Dashboard SSE tails this file.

set -euo pipefail

EVENT="$(cat)"
LOG_DIR="${HOME}/.claude-soma"
LOG_FILE="${LOG_DIR}/activity.jsonl"

mkdir -p "$LOG_DIR"

TS="$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)"
TOOL="$(jq -r '.tool_name // "unknown"' <<<"$EVENT")"
SESSION="$(jq -r '.session_id // "unknown"' <<<"$EVENT")"
SUMMARY="$(jq -c '.tool_input // {}' <<<"$EVENT" | head -c 500)"
RESULT="$(jq -r '.tool_result_summary // ""' <<<"$EVENT" | head -c 500)"

jq -nc \
  --arg ts "$TS" \
  --arg t "$TOOL" \
  --arg s "$SESSION" \
  --argjson inp "$SUMMARY" \
  --arg r "$RESULT" \
  '{ts: $ts, tool: $t, session: $s, input_summary: $inp, result_summary: $r}' \
  >> "$LOG_FILE"

# Truncate file if it grows past 50 MB (basic rotation)
if [[ -f "$LOG_FILE" ]] && [[ $(stat -c%s "$LOG_FILE") -gt 52428800 ]]; then
    tail -n 10000 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi
