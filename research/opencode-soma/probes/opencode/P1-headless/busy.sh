#!/usr/bin/env bash
# P1.4 a second prompt while the session is still generating (decides whether a Telegram front end
# can stay responsive), on one `opencode serve`:
#  4a prompt_async x2 (queue?)  4b sync /message while busy  4c abort while busy
#  4d run --attach while busy   4e v2 /api/session/{id}/prompt queue|steer while busy (separate session)
#  4f an independent `opencode run -s` process (second writer, no --attach) while busy (separate
#     session; bounded; then wedge check + abort recovery)
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P1-headless/busy.sh"
P1_PART=busy; P1_PROXY=$(( ${OCSOMA_PORT_BASE:-47100} + 18 )); P1_SERVE=$(( ${OCSOMA_PORT_BASE:-47100} + 14 ))
source "$(dirname "$0")/common.sh"
long_async() { jcurl -o /dev/null -X POST "$BASE/session/$1/prompt_async" -d "{\"model\":$MJSON,\"parts\":[{\"type\":\"text\",\"text\":\"$LONG\"}]}"; }
ev_open P1-headless/busy.log "second prompt while a session is busy: queue / block / abort / attach / v2 / second process" "$MODEL (thinking off)"
start_serve || ev "serve did not start"
SB=$($DRIVE new --title p1-busy)
ev "session: $SB"
ev_run $DRIVE turn --session "$SB" --model "$MODEL" --text "Remember this secret token: PEAR-5521. Reply only with the word OK."

ev "## 4a. prompt_async #1 (long), then prompt_async #2 three seconds later"
long_async "$SB"; sleep 3
ev_run curl -s "$BASE/session/status"
ev_run jcurl -o /dev/null -w 'second prompt_async -> HTTP %{http_code} in %{time_total}s\n' -X POST "$BASE/session/$SB/prompt_async" -d "{\"model\":$MJSON,\"parts\":[{\"type\":\"text\",\"text\":\"Reply with exactly SECOND-PROMPT-OK and nothing else.\"}]}"
ev "final status: $(wait_idle "$SB" 400)"
ev_run msgs "$SB"

ev "## 4b. sync POST /message while busy"
long_async "$SB"; sleep 3
ev_run jcurl -o "$SANDBOX/sync.json" -w 'sync /message while busy -> HTTP %{http_code} after %{time_total}s\n' -X POST "$BASE/session/$SB/message" -d "{\"model\":$MJSON,\"parts\":[{\"type\":\"text\",\"text\":\"Reply with exactly THIRD-SYNC-OK and nothing else.\"}]}"
ev_run sh -c "jq -c '{role: .info.role, finish: .info.finish, text: ([.parts[]? | select(.type==\"text\") | .text] | join(\" \"))}' '$SANDBOX/sync.json'"
ev "final status: $(wait_idle "$SB" 400)"

ev "## 4c. abort while busy (Telegram /stop analog): POST /session/{id}/abort"
long_async "$SB"; sleep 6
ev_run curl -s "$BASE/session/status"
TA=$(python3.12 -I -c 'import time;print(time.time())')
ev_run jcurl -w '\n-> HTTP %{http_code} in %{time_total}s\n' -X POST "$BASE/session/$SB/abort"
ev "status right after abort: $(wait_idle "$SB" 30) (within $(python3.12 -I -c "import time;print(round(time.time()-$TA,1))")s)"
ev_run sh -c "curl -s '$BASE/session/$SB/message' | jq -c '.[-1] | {role: .info.role, finish: .info.finish, error: (.info.error.name // null), completed: .info.time.completed, text_len: ([.parts[] | select(.type==\"text\") | .text] | join(\"\") | length)}'"
ev_run $DRIVE turn --session "$SB" --model "$MODEL" --text "What was the secret token I gave you at the very beginning? Reply with only the token."

ev "## 4d. opencode run --attach on the same busy session"
long_async "$SB"; sleep 3
ev_run run_bounded 400 "$SANDBOX/attach.txt" "$OC" run --attach "$BASE" -m "$MODEL" -s "$SB" "Reply with exactly ATTACH-WHILE-BUSY-OK and nothing else."
ev_run cat "$SANDBOX/attach.txt"
ev "final status: $(wait_idle "$SB" 200)"
ev_run msgs "$SB"
kill "$SPID" 2>/dev/null; wait "$SPID" 2>/dev/null

