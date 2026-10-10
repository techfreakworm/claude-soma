#!/usr/bin/env bash
# P1.1 opencode run --format json: session ids stable and reusable via --continue and -s <id>.
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P1-headless/session.sh"
P1_PART=session; P1_PROXY=$(( ${OCSOMA_PORT_BASE:-47100} + 15 )); P1_SERVE=$(( ${OCSOMA_PORT_BASE:-47100} + 11 ))
source "$(dirname "$0")/common.sh"
ev_open P1-headless/run-session.log "opencode run --format json; session ids stable and reusable via --continue and -s" "$MODEL (thinking off)"
ev "## plant token KIWI-4417 in session 1"
"$OC" run -m "$MODEL" --format json "Remember this secret token: KIWI-4417. Reply only with the word OK." > "$SANDBOX/t1.json" 2>&1
ev_run cat "$SANDBOX/t1.json"
S1=$(sid_of "$SANDBOX/t1.json")
ev "## plant token LIME-0082 in session 2 (a new run = a new session)"
"$OC" run -m "$MODEL" --format json "Remember this second secret token: LIME-0082. Reply only with the word OK." > "$SANDBOX/t2.json" 2>&1
ev_run cat "$SANDBOX/t2.json"
S2=$(sid_of "$SANDBOX/t2.json")
ev "session 1 (KIWI) = $S1 ; session 2 (LIME) = $S2"
ev "## --continue: resumes the most recently updated session (session 2 at this point)"
"$OC" run -m "$MODEL" --format json --continue "What was the secret token I gave you earlier? Reply with only the token." > "$SANDBOX/t3.json" 2>&1
ev_run cat "$SANDBOX/t3.json"
ev "## -s <session 1>: resumes session 1 explicitly"
"$OC" run -m "$MODEL" --format json -s "$S1" "What was the secret token I gave you earlier? Reply with only the token." > "$SANDBOX/t4.json" 2>&1
ev_run cat "$SANDBOX/t4.json"
ev "## --continue again: session 1 is now the most recently updated one"
"$OC" run -m "$MODEL" --format json --continue "Repeat the secret token once more. Reply with only the token." > "$SANDBOX/t5.json" 2>&1
ev_run cat "$SANDBOX/t5.json"
ev "## session list"
ev_run "$OC" session list --format json
ev "## note: run wraps a single-argument prompt in literal double quotes (see last_user in the proxy log)"
ev_run sh -c "jq -r 'select(.request.n_tools > 0) | .request.last_user[0:80]' '$PROXY_LOG' | head -3"
c3=$(sid_of "$SANDBOX/t3.json"); c4=$(sid_of "$SANDBOX/t4.json"); c5=$(sid_of "$SANDBOX/t5.json")
r3=$(grep -c '"text":"LIME-0082"' "$SANDBOX/t3.json"); r4=$(grep -c '"text":"KIWI-4417"' "$SANDBOX/t4.json"); r5=$(grep -c '"text":"KIWI-4417"' "$SANDBOX/t5.json")
ev "checks: --continue#1 session=$c3 (want $S2) recall=$r3 ; -s session=$c4 (want $S1) recall=$r4 ; --continue#2 session=$c5 (want $S1) recall=$r5"
if [ "$c3" = "$S2" ] && [ "$c4" = "$S1" ] && [ "$c5" = "$S1" ] && [ "$r3" -ge 1 ] && [ "$r4" -ge 1 ] && [ "$r5" -ge 1 ]; then
  ev_verdict PASS "run --format json emits JSON events carrying a stable sessionID; --continue resumes the most recently updated session and -s <id> a chosen one, and the model recalls the planted tokens."
else
  ev_verdict FAIL "continue/session mapping or recall mismatch (see checks line)."
fi
proxy_stop "$P1_PROXY"; ev_redact "$EV_LOG"; tail -1 "$EV_LOG"
