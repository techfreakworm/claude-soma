#!/usr/bin/env bash
# scripts/engagement-post-now.sh — operator-approved single-draft post wrapper.
#
# This is the ONLY path the channel bot (or any operator-driven helper)
# should use to actually post an engagement draft. It exists to close the
# stale-review-doc loop:
#
#   Before this wrapper, the flow was:
#     1. operator types "approve <id>" in Telegram
#     2. bot runs engagement-approve.sh <id>     (status: pending → approved)
#     3. operator/bot manually runs engagement-post-{x,linkedin}.js
#     4. post helper returns RESULT:POSTED ... url=<live-permalink>
#     5. *** step missing: nobody called engagement-posted.sh <id> <url> ***
#     6. status stays "approved" forever
#     7. review doc keeps showing the draft under "Approved · awaiting post"
#        even though the comment is live on X/LinkedIn (live witness:
#        2026-06-05 19:00 IST batch — 6 entries stuck approved-but-posted).
#
#   This wrapper does steps 3+5 atomically: post + mark-posted + regen +
#   relay-republish, so the review doc stays in sync with reality and the
#   "Approved · awaiting post" section never grows stale.
#
# USAGE (the operator passes --i-have-user-approval — see
# FI-NO-POST-WITHOUT-APPROVAL: the channel bot MUST NOT set this itself):
#
#   engagement-post-now.sh <draft-id> --i-have-user-approval
#
# ENV (all optional; defaults match the dispatcher):
#   HERMES_ENGAGEMENT_QUEUE     /var/lib/claude-soma/engagement/queue.jsonl
#   HERMES_ENGAGEMENT_LOG       /var/log/claude-soma/engagement-drip.log
#   HERMES_POST_APPROVAL        bypass for --i-have-user-approval flag
#
# EXIT CODES
#   0  posted + status updated
#   2  approval flag missing
#   3  draft id not found in queue
#   4  post helper returned RESULT:UNVERIFIED or RESULT:ERROR
#                          (draft is marked failed, doc regen ran)
#   5  post helper returned RESULT:NEEDS_REAUTH
#   6  post helper returned RESULT:UNREACHABLE

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUEUE="${HERMES_ENGAGEMENT_QUEUE:-/var/lib/claude-soma/engagement/queue.jsonl}"
LOG="${HERMES_ENGAGEMENT_LOG:-/var/log/claude-soma/engagement-drip.log}"
PYTHON="${HERMES_ENGAGEMENT_PYTHON:-/opt/claude-soma/.venv/bin/python3}"
[[ -x "${PYTHON}" ]] || PYTHON=$(command -v python3)

_die() { echo "ERROR: $*" >&2; exit "${2:-1}"; }
_log() {
    printf '%s engagement-post-now: %s\n' "$(date -u +%FT%TZ)" "$*" \
        | tee -a "${LOG}" >&2 || true
}

ID="${1:-}"
shift || true
[[ -z "${ID}" ]] && _die "usage: engagement-post-now.sh <id> --i-have-user-approval" 2

APPROVED=0
for arg in "$@"; do
    [[ "${arg}" == "--i-have-user-approval" ]] && APPROVED=1
done
if [[ "${APPROVED}" -ne 1 && "${HERMES_POST_APPROVAL:-}" != "yes" ]]; then
    _die "refusing to post without --i-have-user-approval (or HERMES_POST_APPROVAL=yes)" 2
fi

# Look the draft up + extract platform + permalink + text. Single Python
# call to keep the queue read atomic enough for one-shot operator use.
read -r PLATFORM PERMALINK STATUS TEXT_FILE < <(
    "${PYTHON}" - "${QUEUE}" "${ID}" <<'PY'
import json, sys, tempfile
q, draft_id = sys.argv[1], sys.argv[2]
hit = None
try:
    with open(q, encoding="utf-8") as fh:
        for line in fh:
            try: e = json.loads(line)
            except Exception: continue
            if e.get("id") == draft_id:
                hit = e
                break
except FileNotFoundError:
    pass
if not hit:
    print(f"NOTFOUND - - -")
    sys.exit(0)
plat = hit.get("platform", "")
perm = hit.get("source_permalink", "")
status = hit.get("status", "")
text = hit.get("draft_text", "")
if not perm or not isinstance(perm, str) or not perm.strip():
    print(f"{plat} NOPERMALINK {status} -")
    sys.exit(0)
tmp = tempfile.NamedTemporaryFile(
    "w", suffix=".txt", prefix=f"eng-{draft_id}-", delete=False, encoding="utf-8")
tmp.write(text or "")
tmp.close()
print(f"{plat} {perm} {status} {tmp.name}")
PY
)

