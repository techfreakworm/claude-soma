#!/usr/bin/env bash
# P2 analysis: derives events.log, inject.log, gate.log, activity.log (each with a VERDICT) from the
# artifacts run.sh left in evidence/opencode/P2-hooks/ and the sandbox ($TMPDIR/ocsoma-pe/P2-hooks/p2).
# Safe to re-run without new LLM turns.
set -u
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P2-hooks/analyze.sh (after run.sh)"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROBE_ID=P2-hooks; source "$HERE/../sandbox.sh"
MODEL=ollama/qwen3:32b
EVD="$OCSOMA_EVID/P2-hooks"
P2_DIR="$SANDBOX/p2"
RUNLOG="$EVD/run.log"

turn_json() {  # turn_json <n>: the ocdrive JSON printed for "## turn <n>" in run.log
  python3.12 -I - "$RUNLOG" "$1" <<'PY'
import json, re, sys
s = open(sys.argv[1]).read()
n = sys.argv[2]
m = re.search(r"## turn " + n + r" .*?\n(\{\n.*?\n\})\n", s, re.S)
print(m.group(1) if m else "{}")
PY
}

# ---------------- events ----------------
ev_open P2-hooks/events.log "every hook and bus event a plugin actually saw during a session with tools, a subagent, an MCP tool and a permission" "$MODEL"
ev "source: evidence/opencode/P2-hooks/spy.jsonl written by plugins/spy.js during run.sh"
ev "## hooks fired (count by name)"
ev_run sh -c "jq -r 'select(.kind==\"hook\") | .hook' '$EVD/spy.jsonl' | sort | uniq -c | sort -rn"
ev "## hooks declared in the 1.18.8 types that NEVER fired in this session"
ev_run sh -c "for h in config chat.message chat.params chat.headers permission.ask command.execute.before tool.execute.before shell.env tool.execute.after experimental.chat.messages.transform experimental.chat.system.transform experimental.session.compacting experimental.compaction.autocontinue experimental.text.complete tool.definition; do jq -e --arg h \$h 'select(.kind==\"hook\" and .hook==\$h)' '$EVD/spy.jsonl' >/dev/null || echo \"never fired: \$h\"; done"
ev "## bus event types seen by the plugin event hook (count by type)"
ev_run sh -c "jq -r 'select(.kind==\"event\") | .type' '$EVD/spy.jsonl' | sort | uniq -c | sort -rn"
ev "## exact input/output field names per hook"
ev_run sh -c "jq -c 'select(.kind==\"hook\") | {hook, input_keys, output_keys}' '$EVD/spy.jsonl' | sort -u"
ev "## sample inputs: tool.execute.before / tool.execute.after / permission.ask / chat.message / shell.env"
ev_run sh -c "for h in tool.execute.before tool.execute.after permission.ask chat.message shell.env; do jq -c --arg h \$h 'select(.kind==\"hook\" and .hook==\$h) | {hook, input}' '$EVD/spy.jsonl' | head -2; done"
ev "## permission-related bus events (raw)"
ev_run sh -c "jq -c 'select(.kind==\"event\" and (.type|test(\"permission\")))' '$EVD/spy.jsonl'"
n_ev=$(jq -r 'select(.kind=="event") | .type' "$EVD/spy.jsonl" | sort -u | wc -l | tr -d ' ')
n_hk=$(jq -r 'select(.kind=="hook") | .hook' "$EVD/spy.jsonl" | sort -u | wc -l | tr -d ' ')
if [ "$n_ev" -ge 5 ] && [ "$n_hk" -ge 5 ]; then
  ev_verdict PASS "the spy plugin saw $n_hk distinct hooks and $n_ev distinct bus event types in one session (lists above)."
else
  ev_verdict FAIL "spy saw only $n_hk hooks / $n_ev event types."
fi

