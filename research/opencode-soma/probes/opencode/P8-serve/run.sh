#!/usr/bin/env bash
# P8: opencode serve / web / attach.
#  A. unsecured serve on 127.0.0.1:47181 -> OpenAPI dump, POST /session, sync message, prompt_async,
#     SSE capture (/event), lsof of the server (isolation), `opencode attach` in a tmux pane.
#  B. serve with OPENCODE_SERVER_PASSWORD on :47182 -> 401 without creds, 200 with, run --attach.
#  C. `opencode web` on :47183 (127.0.0.1) with an `open` PATH shim so no real browser starts.
set -u
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P8-serve/run.sh"
PROXY_PORT=47185
PROBE_ID=P8-serve; SANDBOX_FRESH=1; OC_BASEURL="http://127.0.0.1:$PROXY_PORT/v1"
source "$(dirname "$0")/../sandbox.sh"
cd "$PROJ" || exit 1
EVD="$OCSOMA_EVID/P8-serve"; mkdir -p "$EVD"
: > "$EVD/proxy.jsonl"
proxy_start "$PROXY_PORT" "$EVD/proxy.jsonl" || exit 1
TM="tmux -L ocsoma-pe-p8"
A=http://127.0.0.1:47181
MODEL='{"providerID":"ollama","modelID":"qwen3:32b"}'

wait_up() { local i; for i in $(seq 1 100); do curl -s -o /dev/null "$@" && return 0; sleep 0.2; done; return 1; }
jcurl() { curl -s -H 'content-type: application/json' "$@"; }

ev_open P8-serve/serve.log "opencode serve HTTP API: OpenAPI, sessions, sync+async prompt, SSE, auth, attach, web" "ollama/qwen3:32b (thinking off)"

# ---------------- A. unsecured server ----------------
ev "## A1. start: opencode serve --port 47181 --hostname 127.0.0.1 (no password)"
"$OC" serve --port 47181 --hostname 127.0.0.1 > "$SANDBOX/serve-a.out" 2>&1 &
SA=$!
wait_up "$A/global/health" || { ev "server A did not start"; }
ev_run cat "$SANDBOX/serve-a.out"
ev_run curl -s "$A/global/health"
ev "## A2. OpenAPI spec (saved to evidence/opencode/P8-serve/openapi.json)"
curl -s "$A/doc" -o "$EVD/openapi.json"
ev_run python3.12 -I -c '
import json,sys
d=json.load(open(sys.argv[1])); n=0
for p,ops in sorted(d["paths"].items()):
    for m,o in ops.items():
        n+=1; print(m.upper().ljust(7), p, "-", o.get("operationId"))
print(n, "operations; openapi", d.get("openapi"))' "$EVD/openapi.json"

ev "## A3. SSE: curl -sN $A/event  (and v2: $A/api/event) captured in background"
curl -sN "$A/event" > "$EVD/sse-event.txt" 2>&1 &
SSE1=$!
curl -sN "$A/api/event" > "$EVD/sse-api-event.txt" 2>&1 &
SSE2=$!
sleep 1

ev "## A4. create a session over HTTP"
ev_run jcurl -X POST "$A/session" -d '{"title":"p8-http"}'
SID=$(jcurl -X POST "$A/session" -d '{"title":"p8-http-main"}' | jq -r .id)
ev "created main session: $SID"

ev "## A5. sync prompt: POST /session/{id}/message (blocks until the reply is complete)"
T0=$(date +%s)
ev_run jcurl -X POST "$A/session/$SID/message" -d "{\"model\":$MODEL,\"parts\":[{\"type\":\"text\",\"text\":\"Reply with exactly the text P8-SYNC-7731 and nothing else.\"}]}"
ev "sync wall-clock: $(( $(date +%s) - T0 ))s"

ev "## A6. async prompt: POST /session/{id}/prompt_async returns 204 at once; poll GET /session/status"
n_before=$(curl -s "$A/session/$SID/message" | jq '[.[] | select(.info.role=="assistant" and .info.time.completed != null)] | length')
ev_run curl -s -o /dev/null -w 'HTTP %{http_code} in %{time_total}s\n' -H 'content-type: application/json' -X POST "$A/session/$SID/prompt_async" -d "{\"model\":$MODEL,\"parts\":[{\"type\":\"text\",\"text\":\"Reply with exactly the text P8-ASYNC-4402 and nothing else.\"}]}"
for i in $(seq 1 240); do
  st=$(curl -s "$A/session/status" | jq -c --arg s "$SID" '.[$s] // "absent(idle)"')
  [ "$i" -le 4 ] && ev "status poll $i: $st"
  n_now=$(curl -s "$A/session/$SID/message" | jq '[.[] | select(.info.role=="assistant" and .info.time.completed != null)] | length')
  [ "$n_now" -gt "$n_before" ] && [ "$st" != '{"type":"busy"}' ] && break
  sleep 0.5
