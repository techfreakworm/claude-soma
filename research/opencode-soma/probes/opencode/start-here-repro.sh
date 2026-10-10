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

# 1. the whole DoD gate recomputed (structure, citations, isolation window, no-credential scan)
run 'python3.12 -I research/opencode-soma/probes/check_dod.py'
# 2. P2 re-derivation from saved artifacts (gate/inject/activity/events verdicts; no new LLM turns)
run 'bash research/opencode-soma/probes/opencode/P2-hooks/analyze.sh'
# 3. P5 re-derivation from the saved panes (whole-line vs column-aware scrape; no new LLM turns)
run 'bash research/opencode-soma/probes/opencode/P5-tmux/analyze.sh'
# 4. SQLite-only memory bench at 5k: generate a corpus into $TMPDIR, then recall-query it (no model,
#    no neo4j, writes only under $TMPDIR; the full multi-backend matrix is evidence/memory/).
run 'python3.12 research/opencode-soma/probes/memory/gen_memory.py --n-facts 5000 --out "$TMPDIR/sh-mem-5k"'
run 'python3.12 -I research/opencode-soma/probes/opencode/sqlite_mem_bench.py --data "$TMPDIR/sh-mem-5k" --n-facts 5000'
# 5. per-model LLM turn-timing table, recomputed from the committed proxy logs (no model call)
run 'python3.12 -I research/opencode-soma/probes/opencode/turn_timing.py research/opencode-soma/evidence/opencode'
# 6. isolation: confirm no probe touched the operator real opencode/claude dirs (reads version sandboxed)
run 'bash research/opencode-soma/probes/opencode/isolation.sh verify'

rm -rf "${TMPDIR%/}/sh-mem-5k" 2>/dev/null
echo "VERDICT: PASS — reproduce block ran; see $OUT" >> "$OUT"
tail -n1 "$OUT"
