# Orchestrator hard gates — design note

Date: 2026-05-29
Full design: `/home/ubuntu/projects/soma-improver/PLAN-orchestrator-gates.md`

## Why

The bot (channel-claude) is a routing layer, not a worker. Before this change,
two problems coexisted:

1. **Effort waste.** `--effort max` burned reasoning tokens on "ack and
   dispatch" decisions that need at most ten words and one tool call.

2. **Dispatch drift.** The dispatch rule lived only in `responsive_bot.md` as
   a soft convention. Under load or subtle prompt drift the bot occasionally
   ran substantive work inline — blocking the channel for minutes.

## Default effort: LOW

`channel-claude.sh` now passes `--effort low`. Routing and acking are
lightweight; deep reasoning belongs inside the dispatched Agents which run
at `model=opus`. If real-world traffic shows LOW degrades routing accuracy
enough that the gate keeps firing spuriously on every turn, revisit MEDIUM
— but LOW is correct until there is evidence otherwise.

## Hook contract

The gate lives at `scripts/orchestrator_gate.sh`. It fires on every
`PreToolUse` event because `hooks/hooks.json` registers it with
`"matcher": ".*"` (the script does the actual filtering, which is easier to
read and test than a regex in JSON).

The hook is **fail-open by design** (PLAN section 5-6):

- Missing `jq` binary: `command -v jq || exit 0` — exits 0, no output,
  tool proceeds.
- Malformed or empty JSON stdin: jq fails silently, `TOOL` is empty,
  `[[ -z "$TOOL" ]] && exit 0` — exits 0, no output.
- Any other internal error: `set -uo pipefail` (no `-e`) means grep/jq
  failures do not abort the script; they produce empty output and fall
  through to the allow path.
- Only `exit 0 + permissionDecision: "deny"` actually blocks. All other
  exit codes are treated as non-blocking errors by the Claude Code hook
  contract.

The hook is **plugin-scoped** — it loads only when Claude is launched with
`--plugin-dir` (channel-claude only). Lead sessions use `--mcp-config` and
never load plugin hooks, so their sonnet+max+sequential-thinking profile is
unaffected.

## Deny-list summary

The script (`scripts/orchestrator_gate.sh`) is the source of truth. Summary:

**Tool-name level:**
- `Edit`, `Write`, `NotebookEdit` — file edits
- `WebFetch`, `WebSearch` — network + multi-step
- `Skill` — runs inline, blocks channel
- `mcp__playwright*` — browser automation (all playwright servers)
- `mcp__claude_ai_*` — OAuth-heavy integrations (Canva, Gmail, Calendar, Drive)
- `mcp__huggingface__gr1_z_image_turbo_generate` — image gen (minutes)
- `mcp__huggingface__dynamic_space` — heavy compute

**Bash command patterns:**
- Package installs: `apt/apt-get install`, `pip[3] install`, `pipx install`,
  `npm install`, `pnpm install/add`, `yarn add/install`, `cargo build/install/test`,
  `bun install`
- Network git: `git clone`, `git pull`, `git push`, `git fetch --depth`
- Builds/tests: `docker build`, `docker run`, `make`, `cmake`, `pytest`,
  `npm test`, `pnpm test`
- Heavy compute: `codex`, `ffmpeg -i`, `whisper-cli -f`
- Network curl/wget (non-localhost): denied unless target is
  `localhost`, `127.0.0.1`, or `0.0.0.0`

Everything else passes through (fail-open philosophy — see PLAN section 6).

## How to add a pattern

Edit `scripts/orchestrator_gate.sh` and add a `case` branch or `grep -qE`
pattern to the appropriate section. Restart the channel to pick up the change.

## How to disable

Set `SOMA_ORCHESTRATOR_GATE_DISABLED=1` in `/etc/claude-soma/secrets.env`,
then restart the channel (`sudo systemctl restart claude-soma-channel.service`
from a separate terminal — not from within the bot session). The gate script
checks this variable at the top and exits 0 immediately without evaluating
any deny rules.

## Channel restart required

All changes in this commit require a channel restart to take effect. Bundle
with the KNOWN_BUGS #3 deploy and T1-T5 acceptance window:

- Bot runs at `--effort low`
- Gate hook fires on every `PreToolUse`
- Empirical check: dispatch a trivial Agent from the bot; lead-side watcher
  confirms no second `bun server.ts` spawns inline
