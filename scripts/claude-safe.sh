#!/usr/bin/env bash
# scripts/claude-safe.sh
#
# Safety wrapper for running `claude` interactively on the Soma VPS.
#
# WHY: the user-scope telegram@claude-plugins-official plugin loads in ANY
# claude session that reads user-scope settings (it is listed in
# ~/.claude/settings.json -> enabledPlugins). On load the plugin SIGTERMs
# whatever PID owns /home/ubuntu/.claude/channels/telegram/bot.pid -- i.e. it
# hijacks the live bot's Telegram poller, knocking the bot deaf and triggering a
# channel restart. See docs/KNOWN_BUGS.md entry #1.
#
# A bare interactive `claude` on the VPS is therefore unsafe. This wrapper makes
# it safe by:
#   1. injecting `--setting-sources project,local`, which skips user-scope
#      enabledPlugins so the telegram plugin never loads, and
#   2. pointing TELEGRAM_STATE_DIR at a throwaway per-invocation dir as
#      belt-and-suspenders: even if some project/local setting enabled a
#      telegram plugin, it would read an empty state dir (no .env, no token) and
#      bail before ever touching the real bot.pid.
#
# It deliberately does NOT touch the bot's own invocation: any command that
# already names a channel (`--channels`) is passed through untouched, because
# the bot MUST keep loading the plugin to poll Telegram. Management subcommands
# and --version/--help are also passed through untouched (they never boot a
# channel poller and may reject --setting-sources).
#
# Install: see scripts/vps_bootstrap.sh (installs to /usr/local/bin/claude-safe
# and shadows interactive `claude` for the ubuntu user). Tests:
# tests/test_claude_safe_wrapper.py.

set -uo pipefail

# Resolve the REAL claude binary. CLAUDE_SAFE_REAL is an explicit override (used
# by the tests); otherwise prefer the native install and never recurse into this
# wrapper.
real_claude="${CLAUDE_SAFE_REAL:-}"
if [ -z "$real_claude" ]; then
    for cand in /home/ubuntu/.local/bin/claude "$HOME/.local/bin/claude"; do
        if [ -x "$cand" ]; then real_claude="$cand"; break; fi
    done
fi
if [ -z "$real_claude" ]; then
    self="$(readlink -f "$0" 2>/dev/null || echo "$0")"
    while IFS= read -r cand; do
        [ "$(readlink -f "$cand" 2>/dev/null || echo "$cand")" = "$self" ] && continue
        real_claude="$cand"; break
    done < <(type -ap claude 2>/dev/null)
fi
if [ -z "$real_claude" ]; then
    echo "claude-safe: could not locate the real claude binary" >&2
    exit 127
fi

# Disable Claude Code prompt suggestions for every claude launched through this
# wrapper: the faint autosuggested input-box text can be mistaken for / accidentally
# submitted as operator input. Applies to both exec paths below; the env var
# overrides the promptSuggestionEnabled setting and is independent of --setting-sources.
export CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=0

# Pass-through cases: an explicit channel consumer (the bot), management
# subcommands, and info flags. These either MUST load the plugin or never boot a
# channel poller.
passthrough=0
case "${1:-}" in
    plugin|plugins|mcp|config|doctor|install|update|setup-token|migrate-installer|--version|-v|--help|-h)
        passthrough=1 ;;
esac
for a in "$@"; do
    [ "$a" = "--channels" ] && passthrough=1
done

if [ "$passthrough" -eq 1 ]; then
    exec "$real_claude" "$@"
fi

# Respect a caller-supplied --setting-sources; otherwise inject the plugin-skip.
inject_sources=1
for a in "$@"; do
    case "$a" in
        --setting-sources|--setting-sources=*) inject_sources=0 ;;
    esac
done

# Private, throwaway telegram state dir for this invocation (belt-and-suspenders;
# cleaned up on exit). Keeps any stray plugin load away from the real bot.pid.
tg_state="$(mktemp -d "${TMPDIR:-/tmp}/claude-safe-tg.XXXXXX")"
export TELEGRAM_STATE_DIR="$tg_state"

argv=()
[ "$inject_sources" -eq 1 ] && argv+=(--setting-sources project,local)
argv+=("$@")

"$real_claude" "${argv[@]}"
rc=$?
rm -rf "$tg_state"
exit "$rc"
