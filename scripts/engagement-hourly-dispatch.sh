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
    # NOTE: `$()` strips trailing newlines from captured output, so emit the
    # final \n with the redirect's printf (not the captured one) — that's why
    # the dispatch log was previously one long unbroken concatenation.
    local method="$1" result="$2" drafts="$3" latency_ms="$4" exit_code="$5" reason="${6:-}"
    local reason_json
    reason_json=$(printf '%s' "${reason}" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')
    printf '{"ts":"%s","start_ts":%s,"method":"%s","result":"%s","drafts_count":%s,"latency_ms":%s,"subagent_exit_code":%s,"reason":%s}\n' \
        "${START_ISO}" "${START_TS}" "${method}" "${result}" "${drafts}" "${latency_ms}" "${exit_code}" "${reason_json}" \
        >> "${DISPATCH_LOG}" 2>/dev/null || true
}

# FI-DRIP-IST-WINDOW (2026-06-06): 12 fires/day at IST 10..21, single
# draft/run. Dispatcher picks the platform by IST hour parity:
#   - odd IST hour  → X-pop  (run subagent → drip --single-platform=x)
#   - even IST hour → LI-pop (skip subagent → drip --single-platform=linkedin)
# Daily total: 6 X + 6 LinkedIn. LI pool refill cadence drops from 12/day
# to 6/day (only on X-pop hours via the subagent's send_to_project), which
# is plenty since each refill brings 3-5 drafts and pool-depth-check
# self-throttles when the LI pool exceeds 6.
IST_HOUR=$(TZ=Asia/Kolkata date +%H)
# strip leading zero so 09 → 9 (arithmetic in bash treats 09 as octal-invalid).
IST_HOUR=$((10#${IST_HOUR}))
if (( IST_HOUR % 2 == 1 )); then
    PLATFORM_THIS_HOUR=x
else
    PLATFORM_THIS_HOUR=linkedin
fi

_run_drip_single_x_fresh() {
    "${PYTHON}" "${DRIP_PY}" --single-platform=x --source=fresh \
        --start-ts "${START_TS}"
}

_run_drip_single_x_pool() {
    "${PYTHON}" "${DRIP_PY}" --single-platform=x --source=any
}

_run_drip_single_li_pool() {
    "${PYTHON}" "${DRIP_PY}" --single-platform=linkedin --source=any
}

_run_drip_fresh() {
    # Back-compat shim. Pre-FI-DRIP-IST-WINDOW callers invoked this for
    # the "happy path after subagent succeeds" branch. The new flow uses
    # _run_drip_single_x_fresh directly; keep this as an alias so any
    # operator who already invokes the old name still gets a sensible
    # behavior.
    _run_drip_single_x_fresh
}

_run_drip_fallback() {
    local reason="$1"
    # FI-DRIP-IST-WINDOW: fall back to the pool of the platform we were
    # supposed to surface this hour, not the legacy --fallback that mixed
    # both platforms. Same platform, just pool instead of fresh.
    if [[ "${PLATFORM_THIS_HOUR}" == "x" ]]; then
        _run_drip_single_x_pool
    else
        _run_drip_single_li_pool
    fi
}

_purge_null_permalink_drafts() {
    # FI-FRESH-DRIP (2026-06-05): the subagent has occasionally appended
    # fresh drafts with a null/empty source_permalink — those are
    # un-postable (engagement-post-x.js / engagement-post-li.js need a
    # permalink) so they should never reach pending_review. Delegate to
    # the locked CLI flag so the purge runs under queue_locked() and is
    # safe against concurrent writers (FI-QUEUE-DEDUP-LOCK).
    "${PYTHON}" "${DRIP_PY}" --purge-null-permalink-fresh "${START_TS}" 2>/dev/null || true
}

_dedup_fresh_drafts() {
    # FI-TARGET-DEDUP-LEDGER (Bundle 2, #93): deterministic post-pass that
    # drops fresh drafts targeting a post already in the queue or ledger.
    # Runs after _purge_null_permalink_drafts, before _count_fresh_drafts.
    "${PYTHON}" "${DRIP_PY}" --dedup-fresh-against-targets "${START_TS}" 2>/dev/null || true
}

_count_fresh_drafts() {
    # Read queue.jsonl and count entries with status=queued AND
    # freshly_drafted_at >= START_TS AND a usable source_permalink (so
    # un-postable drafts can't masquerade as fresh).
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
                if float(e.get("freshly_drafted_at") or 0) < start_ts:
                    continue
            except Exception:
                continue
            sp = e.get("source_permalink")
            if not (isinstance(sp, str) and sp.strip()):
                continue
            n += 1
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

# FI-DRIP-IST-WINDOW (2026-06-06): on LinkedIn-pop hours the X subagent
# does not run at all — we simply pop one LI from the pool and exit.
# Social-manager keeps the pool warm via send_to_project asks issued on
# X-pop hours (the other half of the schedule).
if [[ "${PLATFORM_THIS_HOUR}" == "linkedin" ]]; then
    _run_drip_single_li_pool
    END_TS=$(date +%s)
    _log_dispatch_line "skip" "li_pool" 0 \
        $(( (END_TS - START_TS) * 1000 )) 0 "ist_hour=${IST_HOUR}:linkedin_pop"
    exit 0
fi

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

# Persist the last subagent stderr to a stable path for forensics
# (FI-ENGAGEMENT-FRESH-DRIP follow-up #50). Each run overwrites the
# previous file so disk pressure stays flat; logrotate-style retention
# of N past runs is a future improvement. Also keep stdout of the
# subagent (the prompt's final status line + any unrelated noise) so
# the operator can confirm what the subagent actually printed.
STDERR_DIR="${HERMES_ENGAGEMENT_SUBAGENT_LOG_DIR:-/var/log/claude-soma}"
mkdir -p "${STDERR_DIR}" 2>/dev/null || true
SUBAGENT_STDERR="${STDERR_DIR}/engagement-subagent-last.err"
SUBAGENT_STDOUT="${STDERR_DIR}/engagement-subagent-last.out"
# Truncate at the start of each run so the file describes ONLY this run.
: > "${SUBAGENT_STDERR}" 2>/dev/null || true
: > "${SUBAGENT_STDOUT}" 2>/dev/null || true

# Reading the prompt fresh each call lets the operator edit the template
# without redeploying the dispatcher.
SUBAGENT_INSTRUCTION=$(cat "${SUBAGENT_PROMPT}")
SUBAGENT_INSTRUCTION="${SUBAGENT_INSTRUCTION//__START_ISO__/${START_ISO}}"
SUBAGENT_INSTRUCTION="${SUBAGENT_INSTRUCTION//__QUEUE_PATH__/${QUEUE_PATH}}"
SUBAGENT_INSTRUCTION="${SUBAGENT_INSTRUCTION//__START_TS__/${START_TS}}"

# `timeout --kill-after` so an exotic subagent hang (claude not responding
# to SIGTERM) eventually dies with SIGKILL after a 30s grace period.
# --permission-mode bypassPermissions: in `claude -p` headless mode, every
# Bash tool call defaults to "ask for approval" — but there is no human on
# the other end of a dispatched subagent, so node/openssl/printf all hang as
# permission-denied. Bypass that here so the subagent can actually run the
# harvest helpers + queue-append it was instructed to. SAFETY: the
# orchestrator_gate.sh PreToolUse hook still fires for every Bash call
# (the gate blocks claude/codex/pip/apt/network curl etc.); the MCP set is
# tight (only hermes-api); --add-dir scopes file access to
# /var/lib/claude-soma/engagement; the prompt is sealed (don't post, don't
# touch arbitrary FS). Bypass is bounded by those layers.
set +e
timeout --kill-after=30 "${SUBAGENT_TIMEOUT}" \
    "${CLAUDE_BIN}" -p \
        --mcp-config "${SUBAGENT_MCP}" \
        --setting-sources project \
        --add-dir "${ENGAGEMENT_DIR}" \
        --permission-mode bypassPermissions \
        --output-format text \
        "${SUBAGENT_INSTRUCTION}" \
    >"${SUBAGENT_STDOUT}" 2>"${SUBAGENT_STDERR}"
SUBAGENT_EXIT=$?
set -e

# Purge any null-permalink fresh drafts the subagent produced before
# the count drives the decision tree. This is defense in depth — the
# subagent prompt also tells it not to write null-permalink rows.
_purge_null_permalink_drafts
# FI-TARGET-DEDUP-LEDGER (Bundle 2, #93): drop fresh drafts that
# duplicate a target already in the queue or posted-target ledger.
_dedup_fresh_drafts
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
    # SHORT_ERR is just the tail head — the full stderr is in
    # ${SUBAGENT_STDERR} for the operator to inspect.
    SHORT_ERR=$(tail -c 400 "${SUBAGENT_STDERR}" 2>/dev/null | tr '\n' ' ' | head -c 200)
    _run_drip_fallback "subagent_exit_${SUBAGENT_EXIT}"
    _log_dispatch_line "spawn" "fallback" "${FRESH_COUNT}" \
        "${LATENCY_MS}" "${SUBAGENT_EXIT}" "subagent_failed:${SHORT_ERR}|stderr_log:${SUBAGENT_STDERR}"
    exit 0
fi

if [[ "${FRESH_COUNT}" -eq 0 ]]; then
    _run_drip_fallback "subagent_produced_zero"
    _log_dispatch_line "spawn" "fallback" 0 \
        "${LATENCY_MS}" "${SUBAGENT_EXIT}" "zero_fresh_drafts_after_subagent|stdout_log:${SUBAGENT_STDOUT}|stderr_log:${SUBAGENT_STDERR}"
    exit 0
fi

# Happy path: fresh drafts landed.
_run_drip_fresh
_log_dispatch_line "spawn" "fresh" "${FRESH_COUNT}" \
    "${LATENCY_MS}" "${SUBAGENT_EXIT}" ""
exit 0
