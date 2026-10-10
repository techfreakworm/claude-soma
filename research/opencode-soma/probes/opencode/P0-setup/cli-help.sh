#!/usr/bin/env bash
# Save `opencode --help` and every subcommand's --help (sandbox env) to evidence/opencode/cli-help.log
set -u
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P0-setup/cli-help.sh"
PROBE_ID=P0-clihelp; source "$(dirname "$0")/../sandbox.sh"
cd "$PROJ" || exit 1
ev_open cli-help.log "full CLI surface of opencode 1.18.8: top-level and every subcommand --help" "none"
ev_run "$OC" --help
for sub in completion acp mcp attach run debug providers agent upgrade uninstall serve web models stats export import github pr session plugin db; do
  ev_run "$OC" "$sub" --help
done
# second-level subcommands discovered from the first-level help output
for pair in "mcp add" "mcp list" "mcp auth" "mcp logout" "mcp debug" \
            "debug config" "debug lsp" "debug rg" "debug file" "debug scrap" "debug skill" "debug snapshot" "debug startup" "debug agent" "debug v2" "debug info" "debug paths" "debug wait" \
            "providers list" "providers login" "providers logout" \
            "agent create" "agent list" \
            "session list" "session delete" \
            "db path" "db query" "db migrate" \
            "github install" "github run"; do
  # shellcheck disable=SC2086
  ev_run "$OC" $pair --help
done
if grep -q 'opencode run \[message..\]' "$EV_LOG" && grep -q 'opencode serve' "$EV_LOG"; then
  ev_verdict PASS "help for the top level and every subcommand captured from the 1.18.8 binary inside the sandbox."
else
  ev_verdict FAIL "help output incomplete."
fi
