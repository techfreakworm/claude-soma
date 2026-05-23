#!/usr/bin/env bash
# scripts/deploy.sh — sync claude-soma repo to OCI VPS and install Python deps.

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
EOSSH

echo "✓ Deployed to $HOST:$REMOTE"
