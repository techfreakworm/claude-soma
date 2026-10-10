#!/usr/bin/env bash
# P1 headless:
#  1. `opencode run --format json`: plant a token, recall it with -s <id>, then with --continue.
#  2. crash: kill -9 a `run` mid-generation, export the session, continue it and recall the token.
#  3. crash: kill -9 `opencode serve` mid-generation, restart, read the session over HTTP, continue it.
#  4. busy session: second prompt while the first still generates (prompt_async, sync /message,
#     v2 /api/.../prompt queue|steer, a second CLI process on the same session), then abort.
set -u
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P1-headless/run1-monolithic.sh (first run; superseded by session.sh crash-run.sh crash-serve.sh busy.sh)"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROXY_PORT=47115; SERVE_PORT=47111
PROBE_ID=P1-headless; SANDBOX_FRESH=1; OC_BASEURL="http://127.0.0.1:$PROXY_PORT/v1"
source "$HERE/../sandbox.sh"
cd "$PROJ" || exit 1
MODEL=ollama/qwen3:32b
MJSON='{"providerID":"ollama","modelID":"qwen3:32b"}'
EVD="$OCSOMA_EVID/P1-headless/run1"; mkdir -p "$EVD"
: > "$EVD/proxy.jsonl"
proxy_start "$PROXY_PORT" "$EVD/proxy.jsonl" || exit 1
BASE="http://127.0.0.1:$SERVE_PORT"
DRIVE="python3.12 -I $OCSOMA_PROBES/ocdrive.py --base $BASE"
LONG='Count from 1 to 200, writing every number as an English word, one per line. Output only the list.'
jcurl() { curl -s -H 'content-type: application/json' "$@"; }
msgs() { curl -s "$BASE/session/$1/message" | jq -c '.[] | {id: .info.id, role: .info.role, created: .info.time.created, completed: .info.time.completed, finish: .info.finish, error: (.info.error.name // null), text: ([.parts[] | select(.type=="text") | .text] | join(" ") | .[0:120])}'; }
start_serve() { "$OC" serve --port "$SERVE_PORT" --hostname 127.0.0.1 >> "$SANDBOX/serve.out" 2>&1 & SPID=$!; for i in $(seq 1 100); do curl -s -o /dev/null "$BASE/global/health" && return 0; sleep 0.2; done; return 1; }
sid_of() { grep -o '"sessionID":"ses_[A-Za-z0-9]*"' "$1" | head -1 | cut -d'"' -f4; }

# ======================= 1. run + session reuse =======================
ev_open P1-headless/run1/run-session.log "opencode run --format json; session ids stable and reusable via -s and --continue" "$MODEL (thinking off)"
ev_run "$OC" run -m "$MODEL" --format json "Remember this secret token: KIWI-4417. Reply only with the word OK."
"$OC" run -m "$MODEL" --format json "Remember this second secret token: LIME-0082. Reply only with the word OK." > "$SANDBOX/t1.json" 2>&1
ev "(second plant, separate session; output in sandbox t1.json)"
ev_run cat "$SANDBOX/t1.json"
S1=$(grep -o '"sessionID":"ses_[A-Za-z0-9]*"' "$EV_LOG" | head -1 | cut -d'"' -f4)
S2=$(sid_of "$SANDBOX/t1.json")
ev "session 1 (KIWI) = $S1 ; session 2 (LIME) = $S2"
ev "## recall with -s <session 1>"
ev_run "$OC" run -m "$MODEL" --format json -s "$S1" "What was the secret token I gave you earlier? Reply with only the token."
ev "## recall with --continue (continues the most recent session = session 2)"
ev_run "$OC" run -m "$MODEL" --format json --continue "What was the secret token I gave you earlier? Reply with only the token."
ev "## session list"
ev_run "$OC" session list --format json
k_ok=$(grep -c '"text":"KIWI-4417"' "$EV_LOG"); l_ok=$(grep -c '"text":"LIME-0082"' "$EV_LOG")
s1_reused=$(grep -c "\"sessionID\":\"$S1\"" "$EV_LOG")
if [ "$k_ok" -ge 1 ] && [ "$l_ok" -ge 1 ] && [ "$s1_reused" -ge 4 ]; then
  ev_verdict PASS "run --format json emits JSON events with a stable sessionID; -s <id> and --continue resume the right session and the model recalls the planted token."
else
  ev_verdict FAIL "recall kiwi=$k_ok lime=$l_ok s1_events=$s1_reused."
fi

