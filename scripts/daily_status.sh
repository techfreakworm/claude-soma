#!/usr/bin/env bash
# scripts/daily_status.sh
#
# Build a per-lead status digest and send it as a Telegram DM.
# Fired daily by claude-soma-daily-status.timer (10:00 IST).
#
# Prompt budget per lead (combined target: <3k tokens for all active leads):
#   pane tail:  10 lines  per lead
#   git log:     4 commits per lead
#   notes:     600 chars  per lead (NEXT.md + MEMORY)

set -uo pipefail

REGISTRY="${HERMES_ORCH_DB:-/opt/claude-soma/registry.sqlite}"
LOG="${HERMES_DAILY_STATUS_LOG:-/var/log/claude-soma/daily-status.log}"
CLAUDE_BIN="${HERMES_CLAUDE_BIN:-/home/ubuntu/.local/bin/claude}"
CHAT_ID="${HERMES_NOTIFY_CHAT_ID:-${TELEGRAM_CHAT_ID:-}}"
ENV_FILE="${TG_ENV_FILE:-/home/ubuntu/.claude/channels/telegram/.env}"
TMUX_BIN="${HERMES_TMUX_BIN:-/usr/bin/tmux}"

# Prompt budget per lead (targets <3k tokens total across all active leads)
PANE_TAIL=10    # pane tail lines per lead
GIT_COMMITS=4   # recent git commits per lead
NOTES_CHARS=600 # NEXT/MEMORY chars per lead

mkdir -p "$(dirname "$LOG")"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
DATE="$(date -u +%Y-%m-%d)"
log() { echo "[$TS] $*" >> "$LOG"; }

if [[ ! -r "$ENV_FILE" ]]; then
    log "FATAL: cannot read $ENV_FILE"
    exit 1
fi

TELEGRAM_BOT_TOKEN=""
# shellcheck source=/dev/null
source "$ENV_FILE"
if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
    log "FATAL: TELEGRAM_BOT_TOKEN unset after sourcing $ENV_FILE"
    exit 1
fi

if [[ ! -r "$REGISTRY" ]]; then
    log "FATAL: registry not readable: $REGISTRY"
    exit 1
fi

PROMPT="=== DAILY STATUS DIGEST ${DATE} ===

Summarise each active lead in 1-2 sentences: what it last did and its current state.

"

COUNT=0
while IFS='|' read -r name agent_id cwd; do
    [[ -z "$name" ]] && continue
    COUNT=$((COUNT + 1))
    PROMPT+="--- Lead: ${name} ---
"

    # Pane tail (10 lines per lead)
    PANE_OUT="(session unavailable)"
    if "$TMUX_BIN" -L "soma-lead-${name}" has-session -t "soma-proj-${name}" 2>/dev/null; then
        PANE_OUT=$("$TMUX_BIN" -L "soma-lead-${name}" capture-pane -p -t "soma-proj-${name}" \
            2>/dev/null | tail -n "$PANE_TAIL" || echo "(capture failed)")
    fi
    PROMPT+="Pane (last ${PANE_TAIL} lines):
${PANE_OUT}
"

    # Git log (4 commits per lead)
    GIT_OUT="(no git)"
    if [[ -n "$cwd" && -d "$cwd" ]]; then
        GIT_OUT=$(git -C "$cwd" log --oneline -n "$GIT_COMMITS" 2>/dev/null \
            || echo "(git error)")
    fi
    PROMPT+="Recent commits (${GIT_COMMITS}):
${GIT_OUT}
"

    # NEXT/MEMORY notes (600 chars per lead)
    NOTES=""
    if [[ -n "$cwd" && -r "${cwd}/NEXT.md" ]]; then
        NOTES=$(head -c "$NOTES_CHARS" "${cwd}/NEXT.md" 2>/dev/null || true)
    fi
    if [[ ${#NOTES} -lt $NOTES_CHARS && -n "$cwd" ]]; then
        REMAINING=$((NOTES_CHARS - ${#NOTES}))
        for mf in "${cwd}/MEMORY.md" "${cwd}/.claude/memory/MEMORY.md"; do
            [[ -r "$mf" ]] || continue
            EXTRA=$(head -c "$REMAINING" "$mf" 2>/dev/null || true)
            NOTES="${NOTES} ${EXTRA}"
            break
        done
    fi
    NOTES="${NOTES:0:$NOTES_CHARS}"
    if [[ -n "$NOTES" ]]; then
        PROMPT+="Notes (${NOTES_CHARS} chars max):
${NOTES}
"
    fi
    PROMPT+="
"
done < <(sqlite3 "$REGISTRY" \
    "SELECT name, agent_id, cwd FROM projects WHERE status='active' ORDER BY name;" \
    2>/dev/null)

if [[ "$COUNT" -eq 0 ]]; then
    log "no active leads; skipping digest"
    exit 0
fi

DIGEST=$("$CLAUDE_BIN" -p "$PROMPT" --output-format text 2>/dev/null \
    || echo "(claude -p failed)")

API_URL="https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage"
RESP_FILE="/tmp/daily-status-$$.resp"
HTTP=$(curl -s -o "$RESP_FILE" -w "%{http_code}" \
    --max-time 30 \
    -X POST "$API_URL" \
    -d "chat_id=${CHAT_ID}" \
    --data-urlencode "text=${DIGEST}" 2>>"$LOG")

if [[ "$HTTP" == "200" ]]; then
    log "sent ok (${COUNT} leads, ${#DIGEST} chars)"
    rm -f "$RESP_FILE"
else
    RESP=$(head -c 500 "$RESP_FILE" 2>/dev/null || echo "<no response>")
    log "Telegram send FAILED http=${HTTP} resp=${RESP}"
    rm -f "$RESP_FILE"
    exit 2
fi
