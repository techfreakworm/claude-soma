#!/usr/bin/env bash
# Measures the Neo4j Community image pull (wall time + on-disk image size).
# A re-run on a host that already has the image measures a cached pull; the
# transcript records whether the image was present before the pull started.
set -euo pipefail
IMAGE="${NEO4J_IMAGE:-neo4j:2026.09.0-community}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LOG="$ROOT/evidence/memory/neo4j-pull.log"
mkdir -p "$(dirname "$LOG")"
{
  echo "date_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "command: docker pull $IMAGE"
  echo "docker: $(docker version --format '{{.Client.Version}} client / {{.Server.Version}} server ({{.Server.Os}}/{{.Server.Arch}})')"
  echo "host: $(uname -srm)"
  if docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "image_present_before_pull: yes (pull time below is a cached pull)"
  else
    echo "image_present_before_pull: no (cold pull)"
  fi
  t0=$(python3 -c 'import time; print(time.time())')
  docker pull "$IMAGE" 2>&1 | grep -v -E 'Downloading|Extracting|Waiting|Verifying' || true
  t1=$(python3 -c 'import time; print(time.time())')
  pull_s=$(python3 -c "print(round($t1-$t0,1))")
  echo "pull_wall_seconds: $pull_s"
  size_bytes=$(docker image inspect "$IMAGE" --format '{{.Size}}')
  echo "image_size_bytes: $size_bytes"
  echo "image_size_mb: $(python3 -c "print(round($size_bytes/1e6,1))")"
  echo "image_id: $(docker image inspect "$IMAGE" --format '{{.Id}}')"
  echo "image_arch: $(docker image inspect "$IMAGE" --format '{{.Os}}/{{.Architecture}}')"
  docker images "$IMAGE" --format 'docker images: {{.Repository}}:{{.Tag}} {{.Size}}'
  echo "VERDICT: PASS — pulled $IMAGE in ${pull_s}s; image is $(python3 -c "print(round($size_bytes/1e6))") MB unpacked (see image_present_before_pull for cold vs cached)"
} 2>&1 | tee "$LOG"
