#!/usr/bin/env bash
# P2 empirical: plugins spy/inject/gate/activity loaded from <project>/.opencode/plugin/, one session
# on `opencode serve` driven over HTTP (ocdrive.py). Turns: inject x2, gate (primary), subagent via
# task tool, MCP tool, permission "ask" answered over HTTP. Then four analysis logs with verdicts.
set -u
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P2-hooks/run.sh"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROXY_PORT=47125; SERVE_PORT=47121
PROBE_ID=P2-hooks; SANDBOX_FRESH=1; OC_BASEURL="http://127.0.0.1:$PROXY_PORT/v1"
source "$HERE/../sandbox.sh"
MODEL=ollama/qwen3:32b
EVD="$OCSOMA_EVID/P2-hooks"; mkdir -p "$EVD"
export P2_DIR="$SANDBOX/p2"; mkdir -p "$P2_DIR"
BASE="http://127.0.0.1:$SERVE_PORT"
DRIVE="python3.12 -I $OCSOMA_PROBES/ocdrive.py --base $BASE"

mkdir -p "$PROJ/.opencode/plugin"
cp "$HERE"/plugins/*.js "$PROJ/.opencode/plugin/"
cat > "$PROJ/opencode.json" <<JSON
{
  "\$schema": "https://opencode.ai/config.json",
  "permission": { "bash": { "*": "allow", "echo P2-ASK*": "ask" } },
  "mcp": {
    "probe-stdio": {
      "type": "local",
      "command": ["python3.12", "-I", "$OCSOMA_PROBES/P3-mcp/mini_mcp.py", "--transport", "stdio", "--kind", "echo-stdio", "--log", "$P2_DIR/mcp-stdio.jsonl"],
      "enabled": true
    }
  }
}
JSON
git -C "$PROJ" add -A >/dev/null; git -C "$PROJ" commit -qm "p2 config" >/dev/null

: > "$EVD/proxy.jsonl"
proxy_start "$PROXY_PORT" "$EVD/proxy.jsonl" || exit 1
( cd "$PROJ" && exec "$OC" serve --port "$SERVE_PORT" --hostname 127.0.0.1 ) > "$SANDBOX/serve.out" 2>&1 &
SPID=$!
for i in $(seq 1 100); do curl -s -o /dev/null "$BASE/global/health" && break; sleep 0.2; done
curl -sN "$BASE/event" > "$EVD/sse-event.txt" 2>&1 &
SSEPID=$!
sleep 1

ev_open P2-hooks/run.log "P2 driver transcript: plugins + MCP + subagent + permission in one served session" "$MODEL (thinking off)"
ev "plugins installed: $(ls "$PROJ/.opencode/plugin" | tr '\n' ' ')"
ev_run cat "$PROJ/opencode.json"
ev_run curl -s "$BASE/mcp"
SID=$($DRIVE new --title p2-main)
ev "session: $SID"

ev "## turn 1 (inject): files say MSG=ALPHA-111 SYS=ZULU-901"
echo "LATEST-MSG-CODE=ALPHA-111" > "$P2_DIR/inject-msg.txt"
echo "LATEST-SYS-CODE=ZULU-901" > "$P2_DIR/inject-sys.txt"
Q='Look at the [notify-inject] note attached to my message and the [notify-inject-system] note in your system prompt. Reply with exactly one line in the form MSG=<current value of LATEST-MSG-CODE> SYS=<current value of LATEST-SYS-CODE>, using the most recent values. Do not call any tools.'
ev_run $DRIVE turn --session "$SID" --model "$MODEL" --text "$Q"
ev "## turn 2 (inject): files rewritten to MSG=BRAVO-222 SYS=YANKEE-902 (same session)"
echo "LATEST-MSG-CODE=BRAVO-222" > "$P2_DIR/inject-msg.txt"
echo "LATEST-SYS-CODE=YANKEE-902" > "$P2_DIR/inject-sys.txt"
ev_run $DRIVE turn --session "$SID" --model "$MODEL" --text "$Q"
rm -f "$P2_DIR/inject-msg.txt" "$P2_DIR/inject-sys.txt"

ev "## turn 3 (gate, primary agent): bash touch sentinel must be blocked before it runs"
ev_run $DRIVE turn --session "$SID" --model "$MODEL" --perm once --text "Run exactly this bash command: touch $P2_DIR/sentinel-primary   Then tell me whether it succeeded and quote any error message verbatim."
ev_run ls -la "$P2_DIR"

ev "## turn 4 (subagent via task tool; gate exempts subagent sessions)"
ev_run $DRIVE turn --session "$SID" --model "$MODEL" --perm once --text "Use the task tool to launch the general subagent (subagent_type: general) with this prompt: 'Run exactly this bash command: touch $P2_DIR/sentinel-subagent && echo P2-SUB-DONE  and report its output.' When it finishes, tell me what the subagent reported."
ev_run ls -la "$P2_DIR"

ev "## turn 5 (MCP tool from the stdio server)"
ev_run $DRIVE turn --session "$SID" --model "$MODEL" --perm once --text "Call the stdio_echo tool with text P2-MCP and tell me exactly what it returned."

ev "## turn 6 (permission ask: bash 'echo P2-ASK*' is configured as ask; the driver replies once over HTTP)"
ev_run $DRIVE turn --session "$SID" --model "$MODEL" --perm once --text "You must call the bash tool now (do not answer from memory) to run exactly: echo P2-ASK-OK   Then tell me the output the tool returned."

ev "## session tree"
ev_run $DRIVE children --session "$SID"
ev "## ground-truth dump of every session in the tree (main + subagent children) -> sessions-dump.json"
python3.12 -I - "$BASE" "$SID" "$OCSOMA_PROBES" > "$EVD/sessions-dump.json" <<'PY'
import json, sys
sys.path.insert(0, sys.argv[3])
import ocdrive
api = ocdrive.Api(sys.argv[1])
print(json.dumps({s: ocdrive.compact_msgs(api, s) for s in ocdrive.tree(api, sys.argv[2])}, indent=1))
PY
ev_run sh -c "jq -c 'to_entries[] | {session: .key, tools: [.value[].tools[] | {tool, callID, status}]}' '$EVD/sessions-dump.json'"
kill "$SSEPID" 2>/dev/null
kill "$SPID" 2>/dev/null; wait "$SPID" 2>/dev/null
proxy_stop "$PROXY_PORT"
for f in spy activity gate inject mcp-stdio; do cp "$P2_DIR/$f.jsonl" "$EVD/$f.jsonl" 2>/dev/null; done
ev_redact "$EV_LOG"
ev_verdict PASS "driver transcript complete; see the four analysis logs for capability verdicts."

# ---------------- analysis (separate script so it can be re-run without new LLM turns) ----------------
bash "$HERE/analyze.sh"
