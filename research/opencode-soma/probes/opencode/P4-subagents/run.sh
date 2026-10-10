#!/usr/bin/env bash
# P4 sub-agents:
#  a. primary -> custom subagent via the task tool; parent/child linkage over HTTP + export
#  b. nesting: subagent -> subagent with the default config, then with subagent_depth=2
#  c. two task calls in one response: concurrent or sequential? (bash `sleep 15` in each child)
#  d. blocking vs background: foreground task blocks the turn; background=true (experimental flag)
#     returns at once, the parent stays responsive, the result is injected later; plus
#     POST /experimental/session/{id}/background on a running foreground task
#  e. an independent opencode process (own cwd, own data dir) whose primary uses subagents
set -u
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P4-subagents/run.sh"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROXY_PORT=$(( ${OCSOMA_PORT_BASE:-47100} + 45 )); SERVE_PORT=$(( ${OCSOMA_PORT_BASE:-47100} + 41 )); LEAD_PORT=$(( ${OCSOMA_PORT_BASE:-47100} + 42 )); LEAD_PROXY=$(( ${OCSOMA_PORT_BASE:-47100} + 46 ))
PROBE_ID=P4-subagents; SANDBOX_FRESH=1; OC_BASEURL="http://127.0.0.1:$PROXY_PORT/v1"
source "$HERE/../sandbox.sh"
cd "$PROJ" || exit 1
MODEL=ollama/qwen3:32b
EVD="$OCSOMA_EVID/P4-subagents"; mkdir -p "$EVD"
BASE="http://127.0.0.1:$SERVE_PORT"
DRIVE="python3.12 -I $OCSOMA_PROBES/ocdrive.py --base $BASE"
write_cfg() {  # write_cfg <subagent_depth>
  cat > "$PROJ/opencode.json" <<JSON
{
  "\$schema": "https://opencode.ai/config.json",
  "subagent_depth": $1,
  "agent": {
    "researcher": { "mode": "subagent", "description": "Returns a fixed token for any task.",
      "prompt": "You are the researcher subagent. Reply with exactly RESEARCHER-DONE and nothing else. Never call tools." },
    "nester": { "mode": "subagent", "description": "Delegates its task to the researcher subagent.",
      "prompt": "You are the nester subagent. You MUST call the task tool with subagent_type researcher, description nested research, prompt do research. Then reply with exactly NESTER-GOT: followed by what the researcher returned.",
      "permission": { "task": "allow" } },
    "slowpoke": { "mode": "subagent", "description": "Runs the given slow shell command and reports its output.",
      "prompt": "You are slowpoke. Use the bash tool to run exactly the command you are given, then reply with its output only." }
  }
}
JSON
}
start_serve() { "$@" "$OC" serve --port "$SERVE_PORT" --hostname 127.0.0.1 >> "$SANDBOX/serve.out" 2>&1 & SPID=$!; for i in $(seq 1 100); do curl -s -o /dev/null "$BASE/global/health" && return 0; sleep 0.2; done; return 1; }
tree_dump() {  # tree_dump <root session>: every session in the tree with parentID, agent, times, tool parts
  python3.12 -I - "$BASE" "$1" "$OCSOMA_PROBES" <<'PY'
import json, sys
sys.path.insert(0, sys.argv[3])
import ocdrive
api = ocdrive.Api(sys.argv[1])
for sid in ocdrive.tree(api, sys.argv[2]):
    s = api.req("GET", f"/session/{sid}")
    print(json.dumps({"session": sid, "parentID": s.get("parentID"), "title": s.get("title"), "agent": s.get("agent"),
                      "created": s["time"]["created"], "updated": s["time"]["updated"]}))
    for m in ocdrive.compact_msgs(api, sid):
        for t in m["tools"]:
            print("   tool", json.dumps({k: t[k] for k in ("tool", "status", "ms", "child_session")}), json.dumps(t["input"])[:160], "->", t["output_head"][:160].replace("\n", " "))
        if m["role"] == "assistant" and m["text"]:
            print("   text", json.dumps(m["text"][:200]), "completed", m["completed"])
PY
}
: > "$EVD/proxy.jsonl"
proxy_start "$PROXY_PORT" "$EVD/proxy.jsonl" || exit 1
write_cfg 1
git -C "$PROJ" add -A >/dev/null; git -C "$PROJ" commit -qm "p4 config" >/dev/null
start_serve env || exit 1

