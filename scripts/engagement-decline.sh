#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -eq 0 ]]; then
    echo "Usage: engagement-decline.sh <id> [--reason '<text>']" >&2
    exit 1
fi

exec python3 "${SCRIPT_DIR}/engagement-hourly-drip.py" --decline "$@"
