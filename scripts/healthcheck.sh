#!/usr/bin/env bash
# scripts/healthcheck.sh
#
# Verify channel session, api, and frontend are responsive. Restart channel
# if it's not. Also scans for Playwright NEEDS_REAUTH-<platform> sentinels
# and DMs the user once per platform per day if one is found.
# Logs to /var/log/claude-soma/healthcheck.log.
#
# SELF-TEST (run as root or ubuntu with sudo):
#   mkdir -p ~/.claude-pw
#   touch ~/.claude-pw/NEEDS_REAUTH-linkedin
#   sudo /opt/claude-soma/scripts/healthcheck.sh
#   tail /var/log/claude-soma/healthcheck.log
#   # expect a line like: [ts] reauth ping linkedin -> http 200
#   # (or "[ts] reauth: SKIP ..." if token env file is missing/empty)
#   rm ~/.claude-pw/NEEDS_REAUTH-linkedin
#   rm -f /home/ubuntu/.claude-soma/needs_reauth_pinged.txt

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

# 4. Channel's Telegram-MCP bun child is alive
# The telegram plugin runs as `bun server.ts` spawned by `claude --channels`
# over stdio. bun has an internal watchdog (server.ts:668-679) that
# self-exits within ~5s if claude's stdin pipe closes or its parent PID
# changes. Claude does not auto-respawn dead stdio MCP children, so the
# bot keeps running but is silently Telegram-mute. Detect that and bounce
# the channel service.
#
# Bot PID = the `claude --channels` process owned by ubuntu (NOT the tmux
# wrapper, which also matches `pgrep -f` because the command appears in
# its argv). Filter by `comm=claude` to disambiguate.
BOT_PID=""
for pid in $(sudo -u ubuntu pgrep -fu ubuntu -- 'claude --channels' 2>/dev/null); do
    if [ "$(ps -o comm= -p "$pid" 2>/dev/null)" = "claude" ]; then
        BOT_PID="$pid"
        break
    fi
done

if [ -n "$BOT_PID" ]; then
    # Grace period: don't race bun's own startup right after a healthy
    # restart (claude takes ~10-20s to come up, then spawns bun). Without
    # this we'd restart-loop every 10 min.
    ETIMES=$(ps -o etimes= -p "$BOT_PID" 2>/dev/null | tr -d ' ')
    if [ -n "$ETIMES" ] && [ "$ETIMES" -ge 604800 ]; then
        echo "[$TS] channel: WARNING bot pid=$BOT_PID up ${ETIMES}s (>7 days) — schedule channel-clear to trim transcript" >> "$LOG"
    fi
    if [ -n "$ETIMES" ] && [ "$ETIMES" -ge 60 ]; then
        # Walk descendant tree of BOT_PID via pgrep -P recursion.
        descendants() {
            local parent=$1
            local kids
            kids=$(pgrep -P "$parent" 2>/dev/null)
            for k in $kids; do
                echo "$k"
                descendants "$k"
            done
        }
        BUN_FOUND=0
        for pid in $(descendants "$BOT_PID"); do
            comm=$(ps -o comm= -p "$pid" 2>/dev/null)
            if [ "$comm" = "bun" ]; then
                args=$(ps -o args= -p "$pid" 2>/dev/null)
                case "$args" in
                    *server.ts*) BUN_FOUND=1; break ;;
                esac
            fi
        done
        if [ "$BUN_FOUND" -eq 0 ]; then
            HOLDER=$(cat /home/ubuntu/.claude/channels/telegram/bot.pid 2>/dev/null || echo none)
            echo "[$TS] channel: bun MCP missing (bot pid=$BOT_PID up ${ETIMES}s, bot.pid holder=$HOLDER)" >> "$LOG"
            # Non-destructive recovery FIRST: respawn claude in its existing tmux
            # pane. Unlike `systemctl restart` (which re-runs ExecStartPre/Stop and
            # tears down the whole tmux server, killing anything sharing it -- a
            # project lead spawned the old way, an attached operator), this only
            # replaces the bot's claude process. Same argv as the service via the
            # single-source scripts/channel-claude.sh. Re-tap the log afterwards
            # (respawn-pane drops the pipe-pane), then fall back to a full restart
            # only if the poller does not come back.
            if sudo -u ubuntu tmux respawn-pane -k -t hermes:0 \
                    /opt/claude-soma/scripts/channel-claude.sh 2>>"$LOG"; then
                sudo -u ubuntu tmux pipe-pane -t hermes:0 -o \
                    "cat >> /var/log/claude-soma/channel.log" 2>>"$LOG" || true
                echo "[$TS] channel: respawned claude in-pane, verifying poller returns" >> "$LOG"
                RECOVERED=0
                for _ in $(seq 1 12); do   # up to ~60s for a fresh claude+bun
                    sleep 5
                    NEWBOT=""
                    for pid in $(sudo -u ubuntu pgrep -fu ubuntu -- 'claude --channels' 2>/dev/null); do
                        if [ "$(ps -o comm= -p "$pid" 2>/dev/null)" = "claude" ]; then
                            NEWBOT="$pid"; break
                        fi
                    done
                    [ -z "$NEWBOT" ] && continue
                    for pid in $(descendants "$NEWBOT"); do
                        if [ "$(ps -o comm= -p "$pid" 2>/dev/null)" = "bun" ]; then
                            case "$(ps -o args= -p "$pid" 2>/dev/null)" in
                                *server.ts*) RECOVERED=1; break ;;
                            esac
                        fi
                    done
                    [ "$RECOVERED" -eq 1 ] && break
                done
                if [ "$RECOVERED" -eq 1 ]; then
                    echo "[$TS] channel: in-pane respawn recovered the poller (no service restart)" >> "$LOG"
                else
                    echo "[$TS] channel: in-pane respawn did not restore poller, restarting service" >> "$LOG"
                    sudo systemctl restart claude-soma-channel.service
                fi
            else
                echo "[$TS] channel: respawn-pane failed, restarting service" >> "$LOG"
                sudo systemctl restart claude-soma-channel.service
            fi
        fi
    fi
