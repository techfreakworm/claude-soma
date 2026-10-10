#!/usr/bin/env bash
# P0: prove the sandbox redirection holds, the ollama provider works through the logging proxy,
# and record the context length ollama really allocates vs the size of opencode's prompt.
set -u
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P0-setup/sandbox-verify.sh"
PORT=47101
PROBE_ID=P0-setup; SANDBOX_FRESH=1; OC_BASEURL="http://127.0.0.1:$PORT/v1"
source "$(dirname "$0")/../sandbox.sh"
cd "$PROJ" || exit 1
EVD="$OCSOMA_EVID/P0-setup"; mkdir -p "$EVD"
PROXY_LOG="$EVD/proxy.jsonl"; : > "$PROXY_LOG"
proxy_start "$PORT" "$PROXY_LOG" || exit 1

ev_open P0-setup/sandbox-verify.log "sandbox paths, resolved config, ollama provider + proxy, real context length" "ollama/lfm2.5-thinking and ollama/qwen3:32b"
ev "## 1. resolved global paths (must all be under the sandbox)"
ev_run "$OC" debug paths
ev "## 2. resolved config"
ev_run "$OC" debug config
ev "## 3. models visible (enabled_providers=[ollama] hides every hosted provider)"
ev_run "$OC" models
ev "## 4. one turn with lfm2.5-thinking (quality irrelevant) through the logging proxy"
ev_run "$OC" run -m ollama/lfm2.5-thinking --format json "Reply with the single word PONG and nothing else."
ev "## 5. one turn with qwen3:32b through the logging proxy"
ev_run "$OC" run -m ollama/qwen3:32b --format json "Reply with the single word PONG and nothing else."
ev "## 6. what ollama allocated (context_length per loaded model)"
ev_run curl -s http://127.0.0.1:11434/api/ps
ev "## 7. proxy summary: per LLM request -> model, n_tools, system_chars, prompt tokens, wall"
ev_run python3.12 -I -c '
import json,sys
for ln in open(sys.argv[1]):
    e=json.loads(ln); r=e.get("request") or {}; u=(e.get("response") or {}).get("usage") or {}
    if not r: continue
    print(e["ts"], r.get("model"), "tools=%s" % r.get("n_tools"), "tools_json_chars=%s" % r.get("tools_json_chars"),
          "system_chars=%s" % r.get("system_chars"), "prompt_tokens=%s" % u.get("prompt_tokens"),
          "completion_tokens=%s" % u.get("completion_tokens"), "reasoning_effort=%s" % (r.get("params") or {}).get("reasoning_effort"),
          "wall=%ss" % e.get("wall_s"))
print("tools offered (first request):", (json.loads(open(sys.argv[1]).readline()).get("request") or {}).get("tools_offered"))
' "$PROXY_LOG"
ev "## 8. sandbox contents after the runs (where opencode wrote)"
ev_run find "$SANDBOX" -maxdepth 4 -not -path '*/node_modules/*' -not -path '*/project/.git/*'
proxy_stop "$PORT"

ok_paths=$(grep -c "ocsoma-pe/P0-setup" "$EV_LOG")
n_req=$(grep -c '"request"' "$PROXY_LOG")
if [ "$ok_paths" -ge 9 ] && [ "$n_req" -ge 2 ] && grep -q 'PONG' "$EV_LOG"; then
  ev_verdict PASS "all opencode paths resolve inside the sandbox and both ollama models answered through the logging proxy."
else
  ev_verdict FAIL "paths=$ok_paths requests=$n_req; see transcript."
fi
ev_redact "$EV_LOG" "$PROXY_LOG"
tail -1 "$EV_LOG"