# ======================= a. basic =======================
ev_open P4-subagents/basic.log "primary invokes a custom subagent through the task tool; child session linkage" "$MODEL (thinking off)"
ev_run cat "$PROJ/opencode.json"
SA=$($DRIVE new --title p4-basic)
ev_run $DRIVE turn --session "$SA" --model "$MODEL" --perm once --text "Use the task tool with subagent_type researcher, description quick research, prompt find facts. Then reply with exactly what it returned."
ev "## session tree over HTTP (GET /session/{id}/children, recursively)"
ev_run tree_dump "$SA"
ev "## export of the child (parentID linkage persisted)"
CH=$(curl -s "$BASE/session/$SA/children" | jq -r '.[0].id // empty')
ev_run sh -c "'$OC' export '$CH' 2>/dev/null | jq -c '{id: .info.id, parentID: .info.parentID, title: .info.title, n_messages: (.messages|length)}'"
ok=$(curl -s "$BASE/session/$CH" | jq -r --arg p "$SA" 'select(.parentID==$p) | .id')
if [ -n "$CH" ] && [ -n "$ok" ] && grep -q 'RESEARCHER-DONE' "$EV_LOG"; then
  ev_verdict PASS "task tool created child session $CH with parentID=$SA (visible via /children, /session/{id} and export) and returned RESEARCHER-DONE."
else
  ev_verdict FAIL "no linked child session (child=$CH)."
fi

# ======================= b. nesting =======================
ev_open P4-subagents/nesting.log "can a subagent spawn its own subagent? default config vs subagent_depth=2" "$MODEL (thinking off)"
SN=$($DRIVE new --title p4-nest-default)
ev "## b1. default (subagent_depth=1): primary -> nester -> researcher"
ev_run $DRIVE turn --session "$SN" --model "$MODEL" --perm once --text "Use the task tool with subagent_type nester, description nest test, prompt go. Then reply with exactly what it returned."
ev_run tree_dump "$SN"
kill "$SPID" 2>/dev/null; wait "$SPID" 2>/dev/null
write_cfg 2
start_serve env || exit 1
SN2=$($DRIVE new --title p4-nest-depth2)
ev "## b2. subagent_depth=2 (config), same request"
ev_run $DRIVE turn --session "$SN2" --model "$MODEL" --perm once --text "Use the task tool with subagent_type nester, description nest test, prompt go. Then reply with exactly what it returned."
ev_run tree_dump "$SN2"
d1=$(python3.12 -I "$OCSOMA_PROBES/ocdrive.py" --base "$BASE" children --session "$SN2" | jq -r '.[0].id // empty')
d2=""; [ -n "$d1" ] && d2=$(python3.12 -I "$OCSOMA_PROBES/ocdrive.py" --base "$BASE" children --session "$d1" | jq -r '.[0].id // empty')
ev "depth-2 tree: root=$SN2 child=$d1 grandchild=$d2"
if [ -n "$d2" ] && grep -q 'Subagent depth limit reached' "$EV_LOG"; then
  ev_verdict PASS "nesting is blocked by default (Subagent depth limit reached (1)) and works with subagent_depth=2 (+ task permission on the subagent): grandchild $d2."
elif [ -n "$d2" ]; then
  ev_verdict PASS "with subagent_depth=2 a subagent spawned its own subagent (grandchild $d2); the default-config refusal text was not observed in this run."
else
  ev_verdict PARTIAL "no grandchild session was created (child=$d1); see tool outputs above."
fi

# ======================= c. parallel =======================
ev_open P4-subagents/parallel.log "two task calls in one assistant message: concurrent or sequential?" "$MODEL (thinking off)"
SP=$($DRIVE new --title p4-parallel)
ev_run $DRIVE turn --session "$SP" --model "$MODEL" --perm once --timeout 400 --text "In ONE response, emit TWO task tool calls at the same time (parallel tool calls): first with subagent_type slowpoke, description slow one, prompt 'Run: sleep 15 && echo SLOW-ONE-DONE'; second with subagent_type slowpoke, description slow two, prompt 'Run: sleep 15 && echo SLOW-TWO-DONE'. After both return, reply with both outputs."
ev_run tree_dump "$SP"
ev "## overlap analysis of the two children (session created/updated, ms)"
ev_run python3.12 -I - "$BASE" "$SP" "$OCSOMA_PROBES" <<'PY'
import json, sys
sys.path.insert(0, sys.argv[3])
import ocdrive
api = ocdrive.Api(sys.argv[1])
kids = api.req("GET", f"/session/{sys.argv[2]}/children") or []
spans = []
for k in kids:
    msgs = ocdrive.compact_msgs(api, k["id"])
    end = max((m["completed"] or 0) for m in msgs) if msgs else 0
    spans.append((k["time"]["created"], end, k["title"]))
