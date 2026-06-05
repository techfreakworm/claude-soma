#!/usr/bin/env bash
# scripts/engagement-hourly-dispatch.sh — FI-ENGAGEMENT-FRESH-DRIP dispatcher
#
# Hourly entry point for the engagement-drip system. Replaces the direct
# `python3 engagement-hourly-drip.py` ExecStart in
# systemd/claude-soma-engagement-drip.service.
#
# Behavior:
#   1. Health-check the X + LinkedIn playwright sessions (state files
#      present and not aged past 7 days; light smoke check).
#   2. If healthy: spawn a focused, ephemeral browse+draft subagent
#      (`claude -p` with playwright-x + playwright-linkedin + hermes-api
#      MCPs only) under a 12-minute hard ceiling. The subagent writes
#      fresh drafts to queue.jsonl with `freshly_drafted_at`.
#   3. After the subagent exits OR times out, call engagement-hourly-drip.py
#      with the appropriate flag:
#        --source=fresh                              when fresh drafts landed
#        --fallback --fallback-reason "<short>"      otherwise
#   4. Write one structured line per run to
#      /var/log/claude-soma/engagement-dispatch.jsonl for auditing.
#
# Toggling: set HERMES_ENGAGEMENT_FRESH_MODE=off in /etc/claude-soma/secrets.env
# to short-circuit step 2 and go straight to --fallback. Useful when X / LI
# sessions are known-bad and the operator just wants the silent pool drip.
#
# Robustness contract: this script ALWAYS exits 0 (so the systemd timer
# doesn't go into a failure backoff loop) and ALWAYS produces exactly one
# DM via the downstream drip invocation. A silent hour is a contract
# violation; structured log + DM is the unambiguous evidence.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRIP_PY="${REPO_ROOT}/scripts/engagement-hourly-drip.py"
PYTHON="${HERMES_ENGAGEMENT_PYTHON:-${REPO_ROOT}/.venv/bin/python3}"
[[ -x "${PYTHON}" ]] || PYTHON=$(command -v python3)

QUEUE_PATH="${HERMES_ENGAGEMENT_QUEUE:-/var/lib/claude-soma/engagement/queue.jsonl}"
ENGAGEMENT_DIR="$(dirname "${QUEUE_PATH}")"
DISPATCH_LOG="${HERMES_ENGAGEMENT_DISPATCH_LOG:-/var/log/claude-soma/engagement-dispatch.jsonl}"
SUBAGENT_PROMPT="${REPO_ROOT}/scripts/engagement-browse-draft-subagent.txt"
SUBAGENT_MCP="${REPO_ROOT}/config/claude/engagement-subagent-mcp.json"
SUBAGENT_TIMEOUT="${HERMES_ENGAGEMENT_SUBAGENT_TIMEOUT_SECS:-720}"  # 12 min
X_STATE="${HERMES_PW_X_STATE:-/home/ubuntu/.claude-pw/state-x.json}"
LI_STATE="${HERMES_PW_LINKEDIN_STATE:-/home/ubuntu/.claude-pw/state-linkedin.json}"
PW_STATE_MAX_AGE_DAYS="${HERMES_PW_STATE_MAX_AGE_DAYS:-7}"
FRESH_MODE="${HERMES_ENGAGEMENT_FRESH_MODE:-on}"

START_TS=$(date +%s)
START_ISO=$(date -u +%FT%TZ)
mkdir -p "$(dirname "${DISPATCH_LOG}")" 2>/dev/null || true
mkdir -p "${ENGAGEMENT_DIR}" 2>/dev/null || true

_log_dispatch_line() {
    # _log_dispatch_line method result drafts_count latency_ms subagent_exit reason
    local method="$1" result="$2" drafts="$3" latency_ms="$4" exit_code="$5" reason="${6:-}"
    local line
    line=$(printf '{"ts":"%s","start_ts":%s,"method":"%s","result":"%s","drafts_count":%s,"latency_ms":%s,"subagent_exit_code":%s,"reason":%s}\n' \
        "${START_ISO}" "${START_TS}" "${method}" "${result}" "${drafts}" "${latency_ms}" "${exit_code}" \
        "$(printf '%s' "${reason}" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')")
    printf '%s' "${line}" >> "${DISPATCH_LOG}" 2>/dev/null || true
}

_run_drip_fresh() {
    "${PYTHON}" "${DRIP_PY}" --source=fresh --start-ts "${START_TS}"
}

_run_drip_fallback() {
    local reason="$1"
    "${PYTHON}" "${DRIP_PY}" --fallback --fallback-reason "${reason}"
}

_count_fresh_drafts() {
    # Read queue.jsonl and count entries with status=queued AND
    # freshly_drafted_at >= START_TS.
    "${PYTHON}" - "${QUEUE_PATH}" "${START_TS}" <<'PY' 2>/dev/null
import json, sys
path, start_ts = sys.argv[1], float(sys.argv[2])
n = 0
try:
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                e = json.loads(raw)
            except Exception:
                continue
            if e.get("status") != "queued":
                continue
            try:
                if float(e.get("freshly_drafted_at") or 0) >= start_ts:
                    n += 1
            except Exception:
                pass
except FileNotFoundError:
    pass
print(n)
PY
}

_health_check_playwright() {
    # Returns 0 if both state files exist and are fresh enough, non-zero
    # with a reason in $REASON otherwise.
    REASON=""
    if [[ ! -f "${X_STATE}" ]]; then
        REASON="x_state_missing:${X_STATE}"
        return 1
    fi
    if [[ ! -f "${LI_STATE}" ]]; then
        REASON="linkedin_state_missing:${LI_STATE}"
        return 1
    fi
    local now age days
    now=$(date +%s)
    for state_file in "${X_STATE}" "${LI_STATE}"; do
        age=$(( now - $(stat -c %Y "${state_file}") ))
        days=$(( age / 86400 ))
        if (( days > PW_STATE_MAX_AGE_DAYS )); then
            REASON="state_too_old:$(basename "${state_file}"):age_days=${days}"
            return 1
        fi
    done
    return 0
}

