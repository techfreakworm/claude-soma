# shellcheck shell=bash
# Shared preamble for the P1 scripts (sourced). Each part gets its own sandbox + proxy port.
#   P1_PART=<name> P1_PROXY=<port> P1_SERVE=<port>; source common.sh
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROBE_ID="P1-$P1_PART"; SANDBOX_FRESH=1; OC_BASEURL="http://127.0.0.1:$P1_PROXY/v1"
source "$HERE/../sandbox.sh"
cd "$PROJ" || exit 1
MODEL=ollama/qwen3:32b
MJSON='{"providerID":"ollama","modelID":"qwen3:32b"}'
EVD="$OCSOMA_EVID/P1-headless"; mkdir -p "$EVD"
PROXY_LOG="$EVD/proxy-$P1_PART.jsonl"; : > "$PROXY_LOG"
proxy_start "$P1_PROXY" "$PROXY_LOG" || exit 1
BASE="http://127.0.0.1:$P1_SERVE"
DRIVE="python3.12 -I $OCSOMA_PROBES/ocdrive.py --base $BASE"
LONG='Count from 1 to 200, writing every number as an English word, one per line. Output only the list.'
jcurl() { curl -s -H 'content-type: application/json' "$@"; }
msgs() { curl -s "$BASE/session/$1/message" | jq -c '.[] | {id: .info.id[-8:], role: .info.role, created: .info.time.created, completed: .info.time.completed, finish: .info.finish, error: (.info.error.name // null), text: ([.parts[] | select(.type=="text") | .text] | join(" ") | .[0:120])}'; }
start_serve() { "$OC" serve --port "$P1_SERVE" --hostname 127.0.0.1 >> "$SANDBOX/serve.out" 2>&1 & SPID=$!; for i in $(seq 1 100); do curl -s -o /dev/null "$BASE/global/health" && return 0; sleep 0.2; done; return 1; }
sid_of() { grep -o '"sessionID":"ses_[A-Za-z0-9]*"' "$1" | head -1 | cut -d'"' -f4; }
wait_idle() {  # wait_idle <sid> <max-seconds>; echoes final status
  local i st
  for i in $(seq 1 $(( $2 * 2 ))); do st=$(curl -s "$BASE/session/status" | jq -c --arg s "$1" '.[$s] // "idle"'); [ "$st" = '"idle"' ] && break; sleep 0.5; done
  echo "$st"
}
# run_bounded <secs> <outfile> <cmd...>: background run, SIGKILL if still running after <secs>
run_bounded() {
  local secs="$1" out="$2"; shift 2
  "$@" > "$out" 2>&1 < /dev/null &
  local pid=$! t=0
  while kill -0 "$pid" 2>/dev/null && [ "$t" -lt "$secs" ]; do sleep 1; t=$((t+1)); done
  if kill -0 "$pid" 2>/dev/null; then kill -9 "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; echo "STILL RUNNING after ${secs}s -> SIGKILL (hung)"; return 124; fi
  wait "$pid"; local rc=$?; echo "exited rc=$rc after ~${t}s"; return $rc
}
