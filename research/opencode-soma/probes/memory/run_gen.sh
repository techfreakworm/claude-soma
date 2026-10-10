#!/usr/bin/env bash
# Generates the three benchmark corpora (600 / 5k / 50k facts, seed 7) into $OCSOMA_SCRATCH/data
# and records stats, planted-item counts, oracle self-check and sha256 of every output.
# Re-running must reproduce the same sha256 values (determinism check).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
SCRATCH="${OCSOMA_SCRATCH:-${TMPDIR:-/tmp}/ocsoma-me}"
LOG="$ROOT/evidence/memory/gen.log"
mkdir -p "$SCRATCH/data" "$(dirname "$LOG")"
{
  echo "date_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "command: PYTHONHASHSEED=0 python3.12 gen_memory.py --n-facts {600,5000,50000} --seed 7 --out \$SCRATCH/data"
  echo "python: $(python3.12 --version 2>&1)  host: $(uname -srm)"
  fail=0
  for n in 600 5000 50000; do
    echo "== n=$n"
    if ! PYTHONHASHSEED=0 python3.12 "$HERE/gen_memory.py" --n-facts "$n" --seed 7 --out "$SCRATCH/data"; then
      fail=1
    fi
  done
  if [ "$fail" = 0 ]; then
    echo "VERDICT: PASS — three seeded corpora generated; the oracle self-check (planted unresolved contradictions == Q5 answer, no dangling refs, timestamps in the 180-day window) passed at every scale"
  else
    echo "VERDICT: FAIL — the oracle self-check reported problems (see self_check_problems above)"
  fi
} 2>&1 | tee "$LOG"