# ======================= 2. kill -9 a run mid-generation =======================
ev_open P1-headless/run1/crash-run.log "kill -9 an opencode run mid-generation, then continue the same session id" "$MODEL (thinking off)"
ev "session under test: $S1 (has KIWI-4417 planted)"
ev "## start a long generation on $S1 and kill -9 it after 10s"
"$OC" run -m "$MODEL" --format json -s "$S1" "$LONG" > "$SANDBOX/crash-run.json" 2>&1 &
RPID=$!
sleep 10
ev "process $RPID alive before kill: $(kill -0 $RPID 2>/dev/null && echo yes || echo no)"
kill -9 "$RPID"; wait "$RPID" 2>/dev/null
ev "kill -9 $RPID sent at $(date -u +%H:%M:%S)"
ev_run cat "$SANDBOX/crash-run.json"
sleep 2
ev "## proxy: the in-flight LLM request was cut (client_disconnected=true)"
ev_run sh -c "tail -2 '$EVD/proxy.jsonl' | jq -c '{ts, model: .request.model, last_user: .request.last_user[0:60], client_disconnected, wall_s, completion_tokens: .response.usage.completion_tokens}'"
ev "## export the session after the crash"
"$OC" export "$S1" > "$SANDBOX/export-after-crash.json" 2>"$SANDBOX/export.err"
ev_run sh -c "jq -c '.messages[] | {role: .info.role, finish: .info.finish, completed: .info.time.completed, error: (.info.error.name // null), text: ([.parts[] | select(.type==\"text\") | .text] | join(\" \") | .[0:100])}' '$SANDBOX/export-after-crash.json'"
ev "## continue the same session id after the crash"
ev_run "$OC" run -m "$MODEL" --format json -s "$S1" "Stop counting. What was the secret token I gave you at the very beginning? Reply with only the token."
ok=$(grep -c '"text":"KIWI-4417"' "$EV_LOG")
cut_seen=$(tail -3 "$EVD/proxy.jsonl" | grep -c '"client_disconnected": true')
if [ "$ok" -ge 1 ]; then
  ev_verdict PASS "after kill -9 mid-generation the session history survived on disk (export shows it) and -s <id> continued it; the model recalled KIWI-4417 (in-flight request cut: $cut_seen)."
else
  ev_verdict FAIL "token not recalled after crash."
fi

# ======================= 3. kill -9 serve mid-generation =======================
ev_open P1-headless/run1/crash-serve.log "kill -9 opencode serve mid-generation, restart, read the session over HTTP, continue it" "$MODEL (thinking off)"
start_serve || ev "serve did not start"
SS=$($DRIVE new --title p1-serve-crash)
ev "session: $SS"
ev_run $DRIVE turn --session "$SS" --model "$MODEL" --text "Remember this secret token: MANGO-2290. Reply only with the word OK."
ev "## long generation via prompt_async, then kill -9 the server after 10s"
ev_run jcurl -o /dev/null -w 'prompt_async HTTP %{http_code}\n' -X POST "$BASE/session/$SS/prompt_async" -d "{\"model\":$MJSON,\"parts\":[{\"type\":\"text\",\"text\":\"$LONG\"}]}"
sleep 10
ev_run curl -s "$BASE/session/status"
kill -9 "$SPID"; wait "$SPID" 2>/dev/null
ev "kill -9 serve pid $SPID at $(date -u +%H:%M:%S); health now: $(curl -s -m 2 -o /dev/null -w '%{http_code}' "$BASE/global/health")"
sleep 2
ev "## restart serve on the same port and read the session back"
start_serve || ev "serve did not restart"
ev_run curl -s "$BASE/session/$SS"
ev_run msgs "$SS"
ev_run curl -s "$BASE/session/status"
ev "## continue the session over HTTP"
ev_run $DRIVE turn --session "$SS" --model "$MODEL" --text "Stop counting. What was the secret token I gave you at the very beginning? Reply with only the token."
ok=$(grep -c '"final_text": "MANGO-2290"' "$EV_LOG")
if [ "$ok" -ge 1 ]; then
  ev_verdict PASS "after kill -9 of serve mid-generation, a restarted serve returned the session and its history over HTTP and continued it (model recalled MANGO-2290)."
else
  ev_verdict FAIL "token not recalled after serve crash."
fi

# ======================= 4. busy session =======================
ev_open P1-headless/run1/busy.log "what a second prompt does while the session is still generating (decides Telegram responsiveness)" "$MODEL (thinking off)"
SB=$($DRIVE new --title p1-busy)
ev "session: $SB"
ev_run $DRIVE turn --session "$SB" --model "$MODEL" --text "Remember this secret token: PEAR-5521. Reply only with the word OK."

ev "## 4a. prompt_async #1 (long) then prompt_async #2 three seconds later"
jcurl -o /dev/null -X POST "$BASE/session/$SB/prompt_async" -d "{\"model\":$MJSON,\"parts\":[{\"type\":\"text\",\"text\":\"$LONG\"}]}"
sleep 3
ev_run curl -s "$BASE/session/status"
ev_run jcurl -o /dev/null -w 'second prompt_async -> HTTP %{http_code} in %{time_total}s\n' -X POST "$BASE/session/$SB/prompt_async" -d "{\"model\":$MJSON,\"parts\":[{\"type\":\"text\",\"text\":\"Reply with exactly SECOND-PROMPT-OK and nothing else.\"}]}"
for i in $(seq 1 600); do st=$(curl -s "$BASE/session/status" | jq -c --arg s "$SB" '.[$s] // "idle"'); [ "$st" = '"idle"' ] && break; sleep 0.5; done
ev "idle after ~$((i/2))s"
ev_run msgs "$SB"

