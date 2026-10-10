#!/usr/bin/env bash
# Runs bun_sqlite_probe.ts (a) on this macOS host with the installed bun and (b) inside an
# ubuntu:24.04 arm64 container (ocsoma-me-bun) with bun pinned to the host's version, both against
# the 50k corpus. opencode plugins run in-process on Bun, so bun:sqlite is the store a plugin gets.
# Writes evidence/memory/bun-sqlite.log. The container is removed at the end.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
SCRATCH="${OCSOMA_SCRATCH:-${TMPDIR:-/tmp}/ocsoma-me}"
DATA="$SCRATCH/data/dataset-50000.json"
LOG="$ROOT/evidence/memory/bun-sqlite.log"
NAME=ocsoma-me-bun
BUNV="$(bun --version)"
{
  echo "date_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "command: bash run_bun_sqlite.sh   (bun run bun_sqlite_probe.ts dataset-50000.json, host + ubuntu container)"
  echo "host: $(uname -srm), bun $BUNV at $(command -v bun)"
  echo "== macOS host"
  bun run "$HERE/bun_sqlite_probe.ts" "$DATA" 2>&1
  echo "== ubuntu:24.04 arm64 container, bun $BUNV"
  docker rm -f "$NAME" >/dev/null 2>&1
  docker run -d --name "$NAME" --platform linux/arm64 ubuntu:24.04 sleep infinity >/dev/null
  docker exec "$NAME" bash -lc 'apt-get update -qq >/dev/null && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl unzip ca-certificates >/dev/null 2>&1; echo apt-rc=$?'
  docker exec "$NAME" bash -lc "curl -fsSL https://bun.sh/install | bash -s bun-v$BUNV >/dev/null 2>&1; ~/.bun/bin/bun --version"
  docker cp "$HERE/bun_sqlite_probe.ts" "$NAME:/root/bun_sqlite_probe.ts" >/dev/null
  docker cp "$DATA" "$NAME:/root/dataset-50000.json" >/dev/null
  docker exec "$NAME" bash -lc 'cd /root && ~/.bun/bin/bun run bun_sqlite_probe.ts dataset-50000.json 2>&1'
  docker rm -f "$NAME" >/dev/null
  echo "cleanup: container $NAME removed"
} 2>&1 | tee "$LOG"
pass=$(grep -c '^RESULT: PASS' "$LOG")
worst=$(grep -o 'p50=[0-9.]*' "$LOG" | cut -d= -f2 | sort -n | tail -1)
if [ "$pass" -ge 2 ]; then
  echo "VERDICT: PASS — bun:sqlite on macOS and on Linux (arm64 container) has FTS5, recursive CTEs, JSON1, UPSERT and WAL; no user-defined SQL functions (rank decay in JS); worst 50k-fact recall p50 ${worst} ms" | tee -a "$LOG"
else
  echo "VERDICT: PARTIAL — bun:sqlite probe passed on $pass of 2 platforms (see log)" | tee -a "$LOG"
fi