fi

# 5. NEEDS_REAUTH sentinel scan — DM the user once per platform per day when
#    pw-refresh.js drops a ~/.claude-pw/NEEDS_REAUTH-<platform> file.
#    Uses the Telegram Bot API directly (same pattern as portfolio_oneliner.sh).
#    Soft-fail: any error in this section logs and continues; it MUST NOT abort
#    the rest of the healthcheck.
(
    CLAUDE_PW_DIR="${CLAUDE_PW_DIR:-/home/ubuntu/.claude-pw}"
    TG_ENV_FILE="${TG_ENV_FILE:-/home/ubuntu/.claude/channels/telegram/.env}"
    CHAT_ID="${TELEGRAM_CHAT_ID:-935376085}"
    PINGED_FILE="/home/ubuntu/.claude-soma/needs_reauth_pinged.txt"
    TODAY="$(date -u +%Y%m%d)"

    # Ensure the state-dir exists (healthcheck runs as root; create with ubuntu ownership).
    install -d -m 755 -o ubuntu -g ubuntu /home/ubuntu/.claude-soma 2>/dev/null || true

    # Bail out softly if the sentinel dir doesn't exist.
    if [ ! -d "$CLAUDE_PW_DIR" ]; then
        exit 0
    fi

    # Load Telegram token — soft-fail if absent or empty.
    TELEGRAM_BOT_TOKEN=""
    if [ -r "$TG_ENV_FILE" ]; then
        # shellcheck source=/dev/null
        source "$TG_ENV_FILE" 2>/dev/null || true
    fi
    if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
        # Log once per sentinel found, then skip all pings.
        for sentinel in "${CLAUDE_PW_DIR}"/NEEDS_REAUTH-*; do
            [ -e "$sentinel" ] || continue
            plat="${sentinel##*NEEDS_REAUTH-}"
            echo "[$TS] reauth: SKIP ping for $plat — TELEGRAM_BOT_TOKEN empty (populate $TG_ENV_FILE)" >> "$LOG"
        done
        exit 0
    fi

    API_URL="https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage"

    for sentinel in "${CLAUDE_PW_DIR}"/NEEDS_REAUTH-*; do
        [ -e "$sentinel" ] || continue    # handle empty glob
        plat="${sentinel##*NEEDS_REAUTH-}"
        key="${plat}-${TODAY}"

        # Dedupe: skip if we already pinged this platform today.
        if grep -qxF "$key" "$PINGED_FILE" 2>/dev/null; then
            continue
        fi

        MSG="Playwright needs re-auth: ${plat}. VNC in and run scripts/pw-login.js."
        RESP_FILE="/tmp/needs_reauth_${plat}.resp"
        HTTP=$(curl -s -o "$RESP_FILE" -w "%{http_code}" \
            --max-time 15 \
            -X POST "$API_URL" \
            -d "chat_id=${CHAT_ID}" \
            --data-urlencode "text=${MSG}" 2>>"$LOG" || echo "000")

        if [ "$HTTP" = "200" ]; then
            echo "$key" >> "$PINGED_FILE"
            echo "[$TS] reauth ping ${plat} -> http ${HTTP}" >> "$LOG"
        else
            RESP_BODY="$(head -c 200 "$RESP_FILE" 2>/dev/null || echo "<no response>")"
            echo "[$TS] reauth ping ${plat} FAILED http=${HTTP} resp=${RESP_BODY}" >> "$LOG"
        fi
        rm -f "$RESP_FILE"
        # Do NOT remove the sentinel — only pw-login.js should clear it.
    done
) || true

echo "[$TS] healthcheck: ok" >> "$LOG"
