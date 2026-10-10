#!/usr/bin/env bash
# Focused redo of P6 section 2f: can the orchestrator REJECT a pending permission over HTTP, and
# where is it listed? Checks GET /permission, GET /api/permission/request and
# GET /session/{id}/permission, then rejects via POST /permission/{id}/reply {reply: reject}.
set -u
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P6-permissions/reject-retest.sh"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROXY_PORT=47167; SERVE_PORT=47168
PROBE_ID=P6-reject; SANDBOX_FRESH=1; OC_BASEURL="http://127.0.0.1:$PROXY_PORT/v1"
source "$HERE/../sandbox.sh"
cd "$PROJ" || exit 1
MODEL=ollama/qwen3:32b; MJSON='{"providerID":"ollama","modelID":"qwen3:32b"}'
EVD="$OCSOMA_EVID/P6-permissions"; mkdir -p "$EVD"
W="$PROJ/p6work"; mkdir -p "$W"
BASE="http://127.0.0.1:$SERVE_PORT"
cat > "$PROJ/opencode.json" <<JSON
{ "\$schema": "https://opencode.ai/config.json", "permission": { "bash": "ask" } }
JSON
: > "$EVD/reject-proxy.jsonl"
proxy_start "$PROXY_PORT" "$EVD/reject-proxy.jsonl" || exit 1
"$OC" serve --port "$SERVE_PORT" --hostname 127.0.0.1 > "$SANDBOX/serve.out" 2>&1 &
SPID=$!
for i in $(seq 1 100); do curl -s -o /dev/null "$BASE/global/health" && break; sleep 0.2; done
jcurl() { curl -s -H 'content-type: application/json' "$@"; }

ev_open P6-permissions/reject-retest.log "reject a pending permission over HTTP; which endpoint lists it" "$MODEL (thinking off)"
SID=$(jcurl -X POST "$BASE/session" -d '{"title":"p6-reject"}' | jq -r .id)
ev "session: $SID ; firing a bash tool call that needs ask, via prompt_async"
jcurl -o /dev/null -X POST "$BASE/session/$SID/prompt_async" -d "{\"model\":$MJSON,\"parts\":[{\"type\":\"text\",\"text\":\"Use the bash tool to run exactly: touch $W/p6-reject-target   Then say DONE.\"}]}"
ev "## poll the three permission endpoints until one lists a pending request (max 60s)"
PID=""; SRC=""
for i in $(seq 1 120); do
  PID=$(jcurl "$BASE/permission" | jq -r '.[0].id // empty'); [ -n "$PID" ] && { SRC="GET /permission"; break; }
  PID=$(jcurl "$BASE/api/permission/request" | jq -r '(.data // .)[0].id // empty'); [ -n "$PID" ] && { SRC="GET /api/permission/request"; break; }
  PID=$(jcurl "$BASE/session/$SID/permission" | jq -r '(.data // .)[0].id // .[0].requestID // empty'); [ -n "$PID" ] && { SRC="GET /session/{id}/permission"; break; }
  sleep 0.5
done
ev "found pending permission id via: ${SRC:-NONE} after ~$(python3.12 -I -c "print($i*0.5)")s"
ev_run jcurl "$BASE/permission"
ev_run jcurl "$BASE/session/$SID/permission"
ev_run curl -s "$BASE/session/status"
if [ -n "$PID" ]; then
  ev "## reject it: POST /permission/$PID/reply {reply: reject}"
  ev_run jcurl -w '\n-> HTTP %{http_code}\n' -X POST "$BASE/permission/$PID/reply" -d '{"reply":"reject","message":"operator rejected from Telegram"}'
fi
for i in $(seq 1 120); do st=$(curl -s "$BASE/session/status" | jq -c --arg s "$SID" '.[$s] // "idle"'); [ "$st" = '"idle"' ] && break; sleep 0.5; done
ev "session status after reject: $st"
ev_run sh -c "curl -s '$BASE/session/$SID/message' | jq -c '.[-1] | {role: .info.role, finish: .info.finish, error: (.info.error.name // null), tools: [.parts[] | select(.type==\"tool\") | {tool, status: .state.status, err: ((.state.error // \"\")|.[0:120])}], text: ([.parts[] | select(.type==\"text\") | .text] | join(\" \") | .[0:120])}'"
created=no; [ -e "$W/p6-reject-target" ] && created=YES
ev "target file created (must be no): $created"
kill "$SPID" 2>/dev/null; wait "$SPID" 2>/dev/null
proxy_stop "$PROXY_PORT"
if [ -n "$PID" ] && [ "$created" = no ]; then
  ev_verdict PASS "a pending permission is listed via $SRC and rejected over HTTP with POST /permission/{id}/reply {reply:reject}; the tool call did not run (target absent)."
else
  ev_verdict FAIL "reject path not demonstrated (pid=${PID:-none} created=$created)."
fi
ev_redact "$EV_LOG"; tail -1 "$EV_LOG"