# ---------------- (a) inject ----------------
ev_open P2-hooks/inject.log "(a) inject changing context on every prompt (notify_inject.sh analog): chat.message synthetic part + experimental.chat.system.transform" "$MODEL"
ev "## plugin log (evidence/opencode/P2-hooks/inject.jsonl): what each hook injected, per prompt"
ev_run cat "$EVD/inject.jsonl"
ev "## model answers, turn 1 then turn 2 (files rewritten in between)"
T1=$(turn_json 1 | jq -r '.final_text'); T2=$(turn_json 2 | jq -r '.final_text')
ev "turn 1 final_text: $T1"
ev "turn 2 final_text: $T2"
ev "## proxy: what each LLM request actually carried (system tail + last user message)"
ev_run sh -c "jq -c 'select(.request.n_tools > 0) | {ts, roles: .request.roles, sys_has_ZULU: (.request.system_tail|test(\"ZULU-901\")), sys_has_YANKEE: (.request.system_tail|test(\"YANKEE-902\")), user_has_ALPHA: (.request.last_user|test(\"ALPHA-111\")), user_has_BRAVO: (.request.last_user|test(\"BRAVO-222\")), sys_tail: .request.system_tail[-70:], last_user: .request.last_user[-70:]}' '$EVD/proxy.jsonl' | head -2"
msg_ok=0; case "$T1" in *MSG=ALPHA-111*) case "$T2" in *MSG=BRAVO-222*) msg_ok=1;; esac;; esac
sys_ok=0; case "$T1" in *SYS=ZULU-901*) case "$T2" in *SYS=YANKEE-902*) sys_ok=1;; esac;; esac
px_msg=$(jq -c 'select(.request.n_tools > 0)' "$EVD/proxy.jsonl" | head -2 | jq -s '(.[0].request.last_user|test("ALPHA-111")) and (.[1].request.last_user|test("BRAVO-222")) and ((.[1].request.last_user|test("ALPHA-111"))|not)')
px_sys=$(jq -c 'select(.request.n_tools > 0)' "$EVD/proxy.jsonl" | head -2 | jq -s '(.[0].request.system_tail|test("ZULU-901")) and (.[1].request.system_tail|test("YANKEE-902")) and ((.[1].request.system_tail|test("ZULU-901"))|not)')
ev "summary: chat.message path model-quoted-latest=$msg_ok proxy-fresh=$px_msg | system.transform path model-quoted-latest=$sys_ok proxy-fresh=$px_sys"
if [ "$msg_ok" = 1 ] && [ "$px_msg" = true ] && [ "$px_sys" = true ] && [ "$sys_ok" = 1 ]; then
  ev_verdict PASS "both hooks re-read the file on every prompt; the proxy shows the fresh values in each request and the model quoted the latest values."
elif [ "$msg_ok" = 1 ] && [ "$px_msg" = true ] && [ "$px_sys" = true ]; then
  ev_verdict PASS "chat.message injection (the additionalContext analog) re-read the file per prompt and the model quoted the latest value; system.transform also delivered the fresh value (proxy) but qwen3:32b repeated its stale earlier answer for that field."
elif [ "$px_msg" = true ] || [ "$px_sys" = true ]; then
  ev_verdict PARTIAL "fresh values reached the LLM requests (proxy msg=$px_msg sys=$px_sys) but the model did not quote them (msg=$msg_ok sys=$sys_ok)."
else
  ev_verdict FAIL "injected values not visible in the LLM requests."
fi

