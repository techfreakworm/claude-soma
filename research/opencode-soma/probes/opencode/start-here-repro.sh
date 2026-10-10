#!/usr/bin/env bash
# Literal-echo runner for START-HERE.md's "reproduce the key evidence" block.
# For each command it prints the LITERAL command line (verbatim, unexpanded) then runs it, into
# evidence/start-here/reproduce.log, so the gate's C7 can match each START-HERE command to a
# committed log. Every command here is cheap, deterministic, and touches NO opencode binary and
# mutates NO other probe's evidence (the gate is pure Python; the rest read committed artifacts),
# so running this does not affect the isolation window. Run from the worktree root.
set -u
OUT="research/opencode-soma/evidence/start-here/reproduce.log"
mkdir -p "$(dirname "$OUT")"
{
  echo "# start-here reproduce"
  echo "date-utc: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "purpose: literal commands from START-HERE.md run verbatim; each prints a key result"
  echo
} > "$OUT"

run() {  # run '<literal command>'
  printf '$ %s\n' "$1" >> "$OUT"
  eval "$1" >> "$OUT" 2>&1
  printf '\n' >> "$OUT"
}

# 1. the gate, live: a WebFetch tool event is denied before it runs (deterministic, no model)
run $'printf \'%s\' \'{"tool_name":"WebFetch","tool_input":{"url":"http://x"}}\' | python3.12 scripts/orchestrator_gate.py'
# 2. P8 serve probe verdict (full OpenAPI/SSE/auth/attach/web)
run 'tail -n1 research/opencode-soma/evidence/opencode/P8-serve/serve.log'
# 3. P2 gate probe verdict (tool.execute.before blocks a call before it runs)
run 'tail -n1 research/opencode-soma/evidence/opencode/P2-hooks/gate.log'
# 4. P1 resume probe verdict (kill -9 serve mid-generation, restart, continue)
run 'tail -n1 research/opencode-soma/evidence/opencode/P1-headless/crash-serve.log'
# 5. memory bench at 5k verdict
run 'grep -m1 VERDICT research/opencode-soma/evidence/memory/bench-5000.log'
# 6. tmux send-keys/scrape probe verdict
run 'tail -n1 research/opencode-soma/evidence/opencode/P5-tmux/tmux.log'
# 7. per-model LLM turn-timing table, recomputed live from the committed proxy logs (no model call)
run 'python3.12 -I research/opencode-soma/probes/opencode/turn_timing.py research/opencode-soma/evidence/opencode'

# 8. isolation: confirm no probe touched the operator real opencode/claude dirs (reads version sandboxed)
run 'bash research/opencode-soma/probes/opencode/isolation.sh verify'

echo "VERDICT: PASS — reproduce block ran; see $OUT" >> "$OUT"
tail -n1 "$OUT"
