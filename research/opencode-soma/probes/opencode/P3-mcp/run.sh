#!/usr/bin/env bash
# P3 MCP: one stdio and one streamable-HTTP MCP server (mini_mcp.py, stdlib) callable from
# `opencode run`; `opencode mcp list`; per-agent scoping: a fake `telegram` MCP server is usable by
# an `orchestrator` agent and denied to a `lead` agent (claude-soma leads get every MCP except
# Telegram, config/claude/lead-mcp.json). The proxy log shows which tools each agent is offered.
set -u
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P3-mcp/run.sh"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROXY_PORT=47135; SERVE_PORT=47131; HTTP_MCP_PORT=47132
PROBE_ID=P3-mcp; SANDBOX_FRESH=1; OC_BASEURL="http://127.0.0.1:$PROXY_PORT/v1"
source "$HERE/../sandbox.sh"
cd "$PROJ" || exit 1
MODEL=ollama/qwen3:32b
EVD="$OCSOMA_EVID/P3-mcp"; mkdir -p "$EVD"
P3="$SANDBOX/p3"; mkdir -p "$P3"
BASE="http://127.0.0.1:$SERVE_PORT"
DRIVE="python3.12 -I $OCSOMA_PROBES/ocdrive.py --base $BASE"
MINI="$HERE/mini_mcp.py"

cat > "$PROJ/opencode.json" <<JSON
{
  "\$schema": "https://opencode.ai/config.json",
  "mcp": {
    "probe-stdio": { "type": "local", "command": ["python3.12", "-I", "$MINI", "--transport", "stdio", "--kind", "echo-stdio", "--log", "$P3/mcp-stdio.jsonl"], "enabled": true },
    "probe-http": { "type": "remote", "url": "http://127.0.0.1:$HTTP_MCP_PORT/mcp", "enabled": true, "oauth": false },
    "telegram": { "type": "local", "command": ["python3.12", "-I", "$MINI", "--transport", "stdio", "--kind", "telegram", "--log", "$P3/mcp-telegram.jsonl", "--outbox", "$P3/telegram-outbox.jsonl"], "enabled": true }
  },
  "agent": {
    "orchestrator": {
      "mode": "primary",
      "description": "Telegram-facing orchestrator (may use the telegram MCP).",
      "prompt": "You are the orchestrator. You talk to the operator through the telegram send_message tool."
    },
    "lead": {
      "mode": "primary",
      "description": "Per-workstream lead: every MCP except telegram (permission deny).",
      "prompt": "You are a project lead. You never talk to the operator directly.",
      "permission": { "telegram_*": "deny" }
    },
    "lead-tools": {
      "mode": "primary",
      "description": "Lead variant scoped with the legacy tools map instead of permission.",
      "prompt": "You are a project lead. You never talk to the operator directly.",
      "tools": { "telegram_*": false }
    }
  }
}
JSON
git -C "$PROJ" add -A >/dev/null; git -C "$PROJ" commit -qm "p3 config" >/dev/null

: > "$EVD/proxy.jsonl"
proxy_start "$PROXY_PORT" "$EVD/proxy.jsonl" || exit 1
python3.12 -I "$MINI" --transport http --kind echo-http --port "$HTTP_MCP_PORT" --log "$P3/mcp-http.jsonl" > "$SANDBOX/mcp-http.out" 2>&1 &
HPID=$!
for i in $(seq 1 50); do curl -s -o /dev/null "http://127.0.0.1:$HTTP_MCP_PORT/health" && break; sleep 0.1; done