done
ev "status after poll loop ($i polls): $st; completed assistant messages $n_before -> $n_now"
ev_run curl -s "$A/session/status"
ev "## A7. read the conversation back: GET /session/{id}/message (role + text parts)"
ev_run sh -c "curl -s '$A/session/$SID/message' | jq -c '.[] | {role: .info.role, agent: .info.agent, model: (.info.modelID // .info.model.modelID), tokens: .info.tokens, text: ([.parts[] | select(.type==\"text\") | .text] | join(\" \"))}'"
ev "## A8. v2 read path: GET /api/session/{id}/message"
ev_run sh -c "curl -s '$A/api/session/$SID/message' | head -c 1500"

ev "## A9. lsof of the running server: every open file must be inside the sandbox (or system libs)"
ev_run sh -c "lsof -p $SA | awk '{print \$4, \$5, \$9}' | grep ' REG ' | sort -u"

ev "## A10. opencode attach $A --session $SID  in tmux -L ocsoma-pe-p8 (200x50)"
$TM kill-server 2>/dev/null
$TM new-session -d -s attach -x 200 -y 50 "$OC attach $A --session $SID"
sleep 6
$TM capture-pane -p -t attach > "$EVD/attach-pane-1.txt"
ev_run cat "$EVD/attach-pane-1.txt"
ev "## A11. drive the attached TUI: send-keys -l a prompt + Enter, then read it back over HTTP"
$TM send-keys -t attach -l "Reply with exactly the text P8-ATTACH-5150 and nothing else."
sleep 0.5
$TM send-keys -t attach Enter
for i in $(seq 1 120); do
  n=$(curl -s "$A/session/$SID/message" | jq '[.[] | select(.info.role=="assistant")] | length')
  [ "$n" -ge 3 ] && break
  sleep 0.5
done
sleep 3
$TM capture-pane -p -t attach > "$EVD/attach-pane-2.txt"
ev_run cat "$EVD/attach-pane-2.txt"
ev_run sh -c "curl -s '$A/session/$SID/message' | jq -c '.[] | {role: .info.role, text: ([.parts[] | select(.type==\"text\") | .text] | join(\" \"))}'"
$TM kill-server 2>/dev/null

kill "$SSE1" "$SSE2" 2>/dev/null
ev "## A12. SSE event types seen on /event during A4-A11 (count by type)"
ev_run sh -c "grep '^data:' '$EVD/sse-event.txt' | sed 's/^data: //' | jq -r .type | sort | uniq -c | sort -rn"
ev "first 5 SSE frames on /event:"
ev_run sh -c "grep '^data:' '$EVD/sse-event.txt' | head -5 | cut -c1-400"
ev "SSE event types seen on /api/event (v2 stream):"
ev_run sh -c "grep '^data:' '$EVD/sse-api-event.txt' | sed 's/^data: //' | jq -r '.type // .payload.type // \"?\"' | sort | uniq -c | sort -rn | head -40"
kill "$SA" 2>/dev/null; wait "$SA" 2>/dev/null

# ---------------- B. password-protected server ----------------
ev "## B1. start: OPENCODE_SERVER_PASSWORD=<random, redacted> opencode serve --port 47182"
PW=$(python3.12 -I -c 'import secrets;print(secrets.token_hex(12))')
printf 'machine 127.0.0.1 login opencode password %s\n' "$PW" > "$SANDBOX/netrc"; chmod 600 "$SANDBOX/netrc"
OPENCODE_SERVER_PASSWORD="$PW" "$OC" serve --port 47182 --hostname 127.0.0.1 > "$SANDBOX/serve-b.out" 2>&1 &
SB=$!
wait_up --netrc-file "$SANDBOX/netrc" "http://127.0.0.1:47182/global/health"
ev_run cat "$SANDBOX/serve-b.out"
ev "without credentials:"
ev_run curl -s -i http://127.0.0.1:47182/global/health
ev_run curl -s -o /dev/null -w 'GET /session -> HTTP %{http_code}\n' http://127.0.0.1:47182/session
ev_run curl -s -o /dev/null -w 'GET /doc -> HTTP %{http_code}\n' http://127.0.0.1:47182/doc
ev "wrong password:"
ev_run curl -s -o /dev/null -w 'HTTP %{http_code}\n' -u opencode:wrong-password http://127.0.0.1:47182/session
ev "with credentials (curl --netrc-file, password not on the command line):"
ev_run curl -s -i --netrc-file "$SANDBOX/netrc" http://127.0.0.1:47182/global/health
ev "## B2. opencode run --attach against the protected server (password from OPENCODE_SERVER_PASSWORD env)"
OPENCODE_SERVER_PASSWORD="$PW" ev_run "$OC" run --attach http://127.0.0.1:47182 -m ollama/qwen3:32b "Reply with exactly the text P8-AUTH-RUN-88 and nothing else."
ev "## B3. same without the password -> must fail"
ev_run "$OC" run --attach http://127.0.0.1:47182 -m ollama/qwen3:32b "Reply with exactly the text SHOULD-NOT-RUN."
kill "$SB" 2>/dev/null; wait "$SB" 2>/dev/null

