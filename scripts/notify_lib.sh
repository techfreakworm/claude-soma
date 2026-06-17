#!/usr/bin/env bash
# scripts/notify_lib.sh
#
# Shared operator-notify helper for shell senders: Discord primary, Telegram
# best-effort fallback. Sourced by the cron/timer paths that DM the operator
# (daily_status.sh, portfolio_oneliner.sh, healthcheck.sh, listener-healthcheck.sh,
# auto-restart-services.sh). Mirrors the dual-route policy of the Python helper
# src/claude_soma/operator_dm.py and the NEET auto-poster's notify().
#
# Why: Telegram is banned in India (~2026-06) so its sendMessage returns HTTP 000.
# Discord is the primary route; Telegram stays a fallback so delivery auto-resumes
# when the ban lifts.
#
# Public API:
#   soma_notify "message"   -> 0 if any route delivered, 1 if all failed.
#                              Callers run under `set -uo pipefail` (no -e), so a
#                              nonzero return never aborts them.
#
# Tokens are read at call time from the process env or, failing that, from
# /etc/claude-soma/secrets.env (Discord) and ~/.claude/channels/telegram/.env or
# secrets.env (Telegram). They are NEVER echoed to stdout/stderr or any log.
#
# Kill switches:
#   SOMA_NOTIFY_DISCORD_DISABLED=1   -> skip Discord
#   SOMA_NOTIFY_TELEGRAM_DISABLED=1  -> skip Telegram fallback

# Idempotent source guard.
[[ -n "${_SOMA_NOTIFY_LIB_LOADED:-}" ]] && return 0
_SOMA_NOTIFY_LIB_LOADED=1

SOMA_SECRETS_FILE="${SOMA_SECRETS_FILE:-/etc/claude-soma/secrets.env}"
SOMA_DISCORD_API="${SOMA_DISCORD_API:-https://discord.com/api/v10}"
SOMA_DISCORD_DM_CHANNEL_ID="${SOMA_DISCORD_DM_CHANNEL_ID:-1516423259699155045}"
SOMA_TG_ENV_FILE="${SOMA_TG_ENV_FILE:-/home/ubuntu/.claude/channels/telegram/.env}"
SOMA_NOTIFY_TIMEOUT="${SOMA_NOTIFY_TIMEOUT:-15}"

# _soma_read_var FILE NAME -> echoes the last NAME=value (surrounding quotes stripped).
# Reads with plain bash; never sources the file (avoids executing arbitrary lines).
_soma_read_var() {
    local file="$1" name="$2" line val=""
    [[ -r "$file" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
        case "$line" in
            "$name="*) val="${line#*=}" ;;
        esac
    done < "$file"
    val="${val%\"}"; val="${val#\"}"
    val="${val%\'}"; val="${val#\'}"
    printf '%s' "$val"
}

# _soma_discord_send "msg" -> 0 on HTTP 200/201, else 1. Token never echoed.
_soma_discord_send() {
    [[ "${SOMA_NOTIFY_DISCORD_DISABLED:-}" == "1" ]] && return 1
    local msg="$1" token payload http
    token="${DISCORD_BOT_TOKEN:-}"
    [[ -z "$token" ]] && token="$(_soma_read_var "$SOMA_SECRETS_FILE" DISCORD_BOT_TOKEN)"
    [[ -z "$token" ]] && return 1
    payload="$(printf '%s' "$msg" | jq -Rs '{content: .}' 2>/dev/null)"
    [[ -z "$payload" ]] && return 1
    http="$(curl -s -o /dev/null -w '%{http_code}' --max-time "$SOMA_NOTIFY_TIMEOUT" \
        -X POST "${SOMA_DISCORD_API}/channels/${SOMA_DISCORD_DM_CHANNEL_ID}/messages" \
        -H "Authorization: Bot ${token}" \
        -H "Content-Type: application/json" \
        --data "$payload" 2>/dev/null)"
    token=""
    [[ "$http" == "200" || "$http" == "201" ]]
}

# _soma_tg_send "msg" -> 0 on HTTP 200, else 1. Token never echoed.
_soma_tg_send() {
    [[ "${SOMA_NOTIFY_TELEGRAM_DISABLED:-}" == "1" ]] && return 1
    local msg="$1" token chat http
    token="${TELEGRAM_BOT_TOKEN:-}"
    [[ -z "$token" ]] && token="$(_soma_read_var "$SOMA_TG_ENV_FILE" TELEGRAM_BOT_TOKEN)"
    [[ -z "$token" ]] && token="$(_soma_read_var "$SOMA_SECRETS_FILE" TELEGRAM_BOT_TOKEN)"
    [[ -z "$token" ]] && return 1
    chat="${HERMES_NOTIFY_CHAT_ID:-${TELEGRAM_CHAT_ID:-}}"
    [[ -z "$chat" ]] && chat="$(_soma_read_var "$SOMA_SECRETS_FILE" HERMES_NOTIFY_CHAT_ID)"
    [[ -z "$chat" ]] && chat="$(_soma_read_var "$SOMA_SECRETS_FILE" TELEGRAM_CHAT_ID)"
    [[ -z "$chat" ]] && return 1
    http="$(curl -s -o /dev/null -w '%{http_code}' --max-time "$SOMA_NOTIFY_TIMEOUT" \
        -X POST "https://api.telegram.org/bot${token}/sendMessage" \
        --data-urlencode "chat_id=${chat}" \
        --data-urlencode "text=${msg}" 2>/dev/null)"
    token=""
    [[ "$http" == "200" ]]
}

# soma_notify "message" — Discord primary, Telegram best-effort fallback.
# Returns 0 if any route delivered, 1 if all routes failed.
soma_notify() {
    local msg="$1"
    if _soma_discord_send "$msg"; then
        return 0
    fi
    if _soma_tg_send "$msg"; then
        return 0
    fi
    return 1
}
