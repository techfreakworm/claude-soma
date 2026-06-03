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

# pnpm 10 quirks defensively handled:
#   - The deprecated `pnpm` field in package.json is IGNORED (warning emitted).
#     Canonical config now lives in frontend/pnpm-workspace.yaml.
#   - In non-TTY contexts (CI / bootstrap.sh via `sudo -u ubuntu bash ...`),
#     pnpm exits non-zero with ERR_PNPM_IGNORED_BUILDS even when those deps
#     are listed in onlyBuiltDependencies (the YAML is respected for the
#     allow-list semantics but the gate still trips without TTY confirmation).
#     Detect that exact case and run `pnpm rebuild` for the allowed deps so
#     their native binaries actually get built.
ALLOWED_BUILDS="@tailwindcss/oxide esbuild msw sharp unrs-resolver"
PNPM_LOG=$(mktemp)

if pnpm install --prod=false --config.onlyBuiltDependencies="$(echo $ALLOWED_BUILDS | tr ' ' ,)" 2>&1 | tee "$PNPM_LOG"; then
    PNPM_INSTALL_EXIT=0
else
    PNPM_INSTALL_EXIT=${PIPESTATUS[0]}
fi

if [[ $PNPM_INSTALL_EXIT -ne 0 ]]; then
    if grep -q 'ERR_PNPM_IGNORED_BUILDS\|Ignored build scripts' "$PNPM_LOG"; then
        echo "Note: pnpm 10 strict-mode tripped on ignored builds. Forcing rebuild:" >&2
        for pkg in $ALLOWED_BUILDS; do
            pnpm rebuild "$pkg" 2>&1 | tail -1 || true
        done
        PNPM_INSTALL_EXIT=0
    fi
fi
rm -f "$PNPM_LOG"

if [[ $PNPM_INSTALL_EXIT -ne 0 ]]; then
    echo "ERROR: pnpm install FAILED in frontend/" >&2
    echo "  Diagnosis:" >&2
    echo "    1. Verify frontend/pnpm-workspace.yaml has onlyBuiltDependencies" >&2
    echo "       containing every native dep (sharp, msw, etc.)" >&2
    echo "    2. The deprecated 'pnpm' field in frontend/package.json is IGNORED by pnpm 10." >&2
    echo "    3. To temporarily unblock: cd frontend && pnpm approve-builds (interactive)" >&2
    exit 1
fi

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
