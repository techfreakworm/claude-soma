#!/usr/bin/env bash
set -uo pipefail
[[ "${SOMA_ORCHESTRATOR_GATE_DISABLED:-0}" == "1" ]] && exit 0
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
exec /usr/bin/env python3 "$SCRIPT_DIR/orchestrator_gate.py"
