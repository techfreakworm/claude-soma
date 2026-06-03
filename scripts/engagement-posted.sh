#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -lt 2 ]]; then
    echo "Usage: engagement-posted.sh <id> <permalink> | <id> --error '<msg>'" >&2
    exit 1
fi

ID="$1"
shift

if [[ "${1:-}" == "--error" ]]; then
    exec python3 "${SCRIPT_DIR}/engagement-hourly-drip.py" --posted-error "${ID}" "${2:-unknown error}"
else
    exec python3 "${SCRIPT_DIR}/engagement-hourly-drip.py" --posted "${ID}" "$1"
fi
