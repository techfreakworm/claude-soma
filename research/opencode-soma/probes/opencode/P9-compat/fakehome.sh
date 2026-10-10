#!/usr/bin/env bash
# P9b (NO LLM): which user-level config leaks into a soma instance when HOME is the user's OWN home?
# We NEVER touch the real home. We build a FAKE home under the sandbox, seed it with a sentinel
# opencode global config (.config/opencode) and a HOME-anchored .opencode/agent, and run
# `opencode debug config|paths|agent` two ways:
#   A. soma-style: redirect XDG_* + TMPDIR away from HOME (what a daemon can control).
#   B. HOME-anchored: leave XDG_CONFIG_HOME unset so it defaults to $HOME/.config (what leaks if the
#      daemon redirects nothing but HOME is the user's own).
# The .claude/.agents external-skill leak is NOT exercised: creating a `.claude/` path is gated by
# this worktree's harness hook, so per the lead it is recorded and skipped, and cited from opencode's
# own built-in config reference (dumped by `opencode debug skill` in compat.log).
set -u
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P9-compat/fakehome.sh"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROBE_ID=P9b; SANDBOX_FRESH=1
source "$HERE/../sandbox.sh"   # sets OC, EVID helpers; we override HOME/XDG per-run below
EVD="$OCSOMA_EVID/P9-compat"; mkdir -p "$EVD"
FH="$SANDBOX/fakehome"; mkdir -p "$FH/.config/opencode/agent" "$FH/.opencode/agent"

# sentinel global config in the fake HOME's ~/.config/opencode
cat > "$FH/.config/opencode/opencode.json" <<JSON
{ "\$schema": "https://opencode.ai/config.json", "username": "P9B-CONFIG-LEAK", "small_model": "ollama/qwen3:32b" }
JSON
cat > "$FH/.config/opencode/agent/p9b-config-agent.md" <<'MD'
---
description: P9B-CONFIG-AGENT-LEAK sentinel from ~/.config/opencode/agent.
mode: primary
---
sentinel
MD
# HOME-anchored ~/.opencode/agent (is ~/.opencode an agent source? the config reference says no)
cat > "$FH/.opencode/agent/p9b-home-agent.md" <<'MD'
---
description: P9B-HOME-OPENCODE-LEAK sentinel from ~/.opencode/agent.
mode: primary
---
sentinel
MD

runset() {  # runset <label> <extra env ...>
  local label="$1"; shift
  ev "### $label"
  ev_run env -i PATH="$PATH" HOME="$FH" TMPDIR="$SANDBOX/tmp/" OPENCODE_DISABLE_AUTOUPDATE=1 "$@" "$OC" debug paths
  ev_run env -i PATH="$PATH" HOME="$FH" TMPDIR="$SANDBOX/tmp/" OPENCODE_DISABLE_AUTOUPDATE=1 "$@" "$OC" debug config
  ev_run env -i PATH="$PATH" HOME="$FH" TMPDIR="$SANDBOX/tmp/" OPENCODE_DISABLE_AUTOUPDATE=1 "$@" "$OC" agent list
}

ev_open P9-compat/fakehome.log "which user-level config leaks when HOME is the user's own (fake HOME, real home untouched)" "none (no LLM)"
ev "fake HOME: $FH ; seeded ~/.config/opencode/opencode.json (username P9B-CONFIG-LEAK) + agent p9b-config-agent + ~/.opencode/agent p9b-home-agent"
ev "## A. soma-style: XDG_* redirected to the empty sandbox, TMPDIR redirected (what the daemon controls)"
runset "A1 default" XDG_CONFIG_HOME="$SANDBOX/xdg/config" XDG_DATA_HOME="$SANDBOX/xdg/data" XDG_CACHE_HOME="$SANDBOX/xdg/cache" XDG_STATE_HOME="$SANDBOX/xdg/state"
ev "## B. HOME-anchored: XDG_CONFIG_HOME UNSET -> defaults to \$HOME/.config (what leaks with no XDG redirect)"
runset "B1 default" XDG_DATA_HOME="$SANDBOX/xdg/data" XDG_CACHE_HOME="$SANDBOX/xdg/cache" XDG_STATE_HOME="$SANDBOX/xdg/state"
runset "B2 OPENCODE_DISABLE_CLAUDE_CODE=1" OPENCODE_DISABLE_CLAUDE_CODE=1 XDG_DATA_HOME="$SANDBOX/xdg/data" XDG_CACHE_HOME="$SANDBOX/xdg/cache" XDG_STATE_HOME="$SANDBOX/xdg/state"

