#!/usr/bin/env bash
# Fetch @opencode-ai/plugin@1.18.8 and @opencode-ai/sdk@1.18.8 tarballs into the sandbox (npm pack, no install scripts).
set -u
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P2-hooks/fetch-types.sh"
PROBE_ID=P2-types; source "$(dirname "$0")/../sandbox.sh"
DST="$SANDBOX/npm"; mkdir -p "$DST/plugin" "$DST/sdk"
export npm_config_cache="$SANDBOX/npm-cache" npm_config_userconfig="$SANDBOX/npmrc" npm_config_ignore_scripts=true
: > "$SANDBOX/npmrc"
ev_open P2-hooks/fetch-types.log "authoritative 1.18.8 plugin/sdk type definitions fetched into the sandbox" "none"
ev_run npm view @opencode-ai/plugin@1.18.8 version dist.tarball dependencies
ev_run npm view @opencode-ai/sdk@1.18.8 version dist.tarball
ev_run npm pack @opencode-ai/plugin@1.18.8 --pack-destination "$DST"
ev_run npm pack @opencode-ai/sdk@1.18.8 --pack-destination "$DST"
ev_run tar -xzf "$DST/opencode-ai-plugin-1.18.8.tgz" -C "$DST/plugin"
ev_run tar -xzf "$DST/opencode-ai-sdk-1.18.8.tgz" -C "$DST/sdk"
ev_run find "$DST/plugin" "$DST/sdk" -name '*.d.ts'
if [ -f "$DST/plugin/package/dist/index.d.ts" ] && [ -n "$(find "$DST/sdk" -name 'types.gen.d.ts' | head -1)" ]; then
  ev_verdict PASS "plugin and sdk 1.18.8 tarballs fetched and unpacked into the sandbox."
else
  ev_verdict FAIL "type definitions not found after unpacking."
fi
echo "$DST"