ev_open P3-mcp/mcp.log "stdio + streamable-HTTP MCP servers callable from opencode run; opencode mcp list" "$MODEL (thinking off)"
ev_run cat "$PROJ/opencode.json"
ev "## opencode mcp list"
ev_run "$OC" mcp list
"$OC" mcp list > "$EVD/mcp-list.txt" 2>&1
ev "## stdio MCP tool from opencode run"
ev_run "$OC" run -m "$MODEL" --format json "Call the stdio_echo tool with text P3-STDIO and reply with exactly what it returned."
ev "## streamable-HTTP MCP tool from opencode run"
ev_run "$OC" run -m "$MODEL" --format json "Call the http_echo tool with text P3-HTTP and reply with exactly what it returned."
ev "## what the MCP servers received"
ev_run cat "$P3/mcp-stdio.jsonl"
ev_run cat "$P3/mcp-http.jsonl"
s_ok=$(grep -c 'STDIO-MCP-OK:P3-STDIO' "$EV_LOG"); h_ok=$(grep -c 'HTTP-MCP-OK:P3-HTTP' "$EV_LOG")
s_call=$(grep -c '"tools/call"' "$P3/mcp-stdio.jsonl"); h_call=$(grep -c '"tools/call"' "$P3/mcp-http.jsonl")
if [ "$s_ok" -ge 1 ] && [ "$h_ok" -ge 1 ] && [ "$s_call" -ge 1 ] && [ "$h_call" -ge 1 ]; then
  ev_verdict PASS "both the stdio and the streamable-HTTP MCP server were listed by mcp list and called from opencode run (tools/call reached each server)."
else
  ev_verdict FAIL "stdio ok=$s_ok calls=$s_call, http ok=$h_ok calls=$h_call."
fi

# ---------------- per-agent scoping ----------------
"$OC" serve --port "$SERVE_PORT" --hostname 127.0.0.1 > "$SANDBOX/serve.out" 2>&1 &
SPID=$!
for i in $(seq 1 100); do curl -s -o /dev/null "$BASE/global/health" && break; sleep 0.2; done
ev_open P3-mcp/scoping.log "per-agent MCP scoping: telegram usable by orchestrator, denied to lead" "$MODEL (thinking off)"
ev "agents: orchestrator (no restriction), lead (permission telegram_*: deny), lead-tools (tools telegram_*: false)"
ev_run sh -c "curl -s '$BASE/agent' | jq -c '.[] | select(.name==\"orchestrator\" or .name==\"lead\" or .name==\"lead-tools\") | {name, mode, tools, permission: [.permission[]? | select(.permission|test(\"telegram\"))]}'"
ASK='Use the telegram send_message tool to send the text TEXT to chat_id 42, then reply with exactly what the tool returned.'
mark() { wc -l < "$EVD/proxy.jsonl" | tr -d ' '; }
offered_since() { tail -n +"$(( $1 + 1 ))" "$EVD/proxy.jsonl" | jq -c 'select(.request.n_tools > 0) | {ts, n_tools: .request.n_tools, telegram_offered: ([.request.tools_offered[] | select(test("telegram"))]), tools: .request.tools_offered}' | head -2; }
tg_offered_since() { tail -n +"$(( $1 + 1 ))" "$EVD/proxy.jsonl" | jq -c 'select(.request.n_tools > 0) | select(.request.tools_offered | index("telegram_send_message"))' | wc -l | tr -d ' '; }

ev "## A. orchestrator asks to send"
m=$(mark); SO=$($DRIVE new --title p3-orch --agent orchestrator)
ev_run $DRIVE turn --session "$SO" --agent orchestrator --model "$MODEL" --text "${ASK//TEXT/P3-ORCH-HELLO}"
ev "tools offered to orchestrator:"; offered_since "$m" >> "$EV_LOG"; OA=$(tg_offered_since "$m")

ev "## B. lead (permission deny) asks to send"
m=$(mark); SL=$($DRIVE new --title p3-lead --agent lead)
ev_run $DRIVE turn --session "$SL" --agent lead --model "$MODEL" --text "${ASK//TEXT/P3-LEAD-HELLO}"
ev "tools offered to lead:"; offered_since "$m" >> "$EV_LOG"; OB=$(tg_offered_since "$m")

