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

echo "[$TS] healthcheck: ok" >> "$LOG"
