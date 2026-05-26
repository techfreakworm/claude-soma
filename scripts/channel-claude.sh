#!/usr/bin/env bash
# scripts/channel-claude.sh
#
# Single source of truth for the persistent Telegram channel `claude` command.
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
# This is the bot session -- it INTENTIONALLY loads the user-scope telegram
# plugin (via --channels) so it owns the Telegram poller. Do NOT add
# --setting-sources here; that is for non-bot sessions only (see
# scripts/claude-safe.sh and docs/KNOWN_BUGS.md #1).

exec /home/ubuntu/.local/bin/claude \
    --channels plugin:telegram@claude-plugins-official \
    --plugin-dir /opt/claude-soma \
    --add-dir /home/ubuntu/hermes-work \
    --dangerously-skip-permissions \
    --effort max \
    --append-system-prompt-file /opt/claude-soma/system_prompts/responsive_bot.md