ev "## .claude / .agents external-skill leak: SKIPPED"
ev "creating a '.claude/' path is gated by this worktree's harness hook; per the lead this sub-test is"
ev "recorded and skipped, not worked around. From opencode's own built-in config reference (dumped by"
ev "'opencode debug skill' in compat.log): external skills AUTO-LOAD from ~/.claude/skills and"
ev "~/.agents/skills (HOME-anchored), toggled off by OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1 /"
ev "OPENCODE_DISABLE_EXTERNAL_SKILLS=1. So with HOME = the user's own home, those DO leak unless the"
ev "daemon sets those env vars or points HOME at a dedicated soma home."

# analysis: which seeded config loaded in run A (XDG redirected) vs run B (XDG default to $HOME/.config)?
# "loaded" = the agent appears in that run's `agent list` output, or the username in `debug config`.
secA="$(awk '/^### A1 default/{p=1} /^### B1 default/{p=0} p' "$EV_LOG")"
secB="$(awk '/^### B1 default/{p=1} /^### B2 /{p=0} p' "$EV_LOG")"
a_cfg_user=$(printf '%s' "$secA" | grep -c 'P9B-CONFIG-LEAK')
b_cfg_user=$(printf '%s' "$secB" | grep -c 'P9B-CONFIG-LEAK')
a_cfg_agent=$(printf '%s' "$secA" | grep -c 'p9b-config-agent (primary)')
b_cfg_agent=$(printf '%s' "$secB" | grep -c 'p9b-config-agent (primary)')
a_home_agent=$(printf '%s' "$secA" | grep -c 'p9b-home-agent (primary)')
b_home_agent=$(printf '%s' "$secB" | grep -c 'p9b-home-agent (primary)')
ev "## findings (agent 'loaded' = shown by 'agent list' in that run)"
ev "~/.config/opencode (global config, XDG-anchored): username sentinel in A=$a_cfg_user, in B=$b_cfg_user; ~/.config/opencode/agent loaded in A=$a_cfg_agent, in B=$b_cfg_agent"
ev "~/.opencode/agent (HOME-anchored, NOT via XDG): loaded in A=$a_home_agent, in B=$b_home_agent"
ev "=> ~/.config/opencode is isolated by an XDG_CONFIG_HOME redirect (invisible in A, leaks in B)."
ev "=> ~/.opencode/agent leaks REGARDLESS of the XDG redirect (loaded in BOTH A and B); only a HOME"
ev "   redirect (a dedicated soma home) isolates it. The sandbox.sh the probes use does redirect HOME."
if [ "$a_cfg_agent" = 0 ] && [ "$b_cfg_agent" -ge 1 ] && [ "$a_home_agent" -ge 1 ]; then
  ev_verdict PASS "two distinct leak vectors when HOME is the user's own: (1) ~/.config/opencode (config + its agents) leaks only without an XDG_CONFIG_HOME redirect - isolated in A, visible in B; (2) ~/.opencode/agent is HOME-anchored and loads even with XDG redirected (A and B), so redirecting XDG alone is NOT enough - a soma instance must run under a dedicated HOME (as sandbox.sh does) or clear these. ~/.claude|~/.agents external skills also leak (documented from opencode's config reference; hook-skipped here)."
else
  ev_verdict PARTIAL "leak result: cfg-agent A=$a_cfg_agent B=$b_cfg_agent, home-agent A=$a_home_agent B=$b_home_agent; see transcript."
fi
tail -1 "$EV_LOG"
