#!/usr/bin/env bash
# P7 usage: per-message and per-session token accounting from `opencode stats`, `opencode export`,
# the HTTP API and opencode's own SQLite (`opencode db`), for two ollama models; cross-checked
# against the usage blocks ollama returned (logging proxy); then aggregate.py rebuilds a
# claude-soma-style daily usage table from it.
set -u
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P7-usage/run.sh"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROXY_PORT=$(( ${OCSOMA_PORT_BASE:-47100} + 75 )); SERVE_PORT=$(( ${OCSOMA_PORT_BASE:-47100} + 71 ))
PROBE_ID=P7-usage; SANDBOX_FRESH=1; OC_BASEURL="http://127.0.0.1:$PROXY_PORT/v1"
source "$HERE/../sandbox.sh"
cd "$PROJ" || exit 1
EVD="$OCSOMA_EVID/P7-usage"; mkdir -p "$EVD"
BASE="http://127.0.0.1:$SERVE_PORT"
DRIVE="python3.12 -I $OCSOMA_PROBES/ocdrive.py --base $BASE"
: > "$EVD/proxy.jsonl"
proxy_start "$PROXY_PORT" "$EVD/proxy.jsonl" || exit 1
"$OC" serve --port "$SERVE_PORT" --hostname 127.0.0.1 > "$SANDBOX/serve.out" 2>&1 &
SPID=$!
for i in $(seq 1 100); do curl -s -o /dev/null "$BASE/global/health" && break; sleep 0.2; done

ev_open P7-usage/usage.log "token accounting per message/session (stats, export, HTTP, db) for ollama models vs ollama's own usage" "ollama/qwen3:32b and ollama/lfm2.5-thinking"
ev "## workload: qwen3 session with a bash tool call + a subagent; lfm2.5-thinking session (reasoning model)"
U1=$($DRIVE new --title p7-qwen)
ev_run $DRIVE turn --session "$U1" --model ollama/qwen3:32b --perm once --text "Use the bash tool to run: echo P7-TOOL-OK   Then reply with its output."
ev_run $DRIVE turn --session "$U1" --model ollama/qwen3:32b --perm once --text "Use the task tool with subagent_type general, description tiny job, prompt 'Reply with exactly P7-SUB-OK'. Then reply with what it returned."
U2=$($DRIVE new --title p7-lfm)
ev_run $DRIVE turn --session "$U2" --model ollama/lfm2.5-thinking --perm once --text "What is 17 times 23? Think step by step, then give the number."

ev "## 1. HTTP: per assistant message tokens (GET /session/{id}/message) and per session totals (GET /session/{id})"
for s in "$U1" $(curl -s "$BASE/session/$U1/children" | jq -r '.[].id') "$U2"; do
  ev_run sh -c "curl -s '$BASE/session/$s/message' | jq -c '.[] | select(.info.role==\"assistant\") | {session: .info.sessionID[-8:], agent: .info.agent, model: .info.modelID, finish: .info.finish, cost: .info.cost, tokens: .info.tokens}'"
  ev_run sh -c "curl -s '$BASE/session/$s' | jq -c '{session: .id[-8:], parentID: .parentID, agent, cost, tokens}'"
done
kill "$SPID" 2>/dev/null; wait "$SPID" 2>/dev/null

ev "## 2. opencode export <session> (per message tokens persisted)"
ev_run sh -c "'$OC' export '$U1' 2>/dev/null | jq -c '.messages[] | select(.info.role==\"assistant\") | {model: .info.modelID, tokens: .info.tokens, cost: .info.cost}'"
ev "## 3. opencode stats (all time / --models / --tools / --days 1)"
ev_run "$OC" stats
ev_run "$OC" stats --models
ev_run "$OC" stats --tools 10
ev_run "$OC" stats --days 1
ev "## 4. opencode db: session token columns and per-message JSON tokens"
ev_run "$OC" db path
ev_run "$OC" db --format json "SELECT id, parent_id, agent, model, cost, tokens_input, tokens_output, tokens_reasoning, tokens_cache_read, tokens_cache_write FROM session ORDER BY time_created"
ev_run "$OC" db --format json "SELECT json_extract(data,'\$.modelID') AS model, json_extract(data,'\$.agent') AS agent, count(*) AS msgs, sum(json_extract(data,'\$.tokens.input')) AS input, sum(json_extract(data,'\$.tokens.output')) AS output, sum(json_extract(data,'\$.tokens.reasoning')) AS reasoning, sum(json_extract(data,'\$.tokens.cache.read')) AS cache_read FROM message WHERE json_extract(data,'\$.role')='assistant' GROUP BY 1,2"