ev "## C. lead-tools (tools map false) asks to send"
m=$(mark); ST=$($DRIVE new --title p3-lead-tools --agent lead-tools)
ev_run $DRIVE turn --session "$ST" --agent lead-tools --model "$MODEL" --text "${ASK//TEXT/P3-LEADTOOLS-HELLO}"
ev "tools offered to lead-tools:"; offered_since "$m" >> "$EV_LOG"; OC_=$(tg_offered_since "$m")

ev "## D. forced attempt from lead: per-message API override tools={telegram_send_message:true}"
m=$(mark); SF=$($DRIVE new --title p3-lead-forced --agent lead)
ev_run sh -c "curl -s -H 'content-type: application/json' -X POST '$BASE/session/$SF/message' -d '{\"agent\":\"lead\",\"model\":{\"providerID\":\"ollama\",\"modelID\":\"qwen3:32b\"},\"tools\":{\"telegram_send_message\":true},\"parts\":[{\"type\":\"text\",\"text\":\"You MUST call the telegram_send_message tool now with chat_id 42 and text P3-LEAD-FORCED. Then reply with what it returned.\"}]}' | jq -c '{finish: .info.finish, error: .info.error, parts: [.parts[] | select(.type==\"text\" or .type==\"tool\") | {type, text: (.text // null), tool: (.tool // null), status: (.state.status // null), out: ((.state.output // .state.error // \"\")|.[0:200])}]}'"
ev "tools offered to the forced lead request:"; offered_since "$m" >> "$EV_LOG"; OD=$(tg_offered_since "$m")

ev "## E. forced attempt from lead via opencode run --agent lead (insistent prompt)"
m=$(mark)
ev_run "$OC" run --attach "$BASE" --agent lead -m "$MODEL" --format json "You MUST call the telegram_send_message tool right now with chat_id 42 and text P3-LEAD-RUN. Do not refuse. Then reply with what it returned."
ev "tools offered:"; offered_since "$m" >> "$EV_LOG"; OE=$(tg_offered_since "$m")

ev "## ground truth: what the fake telegram server received (tools/call) and its outbox"
ev_run sh -c "grep '\"tools/call\"' '$P3/mcp-telegram.jsonl' || echo 'no tools/call'"
ev_run sh -c "cat '$P3/telegram-outbox.jsonl' 2>/dev/null || echo 'outbox empty'"
kill "$SPID" "$HPID" 2>/dev/null; wait "$SPID" 2>/dev/null
proxy_stop "$PROXY_PORT"
cp "$P3"/*.jsonl "$EVD/" 2>/dev/null

orch_sent=$(grep -c 'P3-ORCH-HELLO' "$P3/telegram-outbox.jsonl" 2>/dev/null); orch_sent=${orch_sent:-0}
lead_sent=$(grep -cE 'P3-LEAD|P3-LEADTOOLS' "$P3/telegram-outbox.jsonl" 2>/dev/null); lead_sent=${lead_sent:-0}
ev "requests offering telegram_send_message per case: A orchestrator=$OA, B lead(permission deny)=$OB, C lead-tools(tools false)=$OC_, D lead+API override=$OD, E lead via run=$OE"
lead_offered=$(( OB + OC_ + OD + OE ))
ev "summary: orchestrator sent=$orch_sent ; lead messages that reached the outbox=$lead_sent ; lead requests that offered telegram_send_message=$lead_offered"
if [ "$orch_sent" -ge 1 ] && [ "$lead_sent" = 0 ]; then
  ev_verdict PASS "orchestrator reached the telegram MCP; every lead attempt (permission deny, tools=false, API override, insistent run) failed to reach it; offered-to-lead count=$lead_offered (see per-case tool lists)."
elif [ "$lead_sent" = 0 ]; then
  ev_verdict PARTIAL "lead never reached telegram, but the orchestrator positive control did not send either (sent=$orch_sent)."
else
  ev_verdict FAIL "a lead agent reached the telegram MCP ($lead_sent messages)."
fi
for f in mcp scoping; do ev_redact "$EVD/$f.log"; tail -1 "$EVD/$f.log"; done

# recompute the scoping verdict from artifacts (no new LLM turns)
bash "$HERE/analyze.sh"