# ---------------- (b) gate ----------------
ev_open P2-hooks/gate.log "(b) deny a bash call BEFORE it runs (orchestrator_gate.py analog) and tell primary from subagent" "$MODEL"
ev "## gate decisions (evidence/opencode/P2-hooks/gate.jsonl): note input_fields = what the hook input carries"
ev_run cat "$EVD/gate.jsonl"
ev "## sentinel files after the run"
ev_run sh -c "ls -la '$P2_DIR' | grep sentinel || echo 'no sentinel files at all'"
ev "## what the model saw: tool results carrying the gate reason (proxy last_tool_result)"
ev_run sh -c "jq -r 'select(.request.last_tool_result != null and (.request.last_tool_result|test(\"BLOCKED\"))) | .request.last_tool_result' '$EVD/proxy.jsonl' | head -3"
ev "## turn 3 (primary asked to touch the sentinel): reply and tool parts"
ev_run sh -c "turn_json() { :; }; true"
turn_json 3 | jq -c '{final_text, tools: [.new_messages[]?.tools[]? | {tool, status, output_head}]}' >> "$EV_LOG"
ev "## turn 4 (subagent asked to touch its sentinel): reply, tool parts and child sessions"
turn_json 4 | jq -c '{final_text, children, tools: [.new_messages[]?.tools[]? | {tool, status, child_session, output_head: .output_head[0:200]}]}' >> "$EV_LOG"
p_absent=0; [ -e "$P2_DIR/sentinel-primary" ] || p_absent=1
s_present=0; [ -e "$P2_DIR/sentinel-subagent" ] && s_present=1
deny_seen=$(jq -c 'select(.decision=="deny")' "$EVD/gate.jsonl" | wc -l | tr -d ' ')
exempt_seen=$(jq -c 'select(.decision=="allow-subagent-exempt")' "$EVD/gate.jsonl" | wc -l | tr -d ' ')
reason_seen=$(jq -c 'select(.request.last_tool_result != null and (.request.last_tool_result|test("BLOCKED by orchestrator gate")))' "$EVD/proxy.jsonl" | wc -l | tr -d ' ')
ev "summary: primary sentinel absent=$p_absent, deny decisions=$deny_seen, model saw reason=$reason_seen, subagent exempt decisions=$exempt_seen, subagent sentinel present=$s_present"
if [ "$p_absent" = 1 ] && [ "$deny_seen" -ge 1 ] && [ "$reason_seen" -ge 1 ] && [ "$exempt_seen" -ge 1 ] && [ "$s_present" = 1 ]; then
  ev_verdict PASS "tool.execute.before blocked the primary's bash call before execution (sentinel absent, reason in the tool result) and exempted the subagent via a session.parentID lookup (the hook input itself carries only tool/sessionID/callID)."
elif [ "$p_absent" = 1 ] && [ "$deny_seen" -ge 1 ] && [ "$reason_seen" -ge 1 ]; then
  ev_verdict PARTIAL "primary-agent gating works (blocked before execution, reason visible) but the subagent path was not demonstrated (exempt=$exempt_seen, subagent sentinel=$s_present)."
else
  ev_verdict FAIL "gate not demonstrated (absent=$p_absent deny=$deny_seen reason=$reason_seen)."
fi

# ---------------- (c) activity log ----------------
ev_open P2-hooks/activity.log "(c) log every tool use (log_activity.sh analog) incl. MCP and subagent tools" "$MODEL"
ev "## activity.jsonl (evidence/opencode/P2-hooks/activity.jsonl)"
ev_run cat "$EVD/activity.jsonl"
ev "## ground truth: every tool part in every session of the tree (sessions-dump.json, read over HTTP) vs activity.jsonl and gate.jsonl"
ev_run python3.12 -I -c '
import json,sys
dump=json.load(open(sys.argv[1]))
truth={t["callID"]:(sid,t["tool"],t["status"]) for sid,msgs in dump.items() for m in msgs for t in m["tools"]}
act={}
for l in open(sys.argv[2]):
    if l.strip():
        d=json.loads(l); act.setdefault(d["callID"],set()).add(d.get("phase","end"))
gate={json.loads(l)["callID"]:json.loads(l)["decision"] for l in open(sys.argv[3]) if l.strip()}
print("tool calls in all session transcripts:", len(truth))
for c,(sid,tool,st) in sorted(truth.items(), key=lambda x:x[1]):
    print(" ", c, tool, "status="+str(st), "session="+sid[-8:], "| activity:", sorted(act.get(c,[])), "| gate:", gate.get(c))
