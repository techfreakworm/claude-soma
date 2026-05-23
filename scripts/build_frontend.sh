#!/usr/bin/env bash
# scripts/build_frontend.sh - pnpm build + copy static assets next to standalone server.
#
# Why this script exists: Next.js output: "standalone" produces a minimal
# server.js but does NOT copy .next/static/ or public/ alongside it. Without
# this copy, the systemd unit's ExecStart of `node .next/standalone/server.js`
# serves HTML but every CSS/JS/font asset 404s. This script does the build
# and the copy in one shot so the standalone tree is self-contained.

set -euo pipefail

cd "$(dirname "$0")/../frontend"

if [[ -z "${AUTH_SECRET:-}" ]]; then
    if [[ -f .env.local ]] && grep -q '^AUTH_SECRET=' .env.local; then
        :  # Will be picked up by next from .env.local
    else
        export AUTH_SECRET=placeholder-soma-build-only
    fi
fi

pnpm install --prod=false
pnpm build

cp -rf .next/static .next/standalone/.next/static
cp -rf public      .next/standalone/public

ls .next/standalone/.next/static >/dev/null && echo "static assets copied"
ls .next/standalone/server.js     >/dev/null && echo "server.js present"