ev "## 4b. sync POST /message while busy"
jcurl -o /dev/null -X POST "$BASE/session/$SB/prompt_async" -d "{\"model\":$MJSON,\"parts\":[{\"type\":\"text\",\"text\":\"$LONG\"}]}"
sleep 3
ev_run jcurl -o "$SANDBOX/sync-while-busy.json" -w 'sync /message while busy -> HTTP %{http_code} after %{time_total}s\n' -X POST "$BASE/session/$SB/message" -d "{\"model\":$MJSON,\"parts\":[{\"type\":\"text\",\"text\":\"Reply with exactly THIRD-SYNC-OK and nothing else.\"}]}"
ev_run sh -c "jq -c '{role: .info.role, finish: .info.finish, text: ([.parts[]? | select(.type==\"text\") | .text] | join(\" \") | .[0:200])}' '$SANDBOX/sync-while-busy.json' 2>/dev/null || head -c 600 '$SANDBOX/sync-while-busy.json'"
for i in $(seq 1 600); do st=$(curl -s "$BASE/session/status" | jq -c --arg s "$SB" '.[$s] // "idle"'); [ "$st" = '"idle"' ] && break; sleep 0.5; done
ev_run msgs "$SB"

ev "## 4c. v2 API while busy: POST /api/session/{id}/prompt delivery=queue and delivery=steer"
jcurl -o /dev/null -X POST "$BASE/session/$SB/prompt_async" -d "{\"model\":$MJSON,\"parts\":[{\"type\":\"text\",\"text\":\"$LONG\"}]}"
sleep 3
ev_run jcurl -w '\n-> HTTP %{http_code} in %{time_total}s\n' -X POST "$BASE/api/session/$SB/prompt" -d '{"prompt":{"text":"Reply with exactly V2-QUEUE-OK and nothing else."},"delivery":"queue"}'
ev_run jcurl -w '\n-> HTTP %{http_code} in %{time_total}s\n' -X POST "$BASE/api/session/$SB/prompt" -d '{"prompt":{"text":"Reply with exactly V2-STEER-OK and nothing else."},"delivery":"steer"}'
ev "## 4d. a second, independent CLI process on the same busy session (no --attach: writes the same DB directly)"
ev_run "$OC" run -m "$MODEL" --format json -s "$SB" "Reply with exactly CLI-WHILE-BUSY-OK and nothing else."
ev "## 4e. run --attach to the server on the same busy session"
ev_run "$OC" run --attach "$BASE" -m "$MODEL" -s "$SB" "Reply with exactly ATTACH-WHILE-BUSY-OK and nothing else."
for i in $(seq 1 600); do st=$(curl -s "$BASE/session/status" | jq -c --arg s "$SB" '.[$s] // "idle"'); [ "$st" = '"idle"' ] && break; sleep 0.5; done
ev_run msgs "$SB"

ev "## 4f. abort a busy session (Telegram /stop analog): POST /session/{id}/abort"
jcurl -o /dev/null -X POST "$BASE/session/$SB/prompt_async" -d "{\"model\":$MJSON,\"parts\":[{\"type\":\"text\",\"text\":\"$LONG\"}]}"
sleep 4
ev_run curl -s "$BASE/session/status"
ev_run jcurl -w '\n-> HTTP %{http_code} in %{time_total}s\n' -X POST "$BASE/session/$SB/abort"
sleep 1
ev_run curl -s "$BASE/session/status"
ev_run sh -c "msgs_last() { :; }; curl -s '$BASE/session/$SB/message' | jq -c '.[-1] | {role: .info.role, finish: .info.finish, error: (.info.error.name // null), completed: .info.time.completed, text_len: ([.parts[] | select(.type==\"text\") | .text] | join(\"\") | length)}'"
ev "## recall after all of the above"
ev_run $DRIVE turn --session "$SB" --model "$MODEL" --text "What was the secret token I gave you at the very beginning of this conversation? Reply with only the token."
kill "$SPID" 2>/dev/null; wait "$SPID" 2>/dev/null
proxy_stop "$PROXY_PORT"
ev_verdict PARTIAL "observational probe; behaviour per mode is summarized in SUMMARY.md from the raw transcript above."
for f in run-session crash-run crash-serve busy; do ev_redact "$OCSOMA_EVID/P1-headless/run1/$f.log"; tail -1 "$OCSOMA_EVID/P1-headless/run1/$f.log"; done