print("calls in NO log at all:", sorted(c for c in truth if c not in act and c not in gate))
print("executed calls (not gate-denied) without an activity end line:", sorted(c for c in truth if gate.get(c)!="deny" and "end" not in act.get(c,set())))
' "$EVD/sessions-dump.json" "$EVD/activity.jsonl" "$EVD/gate.jsonl"
ev "## hook order across plugin files, per call (timestamps of gate / activity start / spy tool.execute.before)"
ev_run python3.12 -I -c '
import json,sys
g={json.loads(l)["callID"]:json.loads(l)["ts"] for l in open(sys.argv[1]) if l.strip()}
a={}
for l in open(sys.argv[2]):
    d=json.loads(l)
    if d.get("phase")=="start": a[d["callID"]]=d["ts"]
s={}
for l in open(sys.argv[3]):
    d=json.loads(l)
    if d.get("kind")=="hook" and d.get("hook")=="tool.execute.before": s[json.loads(d["input"])["callID"]]=d["ts"]
for c in sorted(set(g)|set(a)|set(s)):
    order=sorted([(t,n) for n,t in (("gate",g.get(c)),("activity",a.get(c)),("spy",s.get(c))) if t])
    print(c, " -> ".join(n for _,n in order), "| missing:", [n for n,t in (("gate",g.get(c)),("activity",a.get(c)),("spy",s.get(c))) if not t])
print("plugin files: activity.js gate.js inject.js spy.js (alphabetical); a throwing hook stops the hooks of plugins after it for that call")
' "$EVD/gate.jsonl" "$EVD/activity.jsonl" "$EVD/spy.jsonl"
has_mcp=$(jq -c 'select(.phase=="end" and (.tool|test("stdio_echo")))' "$EVD/activity.jsonl" | wc -l | tr -d ' ')
has_sub=$(jq -c 'select(.phase=="end" and .parentSessionID != null)' "$EVD/activity.jsonl" | wc -l | tr -d ' ')
has_task=$(jq -c 'select(.phase=="end" and .tool=="task")' "$EVD/activity.jsonl" | wc -l | tr -d ' ')
has_agent=$(jq -c 'select(.phase=="end" and .agent != null)' "$EVD/activity.jsonl" | wc -l | tr -d ' ')
missing=$(python3.12 -I -c '
import json,sys
dump=json.load(open(sys.argv[1]))
truth={t["callID"] for msgs in dump.values() for m in msgs for t in m["tools"]}
seen={json.loads(l)["callID"] for l in open(sys.argv[2]) if l.strip()} | {json.loads(l)["callID"] for l in open(sys.argv[3]) if l.strip()}
print(len(truth-seen))' "$EVD/sessions-dump.json" "$EVD/activity.jsonl" "$EVD/gate.jsonl")
n_all=$(wc -l < "$EVD/activity.jsonl" | tr -d ' ')
ev "summary: lines=$n_all missing-from-log=$missing mcp=$has_mcp subagent-session-tools=$has_sub task=$has_task with-agent=$has_agent"
if [ "$missing" = 0 ] && [ "$has_mcp" -ge 1 ] && [ "$has_sub" -ge 1 ] && [ "$has_task" -ge 1 ]; then
  ev_verdict PASS "every executed tool call in every session of the tree (built-in, MCP, task, the subagent's own calls) is in the activity log with args, session, parent session, agent, duration and result head; the gate-blocked attempt is on record in the gate's own log (later plugins never see a call an earlier plugin rejects)."
else
  ev_verdict PARTIAL "activity logged ($n_all lines) but coverage incomplete (mcp=$has_mcp subagent=$has_sub task=$has_task)."
fi
for f in events inject gate activity; do ev_redact "$EVD/$f.log"; tail -1 "$EVD/$f.log"; done
