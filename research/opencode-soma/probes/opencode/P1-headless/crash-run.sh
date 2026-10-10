#!/usr/bin/env bash
# P1.2 kill -9 an `opencode run` mid-generation, then continue the same session id.
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P1-headless/crash-run.sh"
P1_PART=crashrun; P1_PROXY=$(( ${OCSOMA_PORT_BASE:-47100} + 16 )); P1_SERVE=$(( ${OCSOMA_PORT_BASE:-47100} + 12 ))
source "$(dirname "$0")/common.sh"
ev_open P1-headless/crash-run.log "kill -9 an opencode run mid-generation, then continue the same session id" "$MODEL (thinking off)"
"$OC" run -m "$MODEL" --format json "Remember this secret token: KIWI-4417. Reply only with the word OK." > "$SANDBOX/plant.json" 2>&1
ev_run cat "$SANDBOX/plant.json"
S1=$(sid_of "$SANDBOX/plant.json")
ev "session under test: $S1 (KIWI-4417 planted)"
ev "## long generation on $S1, kill -9 after 10s"
"$OC" run -m "$MODEL" --format json -s "$S1" "$LONG" > "$SANDBOX/crash-run.json" 2>&1 &
RPID=$!
sleep 10
ev "process $RPID alive before kill: $(kill -0 $RPID 2>/dev/null && echo yes || echo no)"
kill -9 "$RPID"; wait "$RPID" 2>/dev/null
ev "kill -9 $RPID sent at $(date -u +%H:%M:%S)"
ev_run cat "$SANDBOX/crash-run.json"
sleep 2
ev "## proxy: the in-flight LLM request was cut (client_disconnected=true)"
ev_run sh -c "tail -1 '$PROXY_LOG' | jq -c '{ts, model: .request.model, last_user: .request.last_user[0:60], client_disconnected, wall_s, completion_tokens: .response.usage.completion_tokens}'"
ev "## export after the crash (the partial assistant message is persisted incomplete)"
"$OC" export "$S1" > "$SANDBOX/export-after-crash.json" 2>"$SANDBOX/export.err"
ev_run sh -c "jq -c '.messages[] | {role: .info.role, finish: .info.finish, completed: .info.time.completed, error: (.info.error.name // null), text: ([.parts[] | select(.type==\"text\") | .text] | join(\" \") | .[0:100])}' '$SANDBOX/export-after-crash.json'"
ev "## continue the same session id after the crash"
"$OC" run -m "$MODEL" --format json -s "$S1" "Stop counting. What was the secret token I gave you at the very beginning? Reply with only the token." > "$SANDBOX/after.json" 2>&1
ev_run cat "$SANDBOX/after.json"
ok=$(grep -c '"text":"KIWI-4417"' "$SANDBOX/after.json"); same=$(grep -c "\"sessionID\":\"$S1\"" "$SANDBOX/after.json")
cut_seen=$(tail -2 "$PROXY_LOG" | grep -c '"client_disconnected": true')
if [ "$ok" -ge 1 ] && [ "$same" -ge 1 ]; then
  ev_verdict PASS "after kill -9 mid-generation (in-flight request cut: $cut_seen) the history survived on disk and -s <same id> continued it; the model recalled KIWI-4417."
else
  ev_verdict FAIL "token not recalled after crash (ok=$ok same-session=$same)."
fi
proxy_stop "$P1_PROXY"; ev_redact "$EV_LOG"; tail -1 "$EV_LOG"