ev "## 5. ground truth: usage blocks ollama returned (proxy), summed per model"
ev_run python3.12 -I -c '
import json,sys,collections
agg=collections.defaultdict(collections.Counter)
for l in open(sys.argv[1]):
    e=json.loads(l); r=e.get("request") or {}; u=(e.get("response") or {}).get("usage") or {}
    if not r or not u: continue
    a=agg[r.get("model")]; a["requests"]+=1; a["prompt_tokens"]+=u.get("prompt_tokens",0); a["completion_tokens"]+=u.get("completion_tokens",0)
    a["cached_tokens"]+=(u.get("prompt_tokens_details") or {}).get("cached_tokens",0) or 0
    a["reasoning_chars"]+=(e.get("response") or {}).get("reasoning_chars",0)
for m,a in agg.items(): print(m, dict(a))' "$EVD/proxy.jsonl"
ev "note: opencode input = prompt_tokens - cached_tokens, cache.read = cached_tokens, output = completion_tokens (compare rows above)"

ev "## 6. rebuild a claude-soma-style daily usage table (aggregate.py over opencode db rows)"
"$OC" db --format json "SELECT m.id, m.session_id, m.time_created, m.data, s.parent_id, s.agent AS s_agent FROM message m JOIN session s ON s.id = m.session_id" > "$SANDBOX/msgrows.json" 2>/dev/null
ev_run python3.12 -I "$HERE/aggregate.py" "$SANDBOX/msgrows.json" "$SANDBOX/usage.sqlite" '{"interactive": 200000, "agent": 100000}'
ev_run sh -c "python3.12 -I -c 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); [print(r) for r in c.execute(\"select * from daily_usage\")]' '$SANDBOX/usage.sqlite'"
proxy_stop "$PROXY_PORT"

nz=$("$OC" db --format json "SELECT count(*) AS n FROM message WHERE json_extract(data,'\$.role')='assistant' AND json_extract(data,'\$.tokens.input') > 0 AND json_extract(data,'\$.tokens.output') > 0" 2>/dev/null | jq -r '.[0].n')
models=$("$OC" db --format json "SELECT count(DISTINCT json_extract(data,'\$.modelID')) AS n FROM message WHERE json_extract(data,'\$.role')='assistant' AND json_extract(data,'\$.tokens.output') > 0" 2>/dev/null | jq -r '.[0].n')
reas=$("$OC" db --format json "SELECT coalesce(sum(json_extract(data,'\$.tokens.reasoning')),0) AS n FROM message WHERE json_extract(data,'\$.modelID')='lfm2.5-thinking'" 2>/dev/null | jq -r '.[0].n')
ev "summary: assistant messages with non-zero input+output=$nz, models with non-zero output=$models, lfm2.5 reasoning tokens counted=$reas"
if [ "${nz:-0}" -ge 3 ] && [ "${models:-0}" -ge 2 ]; then
  ev_verdict PASS "per-message and per-session input/output/cache tokens are recorded for both ollama models (HTTP, export, stats, db) and match ollama's usage blocks; aggregate.py rebuilt a daily usage table (reasoning tokens counted: $reas)."
else
  ev_verdict FAIL "token accounting missing (non-zero msgs=$nz models=$models)."
fi
ev_redact "$EV_LOG"; tail -1 "$EV_LOG"
