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
    Edit|Write|NotebookEdit)
        deny "File edits are substantive work${REASON_TAIL}"
        ;;
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

    # Package installs
    if echo "$CMD" | grep -qE '\b(apt|apt-get)\s+(install|update|upgrade)\b|\bpip[3]?\s+install\b|\bpipx\s+install\b|\bnpm\s+(install|i)\b|\bpnpm\s+(install|add)\b|\byarn\s+(add|install)\b|\bcargo\s+(build|install|test)\b|\bbun\s+install\b'; then
        deny "Package install in Bash${REASON_TAIL}"
    fi
    # Network git
    if echo "$CMD" | grep -qE '\bgit\s+(clone|pull|push)\b|\bgit\s+fetch.*--depth'; then
        deny "Network git op in Bash${REASON_TAIL}"
    fi
    # Builds / tests
    if echo "$CMD" | grep -qE '\bdocker\s+(build|run)\b|\bmake\s|\bcmake\s|\bpytest\b|\bnpm\s+test\b|\bpnpm\s+test\b'; then
        deny "Build/test command in Bash${REASON_TAIL}"
    fi
    # Codex / heavy compute
    if echo "$CMD" | grep -qE '\bcodex\b|\bffmpeg\b.*\s-i\s|\bwhisper-cli\b.*\s-f\s'; then
        deny "Heavy compute in Bash${REASON_TAIL}"
    fi
    # Network curl/wget (allow localhost / 127.0.0.1 / 0.0.0.0)
    if echo "$CMD" | grep -qE '\b(curl|wget)\b'; then
        if ! echo "$CMD" | grep -qE '\b(curl|wget)\b[^|;&]*\b(localhost|127\.0\.0\.1|0\.0\.0\.0)'; then
            deny "Network curl/wget in Bash${REASON_TAIL}"
        fi
    fi
fi

# No decision -> allow
exit 0
