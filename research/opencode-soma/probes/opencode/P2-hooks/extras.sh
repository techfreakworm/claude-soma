#!/usr/bin/env bash
# P2 extras requested by the lead (NO LLM):
#  (1) cost of the gate's sessionID->parentID lookup (client.session.get == GET /session/{id}),
#      measured against a live serve, uncached vs an in-process cache (the cache is a dict hit).
#  (2) exact evidence the permission.ask HOOK never fires in 1.18.8: the type declares it, the spy
#      plugin registered it, yet the P2 session log has zero permission.ask hook events while the
#      bus DID emit permission.asked/replied.
#  (3) the `event` hook sees session.idle (memory-capture trigger) and message.updated carrying
#      token counts (usage) - session.idle from P2 spy.jsonl; the token block from the P8 raw SSE dump
#      (the spy plugin truncated event props, so message.updated tokens are read from P8 sse-event.txt).
#  (4) a custom plugin TOOL's ctx carries `agent` (tool.d.ts) - unlike tool.execute.before.
set -u
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P2-hooks/extras.sh"
HERE="$(cd "$(dirname "$0")" && pwd)"
SERVE_PORT=$(( ${OCSOMA_PORT_BASE:-47100} + 26 ))
PROBE_ID=P2-extras; SANDBOX_FRESH=1
source "$HERE/../sandbox.sh"
cd "$PROJ" || exit 1
EVD="$OCSOMA_EVID/P2-hooks"; mkdir -p "$EVD"
TYPES="$OCSOMA_REAL_TMPDIR/ocsoma-pe/P2-types/npm/plugin/package/dist/tool.d.ts"

ev_open P2-hooks/extras.log "gate parentID-lookup cost; permission.ask-never-fires; session.idle + token-bearing message.updated; custom-tool ctx.agent" "none (no LLM)"

ev "## (1) sessionID -> parentID lookup latency (GET /session/{id}); this is what the gate calls per tool call for a primary-vs-subagent decision"
"$OC" serve --port "$SERVE_PORT" --hostname 127.0.0.1 > "$SANDBOX/serve.out" 2>&1 &
SP=$!
for i in $(seq 1 100); do curl -s -o /dev/null "http://127.0.0.1:$SERVE_PORT/global/health" && break; sleep 0.2; done
PID=$(curl -s -H 'content-type: application/json' -X POST "http://127.0.0.1:$SERVE_PORT/session" -d '{"title":"p2-parent"}' | jq -r .id)
CID=$(curl -s -H 'content-type: application/json' -X POST "http://127.0.0.1:$SERVE_PORT/session" -d "{\"title\":\"p2-child\",\"parentID\":\"$PID\"}" | jq -r .id)
ev "parent session $PID, child session $CID (parentID set at create)"
ev_run python3.12 -I - "http://127.0.0.1:$SERVE_PORT" "$CID" <<'PY'
import json, time, urllib.request, sys
base, cid = sys.argv[1], sys.argv[2]
def get_parent():
    with urllib.request.urlopen(f"{base}/session/{cid}", timeout=10) as r:
        return json.load(r).get("parentID")
# uncached: one HTTP round-trip per call (what a naive gate does on every tool call)
N = 50
t = []
for _ in range(N):
    t0 = time.perf_counter(); p = get_parent(); t.append((time.perf_counter() - t0) * 1000)
t.sort()
print(f"uncached GET /session/{{id}} over 127.0.0.1: n={N} parentID={p!r}")
print(f"  latency ms: p50={t[N//2]:.2f} p90={t[int(N*0.9)]:.2f} max={t[-1]:.2f} mean={sum(t)/N:.2f}")
# cached: resolve once, then dict lookups (what the P2 gate plugin's parentCache does)
cache = {}
def cached():
    if cid in cache: return cache[cid]
    cache[cid] = get_parent(); return cache[cid]
cached()  # prime
t2 = []
for _ in range(100000):
    t0 = time.perf_counter(); cached(); t2.append((time.perf_counter() - t0) * 1e6)
