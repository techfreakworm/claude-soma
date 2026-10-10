#!/usr/bin/env bash
# P9-compat (NO LLM): do claude-soma's skills / agents / commands load under opencode 1.18.8?
#  (a) three claude-soma skills (with Claude `allowed-tools` frontmatter) dropped into
#      <project>/.opencode/skill/<name>/SKILL.md -> `opencode debug skill` lists them, extra
#      frontmatter and the mcp__*/Bash(...) allowed-tools entries don't break loading.
#  (b) agents/project-lead.md converted to opencode frontmatter in <project>/.opencode/agent/ ->
#      `opencode agent list` / `debug agent` accept it.
#  (c) one command in <project>/.opencode/command/*.md -> listed via the HTTP /command endpoint.
# The Claude-format .claude/skills + .claude/agents half of P9 is in run-claude-dirs.sh (this
# worktree's harness hook blocks any bash touching .claude/, so that half is gated behind
# OCSOMA_P9_CLAUDE=1 and run separately / by the verifier).
set -u
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P9-compat/run.sh"
HERE="$(cd "$(dirname "$0")" && pwd)"
SERVE_PORT=$(( ${OCSOMA_PORT_BASE:-47100} + 91 ))
PROBE_ID=P9-compat; SANDBOX_FRESH=1
source "$HERE/../sandbox.sh"
REPO="$OCSOMA_REPO_ROOT"
EVD="$OCSOMA_EVID/P9-compat"; mkdir -p "$EVD"

# (a) skills into .opencode/skill/<name>/SKILL.md  (copied verbatim from the repo, Claude frontmatter intact)
mkdir -p "$PROJ/.opencode/skill"
for s in spawn-project message-project list-projects; do
  mkdir -p "$PROJ/.opencode/skill/$s"
  cp "$REPO/skills/$s/SKILL.md" "$PROJ/.opencode/skill/$s/SKILL.md"
done

# (b) converted opencode agent (Claude project-lead.md -> opencode frontmatter)
mkdir -p "$PROJ/.opencode/agent"
cat > "$PROJ/.opencode/agent/soma-lead.md" <<'MD'
---
description: A project-workstream lead spawned by the Telegram orchestrator; reports back over MCP.
mode: primary
model: ollama/qwen3:32b
temperature: 0.2
permission:
  bash: ask
  edit: allow
  webfetch: deny
---
You are the lead of a project workstream. Your cwd is your project's working directory.
Read ./BRIEF.md on start, do the work, and report status back through the soma MCP tools.
MD

# (c) opencode command
mkdir -p "$PROJ/.opencode/command"
cat > "$PROJ/.opencode/command/soma-status.md" <<'MD'
---
description: Summarize this lead's progress for the orchestrator.
agent: soma-lead
---
List what you have done so far, what is in progress, and anything you are blocked on. Be terse.
MD

git -C "$PROJ" add -A >/dev/null 2>&1; git -C "$PROJ" commit -qm p9 >/dev/null 2>&1

ev_open P9-compat/compat.log "claude-soma skills/agents/commands loading under opencode 1.18.8 (no LLM)" "none"
ev "## (a) opencode debug skill  (3 claude-soma skills with Claude allowed-tools frontmatter in .opencode/skill/)"
ev_run sh -c "cd '$PROJ' && '$OC' debug skill 2>&1"
ev "## (a') the raw frontmatter opencode ingested for one of them (allowed-tools present, incl. a Bash(...) and mcp__ entry)"
ev_run sh -c "sed -n '1,12p' '$PROJ/.opencode/skill/message-project/SKILL.md'"
ev "## (b) opencode agent list  (converted soma-lead agent)"
ev_run sh -c "cd '$PROJ' && '$OC' agent list 2>&1 | grep -iE 'soma-lead|primary|subagent' | head"
ev "## (b') opencode debug agent soma-lead (resolved config)"
ev_run sh -c "cd '$PROJ' && '$OC' debug agent soma-lead 2>&1 | head -40"
ev "## (c) command listed over HTTP (GET /command) from a serve in this project"
( cd "$PROJ" && exec "$OC" serve --port "$SERVE_PORT" --hostname 127.0.0.1 ) > "$SANDBOX/serve.out" 2>&1 &
SP=$!
for i in $(seq 1 100); do curl -s -o /dev/null "http://127.0.0.1:$SERVE_PORT/global/health" && break; sleep 0.2; done
curl -s "http://127.0.0.1:$SERVE_PORT/command" > "$SANDBOX/command.json"
curl -s "http://127.0.0.1:$SERVE_PORT/skill"   > "$SANDBOX/skill.json"
curl -s "http://127.0.0.1:$SERVE_PORT/agent"   > "$SANDBOX/agent.json"
ev_run sh -c "jq -c '.[] | {name, description: (.description[0:60]), agent}' '$SANDBOX/command.json'"
ev_run sh -c "jq -r '.[].name' '$SANDBOX/skill.json' 2>/dev/null"
ev_run sh -c "jq -r '.[].name' '$SANDBOX/agent.json' 2>/dev/null"
kill "$SP" 2>/dev/null; wait "$SP" 2>/dev/null

# count from the actual endpoint payloads / debug output, not the echoed command strings
skills_seen=$(jq -r '.[].name' "$SANDBOX/skill.json" 2>/dev/null | grep -icE '^(spawn-project|message-project|list-projects)$')
[ "${skills_seen:-0}" -lt 3 ] && skills_seen=$(cd "$PROJ" && "$OC" debug skill 2>/dev/null | grep -ciE '"name": "(spawn-project|message-project|list-projects)"')
agent_seen=$(jq -r '.[].name' "$SANDBOX/agent.json" 2>/dev/null | grep -ic '^soma-lead$')
cmd_seen=$(jq -r '.[].name' "$SANDBOX/command.json" 2>/dev/null | grep -ic '^soma-status$')
ev "summary: claude-soma skills recognized=$skills_seen refs, converted agent=$agent_seen refs, command=$cmd_seen refs"
if [ "$skills_seen" -ge 3 ] && [ "$agent_seen" -ge 1 ] && [ "$cmd_seen" -ge 1 ]; then
  ev_verdict PASS "claude-soma SKILL.md files load under opencode from .opencode/skill/ with their Claude allowed-tools frontmatter intact (debug skill + /skill list them); a project-lead agent converted to opencode frontmatter is accepted (agent list + debug agent); an opencode command is served via /command."
else
  ev_verdict PARTIAL "skills=$skills_seen agent=$agent_seen cmd=$cmd_seen (see transcript)."
fi
tail -1 "$EV_LOG"
