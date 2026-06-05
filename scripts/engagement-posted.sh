#!/usr/bin/env bash
# FI-REVIEW-DOC-BELT-AND-SUSPENDERS (2026-06-06)
#
# Mark an engagement draft as posted (or post-error) AND force a fresh
# render+publish of the engagement review doc. The inner --posted /
# --posted-error path already regenerates the doc, but a second
# --regen-only call after the status change guarantees the relay doc
# matches queue.jsonl regardless of which script marked the post or
# whether the inner regen quietly errored.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRIP="${SCRIPT_DIR}/engagement-hourly-drip.py"

if [[ $# -lt 2 ]]; then
    echo "Usage: engagement-posted.sh <id> <permalink> | <id> --error '<msg>'" >&2
    exit 1
fi

ID="$1"
shift

if [[ "${1:-}" == "--error" ]]; then
    python3 "${DRIP}" --posted-error "${ID}" "${2:-unknown error}"
    rc=$?
else
    python3 "${DRIP}" --posted "${ID}" "$1"
    rc=$?
fi

# Belt-and-suspenders: always regen the review doc after a status change
# so the relay never serves a stale view, regardless of inner-path edits.
python3 "${DRIP}" --regen-only || true

exit "${rc}"