# --- Fast paths that skip the subagent entirely -----------------------------

if [[ "${FRESH_MODE}" != "on" ]]; then
    _run_drip_fallback "fresh_mode_off"
    END_TS=$(date +%s)
    _log_dispatch_line "skip" "fallback" "$(_count_fresh_drafts)" \
        $(( (END_TS - START_TS) * 1000 )) 0 "fresh_mode_off"
    exit 0
fi

if [[ ! -r "${SUBAGENT_PROMPT}" ]]; then
    _run_drip_fallback "subagent_prompt_missing"
    END_TS=$(date +%s)
    _log_dispatch_line "skip" "fallback" 0 \
        $(( (END_TS - START_TS) * 1000 )) 0 "subagent_prompt_missing:${SUBAGENT_PROMPT}"
    exit 0
fi

if [[ ! -r "${SUBAGENT_MCP}" ]]; then
    _run_drip_fallback "subagent_mcp_missing"
    END_TS=$(date +%s)
    _log_dispatch_line "skip" "fallback" 0 \
        $(( (END_TS - START_TS) * 1000 )) 0 "subagent_mcp_missing:${SUBAGENT_MCP}"
    exit 0
fi

if ! _health_check_playwright; then
    _run_drip_fallback "${REASON}"
    END_TS=$(date +%s)
    _log_dispatch_line "skip" "fallback" 0 \
        $(( (END_TS - START_TS) * 1000 )) 0 "playwright_unhealthy:${REASON}"
    exit 0
fi

# --- Happy path: spawn the focused browse+draft subagent --------------------

CLAUDE_BIN="${HERMES_CLAUDE_BIN:-/home/ubuntu/.local/bin/claude}"
[[ -x "${CLAUDE_BIN}" ]] || CLAUDE_BIN=$(command -v claude)
if [[ -z "${CLAUDE_BIN}" ]]; then
    _run_drip_fallback "claude_binary_not_found"
    END_TS=$(date +%s)
    _log_dispatch_line "spawn" "fallback" 0 \
        $(( (END_TS - START_TS) * 1000 )) 127 "claude_binary_not_found"
    exit 0
fi

SUBAGENT_STDERR=$(mktemp -t engagement-subagent-XXXXXX.err)
trap 'rm -f "${SUBAGENT_STDERR}"' EXIT

# Reading the prompt fresh each call lets the operator edit the template
# without redeploying the dispatcher.
SUBAGENT_INSTRUCTION=$(cat "${SUBAGENT_PROMPT}")
SUBAGENT_INSTRUCTION="${SUBAGENT_INSTRUCTION//__START_ISO__/${START_ISO}}"
SUBAGENT_INSTRUCTION="${SUBAGENT_INSTRUCTION//__QUEUE_PATH__/${QUEUE_PATH}}"
SUBAGENT_INSTRUCTION="${SUBAGENT_INSTRUCTION//__START_TS__/${START_TS}}"

# `timeout --kill-after` so an exotic subagent hang (claude not responding
# to SIGTERM) eventually dies with SIGKILL after a 30s grace period.
set +e
timeout --kill-after=30 "${SUBAGENT_TIMEOUT}" \
    "${CLAUDE_BIN}" -p \
        --mcp-config "${SUBAGENT_MCP}" \
        --setting-sources project \
        --add-dir "${ENGAGEMENT_DIR}" \
        --output-format text \
        "${SUBAGENT_INSTRUCTION}" \
    >/dev/null 2>"${SUBAGENT_STDERR}"
SUBAGENT_EXIT=$?
set -e

FRESH_COUNT=$(_count_fresh_drafts)
END_TS=$(date +%s)
LATENCY_MS=$(( (END_TS - START_TS) * 1000 ))

if [[ "${SUBAGENT_EXIT}" -eq 124 || "${SUBAGENT_EXIT}" -eq 137 ]]; then
    # 124 = timeout signaled; 137 = timeout + SIGKILL grace.
    _run_drip_fallback "subagent_timeout"
    _log_dispatch_line "spawn" "fallback" "${FRESH_COUNT}" \
        "${LATENCY_MS}" "${SUBAGENT_EXIT}" "timeout:${SUBAGENT_TIMEOUT}s"
    exit 0
fi

if [[ "${SUBAGENT_EXIT}" -ne 0 ]]; then
    SHORT_ERR=$(tail -c 400 "${SUBAGENT_STDERR}" 2>/dev/null | tr '\n' ' ' | head -c 200)
    _run_drip_fallback "subagent_exit_${SUBAGENT_EXIT}"
    _log_dispatch_line "spawn" "fallback" "${FRESH_COUNT}" \
        "${LATENCY_MS}" "${SUBAGENT_EXIT}" "subagent_failed:${SHORT_ERR}"
    exit 0
fi

if [[ "${FRESH_COUNT}" -eq 0 ]]; then
    _run_drip_fallback "subagent_produced_zero"
    _log_dispatch_line "spawn" "fallback" 0 \
        "${LATENCY_MS}" "${SUBAGENT_EXIT}" "zero_fresh_drafts_after_subagent"
    exit 0
fi

# Happy path: fresh drafts landed.
_run_drip_fresh
_log_dispatch_line "spawn" "fresh" "${FRESH_COUNT}" \
    "${LATENCY_MS}" "${SUBAGENT_EXIT}" ""
exit 0
