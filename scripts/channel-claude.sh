#!/usr/bin/env bash
# scripts/channel-claude.sh
#
# Single source of truth for the persistent messaging-channel `claude` command.
#
# Used by BOTH:
#   - systemd/claude-soma-channel.service (ExecStart, run inside the `hermes`
#     tmux session), and
#   - scripts/healthcheck.sh, which re-runs it via `tmux respawn-pane` to recover
#     a hijacked/dead poller WITHOUT a `systemctl restart` (which would tear down
#     the whole tmux server -- and anything sharing it).
#
# Keep the argv here, not inlined in two places, so the two callers can never
# drift. This script must be exec'd inside a tmux pane (claude needs a real PTY;
# it drops to --print mode on a pipe).
#
# ONE channel is active at a time, chosen by SOMA_ACTIVE_CHANNEL (telegram or
# discord), read from /etc/claude-soma/secrets.env (the service loads it via
# EnvironmentFile, so it reaches this script through the tmux pane env). The
# install script (bootstrap.sh) sets it from the operator's choice; switch
# channels later by editing SOMA_ACTIVE_CHANNEL in secrets.env and restarting
# claude-soma-channel.service. Default is telegram for backward-compatibility.
#
#   telegram -> plugin:telegram@claude-plugins-official (upstream marketplace;
#               the reply-to fork is parked, see docs/telegram-plugin-fork.md)
#   discord  -> plugin:discord@claude-soma (the discord plugin vendored in the
#               external/claude-plugins-official submodule, surfaced through
#               claude-soma's own marketplace via --plugin-dir below -- no
#               runtime `/plugin install` needed)
#
# Each channel has its own --settings file enabling ONLY that channel's plugin,
# so the other channel's plugin never boots (a stray enabled plugin would poll
# its platform and, for telegram, hijack the poller; docs/KNOWN_BUGS.md #1).
# Telegram is NOT enabled in user scope; the bot opts in EXPLICITLY via
# --settings. Do NOT add --setting-sources here (it would change which scopes
# the bot loads).
#
# --continue resumes the most-recent /opt/claude-soma session on restart instead
# of starting fresh; the channel owns that cwd exclusively, so --continue
# deterministically picks the bot's own session. Covers BOTH the systemd start
# and the healthcheck in-pane respawn (both go through this wrapper).

set -euo pipefail

SOMA_ROOT="${SOMA_ROOT:-/opt/claude-soma}"
SECRETS_FILE="${SECRETS_FILE:-/etc/claude-soma/secrets.env}"

# Resolve the active channel. Prefer the env (the systemd service exports it via
# EnvironmentFile=), but fall back to reading secrets.env directly: a healthcheck
# `tmux respawn-pane` recovery may not inherit the service env, and we must NOT
# silently default to the wrong channel (that would launch a dead/banned channel).
CHANNEL="${SOMA_ACTIVE_CHANNEL:-}"
if [[ -z "$CHANNEL" && -r "$SECRETS_FILE" ]]; then
    CHANNEL="$(grep -E '^SOMA_ACTIVE_CHANNEL=' "$SECRETS_FILE" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '[:space:]')"
fi
CHANNEL="${CHANNEL:-telegram}"

case "$CHANNEL" in
    telegram)
        CHANNEL_PLUGIN="plugin:telegram@claude-plugins-official"
        SETTINGS="${SOMA_ROOT}/config/claude/channel-settings.telegram.json"
        ;;
    discord)
        CHANNEL_PLUGIN="plugin:discord@claude-soma"
        SETTINGS="${SOMA_ROOT}/config/claude/channel-settings.discord.json"
        ;;
    *)
        echo "channel-claude.sh: unknown SOMA_ACTIVE_CHANNEL='${CHANNEL}'" >&2
        echo "  set SOMA_ACTIVE_CHANNEL=telegram or =discord in /etc/claude-soma/secrets.env" >&2
        exit 64
        ;;
esac

if [[ "${1:-}" == "--print-channel" ]]; then
    # Test/inspection hook: report the resolved channel + settings without launching.
    echo "channel=${CHANNEL} plugin=${CHANNEL_PLUGIN} settings=${SETTINGS}"
    exit 0
fi

exec /home/ubuntu/.local/bin/claude \
    --channels "${CHANNEL_PLUGIN}" \
    --continue \
    --settings "${SETTINGS}" \
    --plugin-dir "${SOMA_ROOT}" \
    --add-dir /home/ubuntu/hermes-work \
    --dangerously-skip-permissions \
    --effort low \
    --append-system-prompt-file "${SOMA_ROOT}/system_prompts/responsive_bot.md"
