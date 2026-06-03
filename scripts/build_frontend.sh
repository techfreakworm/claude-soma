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

pnpm install --prod=false || {
    echo "ERROR: pnpm install FAILED in frontend/" >&2
    echo "  Common cause: pnpm 10 ignored-builds. If you see ERR_PNPM_IGNORED_BUILDS above," >&2
    echo "  verify frontend/package.json has pnpm.onlyBuiltDependencies listing every native dep." >&2
    exit 1
}

pnpm run build || {
    echo "ERROR: next build FAILED in frontend/" >&2
    exit 1
}

# Copy static + public NEXT TO the standalone server.js. Use rm -rf + cp (not
# `cp -rf src dst`) so this is rebuild-safe: if the target dir already exists
# from a previous deploy, `cp -rf .next/static .next/standalone/.next/static`
# would nest the assets one level too deep (.../static/static) and they'd 404
# again. mkdir -p guards the (build-created) parent.
mkdir -p .next/standalone/.next
rm -rf .next/standalone/.next/static .next/standalone/public
cp -r .next/static .next/standalone/.next/static
cp -r public       .next/standalone/public

if [[ ! -d .next/standalone/.next/static ]]; then
    echo "ERROR: .next/standalone/.next/static was NOT created by the copy step" >&2
    exit 1
fi
echo "static assets copied"
ls .next/standalone/server.js >/dev/null && echo "server.js present"
