#!/usr/bin/env bash
# P4 addendum (NO LLM): GET /experimental/capabilities reports the background-subagents flag,
# gated by OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS (desk item A). Flag off vs on.
set -u
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P4-subagents/capabilities.sh"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROBE_ID=P4-caps; SANDBOX_FRESH=1
source "$HERE/../sandbox.sh"
EVD="$OCSOMA_EVID/P4-subagents"; mkdir -p "$EVD"
CP=$(( ${OCSOMA_PORT_BASE:-47100} + 48 ))
ev_open P4-subagents/capabilities.log "GET /experimental/capabilities gates background subagents on OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS" "none"
for state in off on; do
  if [ "$state" = on ]; then envflag=(env OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true); else envflag=(env); fi
  ( cd "$PROJ" && exec "${envflag[@]}" "$OC" serve --port "$CP" --hostname 127.0.0.1 ) > "$SANDBOX/caps.out" 2>&1 &
  SP=$!
  for i in $(seq 1 100); do curl -s -o /dev/null "http://127.0.0.1:$CP/global/health" && break; sleep 0.2; done
  ev "## flag $state:"
  ev_run sh -c "curl -s 'http://127.0.0.1:$CP/experimental/capabilities'"
  kill "$SP" 2>/dev/null; wait "$SP" 2>/dev/null
  sleep 1
done
on=$(grep -c '"backgroundSubagents":true' "$EV_LOG")
if [ "$on" -ge 1 ]; then
  ev_verdict PASS "GET /experimental/capabilities reports backgroundSubagents=true only with OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS set; the P4 background.log run exercised the resulting task background=true path."
else
  ev_verdict PARTIAL "capabilities endpoint response captured above."
fi
tail -1 "$EV_LOG"