[[ "${PLATFORM}" == "NOTFOUND" ]] && _die "draft id ${ID} not found in ${QUEUE}" 3
[[ "${PERMALINK}" == "NOPERMALINK" ]] && _die "draft ${ID} has no source_permalink — un-postable" 3

case "${PLATFORM}" in
    x)        POST_HELPER="${SCRIPT_DIR}/engagement-post-x.js" ;;
    linkedin) POST_HELPER="${SCRIPT_DIR}/engagement-post-linkedin.js" ;;
    *)        rm -f "${TEXT_FILE}"; _die "unknown platform: ${PLATFORM}" 3 ;;
esac

_log "posting id=${ID} platform=${PLATFORM} status=${STATUS}"

# Run the post helper WITH the approval flag forwarded; capture the last
# (machine-parseable RESULT:...) line.
RAW=$(node "${POST_HELPER}" --i-have-user-approval "${PERMALINK}" "${TEXT_FILE}" 2>&1)
RC=$?
rm -f "${TEXT_FILE}"
echo "${RAW}" >&2

# Find the RESULT: line — usually last, but defensive grep handles extra
# stderr lines from playwright/chromium.
RESULT_LINE=$(printf '%s\n' "${RAW}" | grep -E '^RESULT:' | tail -1)
_log "id=${ID} helper rc=${RC} result='${RESULT_LINE}'"

# Parse outcome
if [[ -z "${RESULT_LINE}" ]]; then
    _log "no RESULT line; treating as failure"
    "${PYTHON}" "${SCRIPT_DIR}/engagement-hourly-drip.py" --posted-error "${ID}" "no_result_line_helper_rc_${RC}"
    exit 4
fi

case "${RESULT_LINE}" in
    "RESULT:POSTED"*|"RESULT:UNVERIFIED"*)
        # Extract url=<...> (the post helper always includes the live URL).
        LIVE_URL=$(printf '%s' "${RESULT_LINE}" | sed -n 's/.*url=\([^ ]*\).*/\1/p')
        [[ -z "${LIVE_URL}" ]] && LIVE_URL="${PERMALINK}"
        "${PYTHON}" "${SCRIPT_DIR}/engagement-hourly-drip.py" --posted "${ID}" "${LIVE_URL}"
        _log "id=${ID} marked posted permalink=${LIVE_URL}"
        # mark_posted in engagement-hourly-drip.py already calls
        # regenerate_review_page — review doc is now in sync, served by
        # markserv/Caddy from /var/lib/claude-soma/relay/engagement-review.md.
        # No separate soma-publish needed.
        echo "POSTED id=${ID} url=${LIVE_URL}"
        case "${RESULT_LINE}" in *UNVERIFIED*) exit 4 ;; *) exit 0 ;; esac
        ;;
    "RESULT:NEEDS_REAUTH"*)
        "${PYTHON}" "${SCRIPT_DIR}/engagement-hourly-drip.py" --posted-error "${ID}" "needs_reauth"
        echo "NEEDS_REAUTH id=${ID}" >&2
        exit 5
        ;;
    "RESULT:UNREACHABLE"*)
        "${PYTHON}" "${SCRIPT_DIR}/engagement-hourly-drip.py" --posted-error "${ID}" "unreachable_target_post"
        echo "UNREACHABLE id=${ID}" >&2
        exit 6
        ;;
    "RESULT:DRY_RUN-READY"*)
        # If we reached this, the approval flag was missing somehow despite
        # our guard above. Don't update status.
        _log "id=${ID} dry-run-ready — no status mutation"
        echo "${RESULT_LINE}"
        exit 0
        ;;
    *)
        REASON=$(printf '%s' "${RESULT_LINE}" | sed 's/^RESULT://')
        "${PYTHON}" "${SCRIPT_DIR}/engagement-hourly-drip.py" --posted-error "${ID}" "${REASON}"
        echo "POST_FAILED id=${ID} reason=${REASON}" >&2
        exit 4
        ;;
esac
