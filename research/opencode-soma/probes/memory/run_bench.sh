#!/usr/bin/env bash
# Runs bench.py at every scale with all backends. Needs the corpora from run_gen.sh and a venv with
# the `neo4j` driver ($OCSOMA_SCRATCH/venv, created here if missing). Starts and removes the
# ocsoma-me-neo4j container + ocsoma-me-neo4j-data volume itself. Writes evidence/memory/bench-<n>.log
# and merges rows into evidence/memory/bench-results.json.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRATCH="${OCSOMA_SCRATCH:-${TMPDIR:-/tmp}/ocsoma-me}"
export OCSOMA_SCRATCH="$SCRATCH"
if [ ! -x "$SCRATCH/venv/bin/python" ]; then
  python3.12 -m venv "$SCRATCH/venv"
  "$SCRATCH/venv/bin/pip" install -q neo4j==6.4.0
fi
for n in ${SCALES:-600 5000 50000}; do
  PYTHONHASHSEED=0 "$SCRATCH/venv/bin/python" "$HERE/bench.py" --data "$SCRATCH/data" --n-facts "$n" \
    --runs "${RUNS:-30}" --backends "${BACKENDS:-files,naive,sqlite,neo4j}" | tail -3
done
