#!/usr/bin/env bash
# Re-run every opencode probe in order. Sequential on purpose: one LLM consumer at a time, so the
# per-turn wall-clock numbers stay meaningful. Total: roughly an hour on qwen3:32b.
#   run-all.sh                 probes only
#   run-all.sh --isolation     also take a fresh isolation snapshot first and verify it at the end
#                              (overwrites the previous snapshot marker)
set -u
D="$(cd "$(dirname "$0")" && pwd)"
[ "${1:-}" = "--isolation" ] && bash "$D/isolation.sh" snapshot
for s in P0-setup/cli-help.sh P0-setup/sandbox-verify.sh P8-serve/run.sh \
         P2-hooks/fetch-types.sh P2-hooks/catalog.sh P2-hooks/run.sh \
         P1-headless/run.sh P5-tmux/run.sh P3-mcp/run.sh P6-permissions/run.sh \
         P4-subagents/run.sh P7-usage/run.sh; do
  echo "=== $s ($(date -u +%H:%M:%SZ))"
  bash "$D/$s" 2>&1 | tail -6
done
[ "${1:-}" = "--isolation" ] && bash "$D/isolation.sh" verify