ev "## 4e. v2 API while busy (separate session): POST /api/session/{id}/prompt delivery=queue and steer"
start_serve || ev "serve did not restart"
SV=$($DRIVE new --title p1-busy-v2)
long_async "$SV"; sleep 3
ev_run jcurl -w '\n-> HTTP %{http_code} in %{time_total}s\n' -X POST "$BASE/api/session/$SV/prompt" -d '{"prompt":{"text":"Reply with exactly V2-QUEUE-OK and nothing else."},"delivery":"queue"}'
ev_run jcurl -w '\n-> HTTP %{http_code} in %{time_total}s\n' -X POST "$BASE/api/session/$SV/prompt" -d '{"prompt":{"text":"Reply with exactly V2-STEER-OK and nothing else."},"delivery":"steer"}'
ev "final v1 status: $(wait_idle "$SV" 400)"
sleep 20
ev "v1 message list (GET /session/{id}/message):"
ev_run msgs "$SV"
ev "v2 message list (GET /api/session/{id}/message):"
ev_run sh -c "curl -s '$BASE/api/session/$SV/message' | jq -c '.data[] | {type, created: .time.created, completed: .time.completed, text: ([.content[]? | select(.type==\"text\") | .text] | join(\" \") | .[0:80])}'"
ev "LLM requests while the long generation was running (proxy):"
ev_run sh -c "jq -c '{ts, last_user: .request.last_user[0:50], wall_s, text: .response.text[0:30]}' '$PROXY_LOG' | tail -5"

ev "## 4f. an independent opencode process (no --attach) on a busy session owned by serve (bounded 180s)"
SC=$($DRIVE new --title p1-busy-cli)
ev_run $DRIVE turn --session "$SC" --model "$MODEL" --text "Remember this secret token: PLUM-7310. Reply only with the word OK."
long_async "$SC"; sleep 3
ev_run run_bounded 180 "$SANDBOX/cli.txt" "$OC" run -m "$MODEL" --format json -s "$SC" "Reply with exactly CLI-WHILE-BUSY-OK and nothing else."
ev_run sh -c "cut -c1-300 '$SANDBOX/cli.txt'"
ev "server status for the session after the long generation should have ended: $(wait_idle "$SC" 240)"
ev_run msgs "$SC"
ev "## 4f-recovery: POST /abort, then a fresh prompt"
ev_run jcurl -w '\n-> HTTP %{http_code}\n' -X POST "$BASE/session/$SC/abort"
ev "status after abort: $(wait_idle "$SC" 30)"
ev_run $DRIVE turn --session "$SC" --model "$MODEL" --timeout 240 --text "What was the secret token I gave you at the very beginning? Reply with only the token."
kill "$SPID" 2>/dev/null; wait "$SPID" 2>/dev/null

q_ok=$(grep -c '"text":"SECOND-PROMPT-OK"' "$EV_LOG"); s_ok=$(grep -c 'THIRD-SYNC-OK' "$EV_LOG")
a_ok=$(grep -c 'status right after abort: "idle"' "$EV_LOG"); r_ok=$(grep -c '"final_text": "PEAR-5521"' "$EV_LOG")
t_ok=$(grep -c 'ATTACH-WHILE-BUSY-OK' "$SANDBOX/attach.txt")
ev "summary: queued-second-prompt=$q_ok sync-while-busy=$s_ok abort-idle=$a_ok recall-after-abort=$r_ok attach-queued=$t_ok"
if [ "$q_ok" -ge 1 ] && [ "$a_ok" -ge 1 ] && [ "$t_ok" -ge 1 ]; then
  ev_verdict PASS "on one serve, a busy session accepts prompt_async at once and queues it, sync /message blocks until its turn, run --attach queues, and abort returns it to idle; second writers (v2 loop, independent CLI) are hazards shown in 4e/4f."
else
  ev_verdict PARTIAL "queue=$q_ok abort=$a_ok attach=$t_ok (see sections)."
fi
proxy_stop "$P1_PROXY"; ev_redact "$EV_LOG"; tail -1 "$EV_LOG"
