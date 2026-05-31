#!/usr/bin/env bash
# scripts/orchestrator_gate.sh
#
# PreToolUse hook for the channel-claude (bot) session. Blocks tool calls that
# look like inline "substantive work" so the bot dispatches them via Agent
# instead. Loads only in channel-claude (plugin-scoped via --plugin-dir);
# leads use --mcp-config and do not load plugin hooks.
# See docs/notes/2026-05-29-orchestrator-gates.md.
#
# Contract: read PreToolUse event JSON from stdin. For BLOCKED tools, emit
# exit 0 + hookSpecificOutput.permissionDecision="deny" JSON. For everything
# else, exit 0 with no output (no decision -> allow through normal flow).
#
# Fail-open: if jq is missing or input is malformed, exit 0 with no output.
# Bypass: SOMA_ORCHESTRATOR_GATE_DISABLED=1 short-circuits to allow.

set -uo pipefail

# Emergency bypass
if [[ "${SOMA_ORCHESTRATOR_GATE_DISABLED:-0}" == "1" ]]; then
    exit 0
fi

# Fail-open on missing jq
command -v jq >/dev/null 2>&1 || exit 0

EVENT="$(cat)"
TOOL="$(jq -r '.tool_name // ""' <<<"$EVENT" 2>/dev/null)"
[[ -z "$TOOL" ]] && exit 0

deny() {
    local reason="$1"
    jq -n --arg r "$reason" '{
        hookSpecificOutput: {
            hookEventName: "PreToolUse",
            permissionDecision: "deny",
            permissionDecisionReason: $r
        }
    }'
    exit 0
}

REASON_TAIL=" — dispatch via Agent (model=opus, run_in_background=true) instead. See responsive_bot.md."

# ===== Tool-name-level denies =====
case "$TOOL" in
    Edit|NotebookEdit)
        deny "File edits are substantive work${REASON_TAIL}"
        ;;
    Write)
        FPATH="$(jq -r '.tool_input.file_path // ""' <<<"$EVENT" 2>/dev/null)"
        if [[ -z "$FPATH" ]] || [[ "$FPATH" == /opt/claude-soma/* || "$FPATH" == /etc/* || "$FPATH" == /var/lib/* ]]; then
            deny "File edits are substantive work${REASON_TAIL}"
        fi
        ;;  # non-production Write (e.g. /tmp/*) falls through to allow
    WebFetch|WebSearch)
        deny "Network + multi-step thinking${REASON_TAIL}"
        ;;
    Skill)
        deny "Skill invocations run inline and block the channel${REASON_TAIL}"
        ;;
    mcp__huggingface__gr1_z_image_turbo_generate|mcp__huggingface__dynamic_space)
        deny "${TOOL} is slow/multi-step${REASON_TAIL}"
        ;;
    mcp__playwright*|mcp__claude_ai_*)
        deny "${TOOL} is slow/multi-step${REASON_TAIL}"
        ;;
esac

# ===== Bash-pattern denies =====
if [[ "$TOOL" == "Bash" ]]; then
    CMD="$(jq -r '.tool_input.command // ""' <<<"$EVENT" 2>/dev/null)"
    [[ -z "$CMD" ]] && exit 0

    # Strip heredoc body: only inspect the command tokens before the first <<
    # so that heredoc bodies describing network tools are not mistaken for shell-outs.
    CMD_HEAD="${CMD%%<<*}"

    # Extract the first command token to avoid matching substrings in argv or paths.
    # Steps: take first pipeline/chain segment → strip leading whitespace →
    # strip optional "sudo " prefix → strip leading VAR=val assignments →
    # take first whitespace-delimited word → basename (strip directory prefix).
    _seg="${CMD_HEAD%%[|;&(]*}"
    _seg="${_seg#"${_seg%%[![:space:]]*}"}"
    [[ "$_seg" == sudo\ * || "$_seg" == sudo$'\t'* ]] && _seg="${_seg#sudo }"
    while [[ -n "${_seg%% *}" && "${_seg%% *}" == *=* ]]; do _seg="${_seg#* }"; done
    FIRST_CMD="${_seg%% *}"
    FIRST_CMD="${FIRST_CMD##*/}"

    # Deny rules keyed on FIRST_CMD so that a path or grep argument containing
    # a blocked word (e.g. /home/user/codex-output/) never triggers a false deny.
    case "$FIRST_CMD" in
        apt|apt-get)
            echo "$CMD_HEAD" | grep -qE '\b(install|update|upgrade)\b' \
                && deny "Package install in Bash${REASON_TAIL}" ;;
        pip|pip3|pipx)
            echo "$CMD_HEAD" | grep -qE '\binstall\b' \
                && deny "Package install in Bash${REASON_TAIL}" ;;
        npm)
            echo "$CMD_HEAD" | grep -qE '\b(install|i|test)\b' \
                && deny "Package install/test in Bash${REASON_TAIL}" ;;
        pnpm)
            echo "$CMD_HEAD" | grep -qE '\b(install|add|test)\b' \
                && deny "Package install/test in Bash${REASON_TAIL}" ;;
        yarn)
            echo "$CMD_HEAD" | grep -qE '\b(add|install)\b' \
                && deny "Package install in Bash${REASON_TAIL}" ;;
        cargo)
            echo "$CMD_HEAD" | grep -qE '\b(build|install|test)\b' \
                && deny "Build/install/test in Bash${REASON_TAIL}" ;;
        bun)
            echo "$CMD_HEAD" | grep -qE '\binstall\b' \
                && deny "Package install in Bash${REASON_TAIL}" ;;
        git)
            echo "$CMD_HEAD" | grep -qE '\b(clone|pull|push)\b|\bfetch\b.*--depth' \
                && deny "Network git op in Bash${REASON_TAIL}" ;;
        docker)
            echo "$CMD_HEAD" | grep -qE '\b(build|run)\b' \
                && deny "Build command in Bash${REASON_TAIL}" ;;
        make|cmake)
            deny "Build command in Bash${REASON_TAIL}" ;;
        pytest)
            deny "Test command in Bash${REASON_TAIL}" ;;
        codex)
            deny "Heavy compute in Bash${REASON_TAIL}" ;;
        ffmpeg)
            echo "$CMD_HEAD" | grep -qE '\s-i\s' \
                && deny "Heavy compute in Bash${REASON_TAIL}" ;;
        whisper-cli)
            echo "$CMD_HEAD" | grep -qE '\s-f\s' \
                && deny "Heavy compute in Bash${REASON_TAIL}" ;;
        curl|wget)
            # Allow calls to localhost / loopback; deny all other network targets.
            echo "$CMD_HEAD" | grep -qE '\b(localhost|127\.0\.0\.1|0\.0\.0\.0)\b' \
                || deny "Network curl/wget in Bash${REASON_TAIL}" ;;
    esac
fi

# No decision -> allow
exit 0
