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
# This is the bot session -- it owns the Telegram poller. Telegram is NO LONGER
# enabled in user scope (that let every other claude session load the plugin and
# hijack the poller; docs/KNOWN_BUGS.md #1, scripts/disable-user-telegram-plugin.sh).
# The bot opts in EXPLICITLY via --settings (verified 2026-05-26 to load the
# plugin even when telegram is absent from user/project/local, and to merge
# additively so the bot keeps its other user-scope settings). Non-bot sessions
# load only the default scopes -- no telegram, and never this --settings file --
# so they no longer steal the poller. Do NOT add --setting-sources here (it would
# change which scopes the bot loads).
#
# --continue resumes the most-recent /opt/claude-soma session on restart instead of
# starting fresh; the channel owns that cwd exclusively, so --continue
# deterministically picks the bot's own session. Covers BOTH the systemd start and
# the healthcheck in-pane respawn (both go through this wrapper).
#
# The telegram plugin is vendored as a git submodule (external/claude-plugins-official,
# our fork of anthropics/claude-plugins-official) and exposed through claude-soma's own
# marketplace (.claude-plugin/marketplace.json) loaded via --plugin-dir below -- hence
# `telegram@claude-soma`, NOT `@claude-plugins-official`. The fork carries our
# reply_to-context patch (quoted-message relay). See docs/telegram-plugin-fork.md.

exec /home/ubuntu/.local/bin/claude \
    --channels plugin:telegram@claude-soma \
    --continue \
    --settings /opt/claude-soma/config/claude/channel-settings.json \
    --plugin-dir /opt/claude-soma \
    --add-dir /home/ubuntu/hermes-work \
    --dangerously-skip-permissions \
    --effort low \
    --append-system-prompt-file /opt/claude-soma/system_prompts/responsive_bot.md