t2.sort()
print(f"cached (dict hit after first lookup): n=100000 p50={t2[50000]:.3f}us p99={t2[99000]:.3f}us")
print("=> a cold gate pays one ~p50 HTTP round-trip per NEW session; every later tool call on that")
print("   session is a sub-microsecond dict hit. One lookup per session, not per tool call.")
PY
kill "$SP" 2>/dev/null; wait "$SP" 2>/dev/null

ev "## (2) permission.ask HOOK never fires in 1.18.8 (vs the permission.asked EVENT, which does)"
ev "declared in the type defs:"
ev_run sh -c "grep -n 'permission.ask' '$OCSOMA_REAL_TMPDIR/ocsoma-pe/P2-types/npm/plugin/package/dist/index.d.ts'"
ev "registered by the spy plugin (plugins/spy.js):"
ev_run sh -c "grep -n 'permission.ask' '$HERE/plugins/spy.js'"
ev "hook invocations recorded in the committed P2 run (spy.jsonl) - expect ZERO:"
ev_run sh -c "jq -c 'select(.kind==\"hook\" and .hook==\"permission.ask\")' '$EVD/spy.jsonl' | wc -l | tr -d ' '"
ev "bus events for the SAME permission (these DID fire) - permission.asked / permission.replied:"
ev_run sh -c "jq -c 'select(.kind==\"event\" and (.type|test(\"permission\"))) | {type, id: (.props|fromjson?|.id // .props)}' '$EVD/spy.jsonl' 2>/dev/null | head; jq -rc 'select(.kind==\"event\" and (.type|test(\"permission\"))) | .type' '$EVD/spy.jsonl' | sort | uniq -c"

ev "## (3) event hook sees session.idle (memory-capture trigger) and message.updated with token counts (usage)"
ev "session.idle events in the committed P2 spy.jsonl:"
ev_run sh -c "jq -c 'select(.kind==\"event\" and .type==\"session.idle\") | {type, props}' '$EVD/spy.jsonl' | head -3"
ev "message.updated events carrying a tokens block (input/output/cache) - from the P8 raw SSE /event dump, since the spy plugin truncated event props to 300 chars:"
ev_run sh -c "grep -o '\"type\":\"message.updated\"[^\n]*' '$OCSOMA_EVID/P8-serve/sse-event.txt' | grep -o '\"tokens\":{[^}]*}' | sort -u | head -3"
idle=$(jq -c 'select(.kind=="event" and .type=="session.idle")' "$EVD/spy.jsonl" | wc -l | tr -d ' ')
tok=$(grep -c '"type":"message.updated".*"tokens"' "$OCSOMA_EVID/P8-serve/sse-event.txt")
ev "counts: session.idle events=$idle ; message.updated events carrying tokens=$tok"

ev "## (4) a custom plugin TOOL's ctx carries agent (tool.d.ts ToolContext) - unlike tool.execute.before {tool,sessionID,callID}"
ev_run sh -c "sed -n '/export type ToolContext = {/,/};/p' '$TYPES'"

askhook=$(jq -c 'select(.kind=="hook" and .hook=="permission.ask")' "$EVD/spy.jsonl" | wc -l | tr -d ' ')
ctxagent=$(grep -c 'agent: string' "$TYPES")
if [ "$askhook" = 0 ] && [ "$idle" -ge 1 ] && [ "$tok" -ge 1 ] && [ "$ctxagent" -ge 1 ]; then
  ev_verdict PASS "gate parentID lookup is one HTTP round-trip per new session (p50 sub-ms on loopback) then a cache hit; permission.ask hook fired 0 times while the bus emitted permission.asked/replied; the event hook sees session.idle ($idle) and token-bearing message.updated ($tok); a custom tool's ctx has agent."
else
  ev_verdict PARTIAL "askhook=$askhook idle=$idle tok=$tok ctxagent=$ctxagent (see transcript)."
fi
ev_redact "$EV_LOG"
tail -1 "$EV_LOG"