# ---------------- C. opencode web ----------------
ev "## C1. opencode web --port 47183 --hostname 127.0.0.1, password set; PATH shim replaces 'open' so no real browser starts"
mkdir -p "$SANDBOX/bin"
printf '#!/bin/sh\necho "open-shim called with: $*" >> "%s/open-shim.log"\n' "$SANDBOX" > "$SANDBOX/bin/open"
chmod +x "$SANDBOX/bin/open"
PATH="$SANDBOX/bin:$PATH" OPENCODE_SERVER_PASSWORD="$PW" "$OC" web --port 47183 --hostname 127.0.0.1 > "$SANDBOX/web.out" 2>&1 &
SW=$!
wait_up --netrc-file "$SANDBOX/netrc" "http://127.0.0.1:47183/global/health"
sleep 1
ev_run sh -c "sed 's/\x1b\[[0-9;]*m//g' '$SANDBOX/web.out'"
ev_run cat "$SANDBOX/open-shim.log"
ev "GET / without credentials (what a phone browser would see first):"
ev_run curl -s -i http://127.0.0.1:47183/
ev "GET / with credentials:"
curl -s --netrc-file "$SANDBOX/netrc" http://127.0.0.1:47183/ -o "$EVD/web-index.html"
ev_run sh -c "wc -c '$EVD/web-index.html'; grep -oE '<title>[^<]*</title>|<script[^>]*src=\"[^\"]*\"|<link[^>]*href=\"[^\"]*\"' '$EVD/web-index.html' | head -20"
ASSET=$(grep -oE '<script[^>]*src="[^"]*"' "$EVD/web-index.html" | head -1 | sed -E 's/.*src="([^"]*)".*/\1/')
ev "first script asset: $ASSET"
case "$ASSET" in
  http*) ev "asset is REMOTE (not served by the local binary)";;
  "") ev "no script asset found";;
  *) ev_run curl -s -o /dev/null -w 'GET %{url_effective} -> HTTP %{http_code}, %{size_download} bytes, %{content_type}\n' --netrc-file "$SANDBOX/netrc" "http://127.0.0.1:47183$ASSET";;
esac
kill "$SW" 2>/dev/null; wait "$SW" 2>/dev/null
ev "## C2. LAN exposure flags (from --help, NOT exercised): --hostname 0.0.0.0, --mdns (defaults hostname to 0.0.0.0, advertises opencode.local), --mdns-domain, --cors"
ev_run sh -c "'$OC' web --help 2>&1 | grep -E 'hostname|mdns|cors|port'"

proxy_stop "$PROXY_PORT"
ev_redact "$EV_LOG"
sed -i '' "s/$PW/<REDACTED-PW>/g" "$EV_LOG" "$EVD"/*.txt 2>/dev/null

# ---------------- verdict ----------------
pass=1; why=""
grep -q 'P8-SYNC-7731' "$EV_LOG" || { pass=0; why="$why no-sync-reply"; }
grep -q 'P8-ASYNC-4402' "$EV_LOG" || { pass=0; why="$why no-async-reply"; }
grep -q 'HTTP/1.1 401' "$EV_LOG" || { pass=0; why="$why no-401"; }
grep -q 'P8-AUTH-RUN-88' "$EV_LOG" || { pass=0; why="$why no-auth-run"; }
grep -q 'P8-ATTACH-5150' "$EV_LOG" || { pass=0; why="$why attach-prompt-not-seen"; }
[ -s "$EVD/openapi.json" ] || { pass=0; why="$why no-openapi"; }
if [ "$pass" = 1 ]; then
  ev_verdict PASS "serve exposes a full OpenAPI HTTP+SSE API; sync and async prompts, basic-auth (401 without creds), run --attach, TUI attach and the web UI all worked on 127.0.0.1."
else
  ev_verdict PARTIAL "some checks failed:$why (see transcript)."
fi
tail -1 "$EV_LOG"
