#!/usr/bin/env bash
# scripts/deploy.sh — DEV-MACHINE -> REMOTE VPS rsync deploy.
#
# !!!! DO NOT RUN ON THE VPS ITSELF !!!!
# !!!! If you are sitting at a fresh VPS, run scripts/bootstrap.sh INSTEAD !!!!
#
# This script rsyncs the current working tree from a development laptop
# to a remote VPS specified by environment vars / ssh-config alias.
# Running it on the VPS itself either fails (unknown host) or destructively
# rsyncs the directory over itself.
#
# For a fresh-VPS install: see scripts/bootstrap.sh + INSTALL.md.
#
# Usage (from your dev machine):
#   CLAUDE_SOMA_HOST=soma-vps ./scripts/deploy.sh
#   (soma-vps must be a valid ssh-config Host or reachable hostname)

set -euo pipefail

HOST="${CLAUDE_SOMA_HOST:-soma-vps}"
REMOTE="/opt/claude-soma"

ssh "$HOST" "sudo install -d -o ubuntu -g ubuntu $REMOTE"

rsync -avh --delete \
    --exclude '.git' \
    --exclude '.venv' \
    --exclude '__pycache__' \
    --exclude 'node_modules' \
    --exclude '.next' \
    --exclude 'frontend/.next' \
    --exclude 'tests/' \
    --exclude '*.sqlite' \
    ./ "$HOST:$REMOTE/"

ssh "$HOST" bash <<EOSSH
set -euo pipefail
cd $REMOTE
if [[ ! -d .venv ]]; then
    python3.12 -m venv .venv
fi
.venv/bin/pip install -U pip
.venv/bin/pip install -e ".[dev]"
chmod +x scripts/*.sh

# Build the Next.js dashboard ON THE SERVER. rsync excludes .next, and
# output: "standalone" does NOT copy .next/static or public/ next to
# server.js, so build_frontend.sh does the build AND that copy. Without this
# step the standalone server serves HTML but every /_next/static/* asset 404s
# and the dashboard renders completely unstyled. Restart to pick up the build.
bash scripts/build_frontend.sh
sudo systemctl restart claude-soma-frontend.service
EOSSH

echo "✓ Deployed to $HOST:$REMOTE"