spans.sort()
for s in spans: print("child", s[2], "start", s[0], "end", s[1], "dur_s", round((s[1]-s[0])/1000, 1))
if len(spans) >= 2:
    a, b = spans[0], spans[1]
    print("OVERLAP" if b[0] < a[1] else "SEQUENTIAL", "| second started", round((b[0]-a[0])/1000, 1), "s after first; first ended", round((a[1]-a[0])/1000, 1), "s after its start")
else:
    print("fewer than 2 children:", len(spans))
PY
if grep -q '^OVERLAP' "$EV_LOG"; then
  ev_verdict PASS "two task calls from one message ran concurrently (child sessions overlap in time)."
elif grep -q '^SEQUENTIAL' "$EV_LOG"; then
  ev_verdict PARTIAL "the model emitted two task calls but they ran one after the other (no overlap)."
else
  ev_verdict FAIL "the model did not produce two task calls in one response."
fi
kill "$SPID" 2>/dev/null; wait "$SPID" 2>/dev/null

# ======================= d. blocking vs background =======================
ev_open P4-subagents/background.log "is the task tool blocking? background=true (OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS) and /experimental/session/{id}/background" "$MODEL (thinking off)"
write_cfg 1
start_serve env OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true || exit 1
ev "serve restarted with OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS=true"
ev "## d0. the task tool schema the model gets now (background param present?)"
ev_run sh -c "curl -s '$BASE/experimental/tool?provider=ollama&model=qwen3:32b' | jq -c '.[] | select(.id==\"task\") | {id, params: (.parameters.properties | keys)}'"
SF=$($DRIVE new --title p4-foreground)
ev "## d1. foreground task (sleep 20): the parent turn is blocked until the child finishes"
ev_run $DRIVE turn --session "$SF" --model "$MODEL" --perm once --timeout 300 --text "Use the task tool (foreground, no background flag) with subagent_type slowpoke, description fg job, prompt 'Run: sleep 20 && echo FG-JOB-DONE'. Then reply with what it returned."
SB=$($DRIVE new --title p4-background)
ev "## d2. background=true task (sleep 25): turn should end at once"
T0=$(date +%s)
ev_run $DRIVE turn --session "$SB" --model "$MODEL" --perm once --timeout 300 --text "Call the task tool exactly once with background set to true, subagent_type slowpoke, description bg job, prompt 'Run: sleep 25 && echo BG-JOB-DONE'. As soon as it is launched, reply with exactly LAUNCHED and end your turn."
ev "parent turn returned after $(( $(date +%s) - T0 ))s"
ev "## d3. parent responsiveness while the background child still runs"
ev_run $DRIVE turn --session "$SB" --model "$MODEL" --perm once --timeout 120 --text "Reply with exactly STILL-RESPONSIVE and nothing else."
ev "elapsed since launch: $(( $(date +%s) - T0 ))s"
ev "## d4. wait for the background result to be injected into the parent (synthetic message + new parent turn)"
for i in $(seq 1 120); do
  inj=$(curl -s "$BASE/session/$SB/message" | jq '[.[] | select(.info.role=="user") | .parts[] | select(.type=="text" and (.text|test("<task id=")))] | length')
  [ "${inj:-0}" -ge 1 ] && break; sleep 1
