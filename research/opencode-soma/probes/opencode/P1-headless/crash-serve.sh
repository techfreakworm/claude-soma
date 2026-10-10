#!/usr/bin/env bash
# P1.3 kill -9 `opencode serve` mid-generation, restart it, read the session over HTTP, continue it.
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P1-headless/crash-serve.sh"
P1_PART=crashserve; P1_PROXY=47117; P1_SERVE=47113
source "$(dirname "$0")/common.sh"
ev_open P1-headless/crash-serve.log "kill -9 opencode serve mid-generation, restart, read the session over HTTP, continue it" "$MODEL (thinking off)"
start_serve || ev "serve did not start"
SS=$($DRIVE new --title p1-serve-crash)
ev "session: $SS"
ev_run $DRIVE turn --session "$SS" --model "$MODEL" --text "Remember this secret token: MANGO-2290. Reply only with the word OK."
ev "## long generation via prompt_async, kill -9 the server after 10s"
ev_run jcurl -o /dev/null -w 'prompt_async HTTP %{http_code}\n' -X POST "$BASE/session/$SS/prompt_async" -d "{\"model\":$MJSON,\"parts\":[{\"type\":\"text\",\"text\":\"$LONG\"}]}"
sleep 10
ev_run curl -s "$BASE/session/status"
kill -9 "$SPID"; wait "$SPID" 2>/dev/null
ev "kill -9 serve pid $SPID at $(date -u +%H:%M:%S); health now: $(curl -s -m 2 -o /dev/null -w '%{http_code}' "$BASE/global/health")"
sleep 2
ev "## restart serve on the same port, read the session back"
start_serve || ev "serve did not restart"
ev_run curl -s "$BASE/session/$SS"
ev_run msgs "$SS"
ev_run curl -s "$BASE/session/status"
ev "## continue the session over HTTP"
ev_run $DRIVE turn --session "$SS" --model "$MODEL" --text "Stop counting. What was the secret token I gave you at the very beginning? Reply with only the token."
kill "$SPID" 2>/dev/null; wait "$SPID" 2>/dev/null
ok=$(grep -c '"final_text": "MANGO-2290"' "$EV_LOG")
if [ "$ok" -ge 1 ]; then
  ev_verdict PASS "after kill -9 of serve mid-generation, the restarted serve returned the session and its history over HTTP (status idle, partial reply left incomplete) and continued it (MANGO-2290 recalled)."
else
  ev_verdict FAIL "token not recalled after serve crash."
fi
proxy_stop "$P1_PROXY"; ev_redact "$EV_LOG"; tail -1 "$EV_LOG"
