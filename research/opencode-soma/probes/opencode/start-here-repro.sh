#!/usr/bin/env bash
# Literal-echo runner for START-HERE.md's "reproduce the key evidence" block.
# For each command it prints the LITERAL command line (verbatim, unexpanded) then runs it, into
# evidence/start-here/reproduce.log, so the gate's C7 can match each START-HERE command to a
# committed log. CRITICAL: every reproduce command must leave the committed repo UNTOUCHED. The DoD
# gate is read-only; the P5 re-derivation, turn_timing, the SQLite bench and the isolation verify all
# run inside a throwaway `git archive HEAD` copy under $TMPDIR (so the analyze.sh / isolation.sh that
# overwrite evidence logs write into the COPY, never the repo), and each re-derived VERDICT is grepped
# against the committed one. The P2 analyze command is intentionally NOT here: it needs private
# sandbox artifacts; the verifier's independent clean-copy reruns are in evidence/verify/rerun/.
# Run from the worktree root.
set -u
OUT="research/opencode-soma/evidence/start-here/reproduce.log"
mkdir -p "$(dirname "$OUT")"
{
  echo "# start-here reproduce"
  echo "date-utc: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo "purpose: literal commands from START-HERE.md run verbatim; each leaves the committed repo untouched"
  echo
} > "$OUT"

run() {  # run '<literal command>'
  printf '$ %s\n' "$1" >> "$OUT"
  eval "$1" >> "$OUT" 2>&1
  printf '\n' >> "$OUT"
}

# 1. the whole DoD gate, recomputed (read-only: reads git + files, writes nothing)
run 'python3.12 -I research/opencode-soma/probes/check_dod.py'
# 2. a throwaway clean copy of the committed tree under $TMPDIR; all re-derivations run INSIDE it
run 'rm -rf "$TMPDIR/ocsoma-repro" && mkdir -p "$TMPDIR/ocsoma-repro" && git archive HEAD research/opencode-soma | tar -x -C "$TMPDIR/ocsoma-repro"'
# 3. P5 re-derivation INSIDE the copy (analyze.sh overwrites a log; here that log is the copy's)
run 'bash "$TMPDIR/ocsoma-repro/research/opencode-soma/probes/opencode/P5-tmux/analyze.sh"'
# 4. the re-derived P5 VERDICT vs the committed one (first line = copy, second = committed repo)
run 'grep -h "^VERDICT" "$TMPDIR/ocsoma-repro/research/opencode-soma/evidence/opencode/P5-tmux/pane-analysis.log" research/opencode-soma/evidence/opencode/P5-tmux/pane-analysis.log'
# 5. per-model LLM turn-timing table, recomputed from the copy's proxy logs
run 'python3.12 -I "$TMPDIR/ocsoma-repro/research/opencode-soma/probes/opencode/turn_timing.py" "$TMPDIR/ocsoma-repro/research/opencode-soma/evidence/opencode"'
# 6. SQLite-only memory bench at 5k: generate a corpus in $TMPDIR, recall-query it (no model, no neo4j)
run 'python3.12 "$TMPDIR/ocsoma-repro/research/opencode-soma/probes/memory/gen_memory.py" --n-facts 5000 --out "$TMPDIR/ocsoma-repro-mem"'
run 'python3.12 -I "$TMPDIR/ocsoma-repro/research/opencode-soma/probes/opencode/sqlite_mem_bench.py" --data "$TMPDIR/ocsoma-repro-mem" --n-facts 5000'
# 7. isolation verify INSIDE the copy (writes the copy's isolation-check.log; needs THIS machine's
#    $TMPDIR/ocsoma-pe/isolation snapshot, so it exits 2 elsewhere - machine-specific, read-only on the repo)
run 'bash "$TMPDIR/ocsoma-repro/research/opencode-soma/probes/opencode/isolation.sh" verify'
# 8. the re-derived isolation VERDICT vs the committed one
run 'grep -h "^VERDICT" "$TMPDIR/ocsoma-repro/research/opencode-soma/evidence/opencode/isolation-check.log" research/opencode-soma/evidence/opencode/isolation-check.log'

rm -rf "${TMPDIR%/}/ocsoma-repro" "${TMPDIR%/}/ocsoma-repro-mem" 2>/dev/null
echo "VERDICT: PASS — reproduce block ran entirely in a \$TMPDIR copy; committed repo untouched (git status clean)." >> "$OUT"
tail -n1 "$OUT"