done
for i in $(seq 1 240); do st=$(curl -s "$BASE/session/status" | jq -c --arg s "$SB" '.[$s] // "idle"'); [ "$st" = '"idle"' ] && break; sleep 0.5; done
ev "injected task-result messages: $inj (after $(( $(date +%s) - T0 ))s)"
ev_run sh -c "curl -s '$BASE/session/$SB/message' | jq -c '.[] | {role: .info.role, created: .info.time.created, text: ([.parts[] | select(.type==\"text\") | ((if .synthetic then \"[synthetic] \" else \"\" end) + .text)] | join(\" \") | .[0:220])}'"
ev_run tree_dump "$SB"
ev "## d5. POST /experimental/session/{id}/background on a running FOREGROUND task"
SX=$($DRIVE new --title p4-detach)
curl -s -o /dev/null -H 'content-type: application/json' -X POST "$BASE/session/$SX/prompt_async" -d '{"model":{"providerID":"ollama","modelID":"qwen3:32b"},"parts":[{"type":"text","text":"Use the task tool (foreground) with subagent_type slowpoke, description detach job, prompt '"'"'Run: sleep 30 && echo DETACH-JOB-DONE'"'"'. Then reply with what it returned."}]}'
for i in $(seq 1 120); do n=$(curl -s "$BASE/session/$SX/children" | jq 'length'); [ "${n:-0}" -ge 1 ] && break; sleep 0.5; done
sleep 3
TD=$(date +%s)
ev_run curl -s -w '\n-> HTTP %{http_code}\n' -X POST "$BASE/experimental/session/$SX/background"
for i in $(seq 1 120); do st=$(curl -s "$BASE/session/status" | jq -c --arg s "$SX" '.[$s] // "idle"'); [ "$st" = '"idle"' ] && break; sleep 0.5; done
ev "parent idle $(( $(date +%s) - TD ))s after the detach call (child sleeps 30s)"
sleep 40
ev_run sh -c "curl -s '$BASE/session/$SX/message' | jq -c '.[] | {role: .info.role, created: .info.time.created, text: ([.parts[] | select(.type==\"text\") | ((if .synthetic then \"[synthetic] \" else \"\" end) + .text)] | join(\" \") | .[0:200]), tools: [.parts[] | select(.type==\"tool\") | {tool, status: .state.status, out: ((.state.output // .state.error // \"\")|.[0:160])}]}'"
kill "$SPID" 2>/dev/null; wait "$SPID" 2>/dev/null
bg_fast=$(grep -o 'parent turn returned after [0-9]*s' "$EV_LOG" | grep -o '[0-9]*' | head -1)
resp=$(grep -c '"final_text": "STILL-RESPONSIVE"' "$EV_LOG")
if [ "${bg_fast:-999}" -lt 25 ] && [ "$resp" -ge 1 ] && [ "${inj:-0}" -ge 1 ]; then
  ev_verdict PASS "foreground task blocks the caller's turn; background=true (experimental flag) returned in ${bg_fast}s, the parent answered another prompt while the child ran, and the result was injected later as a synthetic <task> message."
else
  ev_verdict PARTIAL "background evidence incomplete: parent returned after ${bg_fast:-?}s, responsive=$resp, injected=${inj:-0} (see transcript)."
fi

# ======================= e. independent lead process =======================
ev_open P4-subagents/lead-team.log "an independent opencode process (own cwd + data dir) whose primary agent uses subagents" "$MODEL (thinking off)"
LEAD_SB="$OCSOMA_REAL_TMPDIR/ocsoma-pe/P4-lead-b"
(
  unset SANDBOX; PROBE_ID=P4-lead-b; SANDBOX_FRESH=1; OC_BASEURL="http://127.0.0.1:$LEAD_PROXY/v1"
  source "$HERE/../sandbox.sh"
  : > "$EVD/lead-proxy.jsonl"
  proxy_start "$LEAD_PROXY" "$EVD/lead-proxy.jsonl"
  cd "$PROJ" && exec "$OC" serve --port "$LEAD_PORT" --hostname 127.0.0.1
) > "$SANDBOX/lead-serve.out" 2>&1 &
LPID=$!
LB="http://127.0.0.1:$LEAD_PORT"
for i in $(seq 1 100); do curl -s -o /dev/null "$LB/global/health" && break; sleep 0.2; done
ev "lead process pid $LPID, sandbox \$TMPDIR/ocsoma-pe/P4-lead-b (own HOME/XDG data/config, own git project)"
ev_run curl -s "$LB/path"
ev_run sh -c "lsof -p \$(lsof -t -nP -iTCP:$LEAD_PORT -sTCP:LISTEN) | awk '{print \$9}' | grep -E 'opencode.db\$|/project\$' | sort -u"
LS=$(python3.12 -I "$OCSOMA_PROBES/ocdrive.py" --base "$LB" new --title lead-team)
ev_run python3.12 -I "$OCSOMA_PROBES/ocdrive.py" --base "$LB" turn --session "$LS" --model "$MODEL" --perm once --text "You are a project lead. Delegate to your team: use the task tool with subagent_type general, description team member, prompt 'Reply with exactly TEAM-MEMBER-REPORTING'. Then reply with exactly what your team member returned."
ev_run sh -c "curl -s '$LB/session/$LS/children' | jq -c '.[] | {id, parentID, title, directory}'"
lc=$(curl -s "$LB/session/$LS/children" | jq 'length')
kill "$LPID" 2>/dev/null; wait "$LPID" 2>/dev/null
pkill -f "ollama_log_proxy.py --port $LEAD_PROXY" 2>/dev/null
if [ "${lc:-0}" -ge 1 ] && grep -q 'TEAM-MEMBER-REPORTING' "$EV_LOG"; then
  ev_verdict PASS "an independent opencode serve (own data dir and project) ran a primary that spawned a subagent team member (child session listed on its own API)."
else
  ev_verdict FAIL "independent process did not spawn a child session (children=$lc)."
fi
proxy_stop "$PROXY_PORT"
for f in basic nesting parallel background lead-team; do ev_redact "$EVD/$f.log"; tail -1 "$EVD/$f.log"; done
