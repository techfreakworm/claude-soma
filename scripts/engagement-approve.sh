#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -eq 0 ]]; then
    echo "Usage: engagement-approve.sh <id> [<id> ...] | --all" >&2
    exit 1
fi

if [[ "$1" == "--all" ]]; then
    exec python3 "${SCRIPT_DIR}/engagement-hourly-drip.py" --approve-all
else
    exec python3 "${SCRIPT_DIR}/engagement-hourly-drip.py" --approve "$@"
fi
