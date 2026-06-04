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

# pnpm 10 quirks defensively handled.
#
# CONFIGURATION:
#   - The deprecated `pnpm` field in package.json is IGNORED by pnpm 10
#     (warning emitted). Canonical config lives in frontend/pnpm-workspace.yaml
#     under `onlyBuiltDependencies`.
#
# THE RE-RUN TRAP we hit on real fresh installs:
#   - First bootstrap attempt: pnpm install creates node_modules + trips
#     ERR_PNPM_IGNORED_BUILDS in non-TTY mode → exit non-zero.
#   - Second attempt (operator re-runs bootstrap): pnpm install sees
#     "Lockfile is up to date / Already up to date" and SKIPS the install
#     phase entirely — including the allowed-build scripts. ERR fires again.
#     Re-running `pnpm install` does NOT force build scripts when deps are
#     already on disk.
#
# THE ROBUST RECIPE:
#   1. pnpm install (best-effort — non-fatal on ignored-builds error code)
#   2. ALWAYS `pnpm rebuild <native-deps>` afterwards — idempotent + cheap
#      when already built; forces the native binaries to materialize when
#      not. Safe to run unconditionally.
#   3. Verify sharp loadable (the only native dep next-server actually requires
#      at runtime — Tailwind oxide breaks styles but the server starts).
#   4. If sharp still won't load: `rm -rf node_modules && pnpm install`
#      (clean slate where onlyBuiltDependencies fires from scratch).
ALLOWED_BUILDS="@tailwindcss/oxide esbuild msw sharp unrs-resolver"

# THE KEY FLAG: --config.strict-dep-builds=false
#
# pnpm 10 has TWO independent gates on dep build scripts:
#   1. onlyBuiltDependencies (allow-list in pnpm-workspace.yaml) — controls
#      WHICH deps may run scripts.
#   2. strict-dep-builds — when true (the default in CI/non-TTY contexts),
#      ANY dep with a build script that is not yet "approved" makes
#      pnpm install FATAL with ERR_PNPM_IGNORED_BUILDS, even when the dep
#      IS in onlyBuiltDependencies.
#
# A fresh `pnpm install` on a clean VPS trips gate #2 before gate #1 has any
# effect. The fix is to disable strict-dep-builds for the install (we manually
# verify the builds happened after via pnpm rebuild + filesystem check).
run_pnpm_install() {
    local log
    log=$(mktemp)
    local exit_code=0
    pnpm install --prod=false --config.strict-dep-builds=false 2>&1 | tee "$log" || exit_code=${PIPESTATUS[0]}
    # If still failing for reasons other than ignored-builds, bail (clean install can retry).
    if [[ $exit_code -ne 0 ]] && ! grep -q 'ERR_PNPM_IGNORED_BUILDS\|Ignored build scripts' "$log"; then
        rm -f "$log"
        return 1
    fi
    rm -f "$log"
    return 0
}

force_rebuild_natives() {
    # pnpm rebuild forces build scripts to run for ALLOWED deps regardless of
    # the strict-dep-builds gate. Idempotent + no-op if already built.
    for pkg in $ALLOWED_BUILDS; do
        pnpm rebuild "$pkg" 2>&1 | tail -3 || true
    done
}

sharp_binary_present() {
    # sharp is a transitive of next (not a direct frontend dep), so we can't
    # require() it from frontend/. Verify the native binary file exists in the
    # pnpm content-addressed store directly.
    # Path shape on linux (any libc, any arch):
    #   node_modules/.pnpm/@img+sharp-{linux,linuxmusl}-{x64,arm64,...}@*/node_modules/@img/sharp-*/lib/sharp-*.node
    # That's 6 dir levels deep from .pnpm — give find room for variance.
    find node_modules/.pnpm -maxdepth 8 -name 'sharp-*.node' 2>/dev/null | head -1 | grep -q .
}

# === Phase 1: install + rebuild ===
if ! run_pnpm_install; then
    echo "ERROR: pnpm install FAILED in frontend/ (non-ignored-builds error)" >&2
    exit 1
fi
force_rebuild_natives

# === Phase 2: verify sharp's native binary is present; clean-install fallback if not ===
if ! sharp_binary_present; then
    echo "Note: sharp native binary not detected — clean-installing node_modules..." >&2
    rm -rf node_modules
    if ! run_pnpm_install; then
        echo "ERROR: clean pnpm install FAILED in frontend/" >&2
        exit 1
    fi
    force_rebuild_natives
    if ! sharp_binary_present; then
        echo "ERROR: sharp native binary still missing after clean install + rebuild." >&2
        echo "  Diagnosis:" >&2
        echo "    1. Verify frontend/pnpm-workspace.yaml lists onlyBuiltDependencies" >&2
        echo "       including 'sharp' (and 'msw', '@tailwindcss/oxide')." >&2
        echo "    2. Check that pnpm 10 is the right version: pnpm --version" >&2
        echo "    3. Look for sharp under node_modules/.pnpm/@img+sharp-*" >&2
        echo "    4. Manual unblock: cd frontend && rm -rf node_modules && \\" >&2
        echo "         pnpm install --config.strict-dep-builds=false && pnpm rebuild sharp" >&2
        exit 1
    fi
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
