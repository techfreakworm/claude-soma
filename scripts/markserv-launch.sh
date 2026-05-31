#!/usr/bin/env bash
# scripts/markserv-launch.sh
#
# Single source of truth for the markserv argv used by:
#   - systemd/claude-soma-markserv.service (ExecStart)
#
# Override defaults via environment variables (useful for local testing):
#   SOMA_MARKSERV_ROOT  — directory to serve  (default: /var/lib/claude-soma/staging)
#   SOMA_MARKSERV_PORT  — TCP port to bind    (default: 18080)
#   SOMA_MARKSERV_ADDR  — bind address        (default: 127.0.0.1)
#
# The existing ngrok tunnel forwards to localhost:18080 — no tunnel change needed.

STAGING_DIR=${SOMA_MARKSERV_ROOT:-/var/lib/claude-soma/staging}
MARKSERV_PORT=${SOMA_MARKSERV_PORT:-18080}
MARKSERV_ADDR=${SOMA_MARKSERV_ADDR:-127.0.0.1}

exec markserv "$STAGING_DIR" --port "$MARKSERV_PORT" --address "$MARKSERV_ADDR"
