#!/usr/bin/env bash
# Run opencode inside a probe's sandbox project dir:  oc-in-proj.sh <probe-id> <opencode args...>
# (keeps the caller's shell cwd untouched; the sandbox config is (re)written by sandbox.sh)
set -u
PROBE_ID="$1"; shift
source "$(dirname "$0")/sandbox.sh"
cd "$PROJ" || exit 1
exec "$OC" "$@"
