# Claude Soma — Repo-Level Instructions

When working in this repo, follow these. They override defaults where they conflict.

## What this is

Claude Soma is a Claude Code plugin + companion services that wraps Claude Code with messaging, voice, project orchestration, and a dashboard. Hermes-Agent's product surface in ~10% the code. See [README.md](README.md) for the pitch, [docs/superpowers/specs/2026-05-22-hermes-claude-design.md](docs/superpowers/specs/2026-05-22-hermes-claude-design.md) for the design, and [docs/superpowers/plans/2026-05-22-hermes-claude-v1.md](docs/superpowers/plans/2026-05-22-hermes-claude-v1.md) for the 49-task implementation plan.

## Status

- **Spec + plan**: frozen (commits `81513a2`, `453c167`).
- **Week 1 code**: complete (tag `week-1-code-complete`).
- **Week 2 code**: complete (tag `week-2-code-complete`).
- **Week 3 (dashboard)**: not started. Begin at Task 29 of the plan.
- **Week 4 (polish)**: not started. Begin at Task 42.
- **User-action items** (VPS provisioning, OAuth, Telegram pairing, systemd installs): pending — see [NEXT.md](NEXT.md).

## Hard conventions

### Identity

- Package: `claude_soma` (Python), `claude-soma` (PyPI / plugin name). NOT `hermes-claude` or `hermes_claude` (legacy — only appears in historical doc filenames).
- Deployment paths on OCI VPS:
  - Code root: `/opt/claude-soma/`
  - Secrets: `/etc/claude-soma/secrets.env` (`CLAUDE_CODE_OAUTH_TOKEN`, `AUTH_GITHUB_*`, etc.)
  - Logs: `/var/log/claude-soma/`
  - Activity log: `~/.claude-soma/activity.jsonl` (per-user, NOT in `/var/log`)
  - Registry: `/opt/claude-soma/registry.sqlite`
  - Usage DB: `/opt/claude-soma/usage.sqlite`
- Public URL: `claude.mayankgupta.in`
- GitHub repo: `techfreakworm/claude-soma`
- Local working copy: `/Users/techfreakworm/Projects/llm/hermes-claude/` (directory name kept as `hermes-claude` for the M5 Max dev machine; the GitHub repo and deploy target are `claude-soma`).

### Auth

- **No Anthropic API key. Ever.** All Claude usage goes through Max OAuth via `CLAUDE_CODE_OAUTH_TOKEN`. If a task seems to require an API key, surface that as a problem — don't quietly add one.
- Codex CLI is the user's *separate* ChatGPT subscription. Image generation delegates there to keep Max credits for reasoning.

### Git

- **User is sole author on every commit.** No `Co-Authored-By` lines. No "Generated with Claude Code" footers. No emoji in commit messages.
- Conventional-style subject lines preferred but not enforced: `prefix: summary` where prefix ∈ {feat, fix, refactor, test, docs, deploy, voice_stt, etc.}.
- Commit only when explicitly asked. Push after each approved task to `origin/main`.
- Two milestone tags exist: `week-1-code-complete`, `week-2-code-complete`. Add `week-3-code-complete` and `week-4-code-complete` as those phases finish.

### Subagents

- **All Agent tool dispatches use `model="opus"`.** Never Sonnet or Haiku, even for trivial tasks. This is a standing user preference.
- The subagent-driven-development workflow (from the superpowers plugin) is the cadence: implementer → spec-compliance reviewer → code-quality reviewer → fix loop if needed → mark complete → next task.
- Don't make the subagent read the plan file — pass the relevant task text inline in the dispatch prompt. Reading the plan inflates the subagent's context unnecessarily.

### Code style

- Python 3.12, ruff line-length 100, mypy strict, pytest with `asyncio_mode = "auto"`.
- Subprocess pattern (set by `voice_stt` and `voice_tts`, mirrored elsewhere): wrap every `subprocess.run` in `try/except CalledProcessError, TimeoutExpired` and re-raise `RuntimeError` with the binary name + the last 500 chars of stderr. Always pass `timeout=N` — never block indefinitely.
- No emoji in code, comments, or commit messages. (Documentation is OK if the user wants them, but default off.)
- Default to writing no comments. Add them only when the *why* is non-obvious.
- Tests-folder convention from user memory: `tests/` inside the project repo is fine for pyproject-managed packages. The "don't pollute Projects root with venvs" rule is about ad-hoc one-off scripts, not pyproject venvs — `.venv` inside this repo is gitignored and correct.

### Skill conventions

- Skill files live at `skills/<name>/SKILL.md` with YAML frontmatter (`name`, `description`, optional `allowed-tools`, `model`, `memory`, etc.).
- Subagent templates live at `agents/<name>.md`.
- All YAML frontmatter must parse with `yaml.safe_load`.
- `allowed-tools` for skills that use MCP tools follows the pattern `mcp__<server-name>__<tool-name>`. For Bash with scoped patterns: `Bash(<glob>)`. For built-in tools: just `SendMessage`, `Read`, etc.

### MCP server conventions

- One server per `src/claude_soma/mcp_servers/<name>/` directory, with `server.py` exposing `@mcp.tool()` decorators and a `main()` entry point.
- Each new server gets a stanza in `.mcp.json` at repo root:
  ```json
  "<server-name>": {
    "type": "stdio",
    "command": "/opt/claude-soma/.venv/bin/python",
    "args": ["-m", "claude_soma.mcp_servers.<name>.server"],
    "env": { ... },
    "alwaysLoad": true
  }
  ```
- Env var names use legacy `HERMES_*` prefix (e.g. `HERMES_WHISPER_BIN`, `HERMES_PIPER_BIN`, `HERMES_ORCH_DB`) — kept stable as interface contracts even though the package is renamed. Don't rebrand env var names without a coordinated server.py + .mcp.json + systemd unit change.

### Deployment paths in scripts and systemd units

When writing systemd units, scripts, deploy artifacts, or wizard code:

- Always use `/opt/claude-soma/...`, NOT `/opt/hermes-claude/...` (the plan document text uses the legacy paths; that's historical — rebrand in all new code).
- systemd unit naming: `claude-soma-<role>.service` / `.timer`.
- Caddy config target: `claude.mayankgupta.in`.

### What's intentionally NOT being rebranded

- The tmux session name `hermes` inside `systemd/claude-soma-channel.service` — would require coordinated changes to healthcheck.sh and runtime ergonomics; V1.5 deferral.
- The `--add-dir /home/ubuntu/hermes-work` work directory — V1.5 deferral.
- Env var names (`HERMES_*` prefix) — interface stability; V1.5 deferral.
- Spec/plan filenames (`2026-05-22-hermes-claude-*.md`) — historical artifacts; don't rename.

## Where to look when stuck

| If you're trying to... | Look at... |
|---|---|
| Understand the architecture | `docs/superpowers/specs/2026-05-22-hermes-claude-design.md` |
| Find the next task to implement | `docs/superpowers/plans/2026-05-22-hermes-claude-v1.md` + `NEXT.md` |
| Add a new MCP server | Copy the shape of `src/claude_soma/mcp_servers/voice_stt/server.py` (subprocess pattern) or `project_orchestrator/server.py` (FastMCP + helpers) |
| Add a new skill | Copy the shape of `skills/spawn-project/SKILL.md` (with allowed-tools) or `skills/voice-action/SKILL.md` (without) |
| Wire a new MCP server into the plugin | Append to `.mcp.json`, follow the existing block pattern |
| Add a systemd unit | Copy the shape of `systemd/claude-soma-channel.service` |
| Understand testing conventions | `tests/conftest.py` (sample_wav fixture pattern), `tests/mcp_servers/test_*.py` (mocking subprocess, TDD red-then-green) |
| Resume work in a fresh session | `NEXT.md` |

## Things to avoid

- Don't add files that aren't on the plan without flagging it first.
- Don't reformat unrelated code while making a targeted edit.
- Don't introduce a new dependency without discussing — the dependency list in `pyproject.toml` was deliberately curated.
- Don't "improve" the spec or plan documents — they're frozen. Surface discrepancies as commit-time notes or open V1.5 follow-up tasks.
- Don't try to run the system end-to-end on the local M5 Max — `whisper-cli` and `piper` aren't installed locally; tests skip; the canonical test environment is the OCI VPS.
- Don't push to remote without explicit ask — except after an approved subagent-driven task, where the established cadence is "approve → push".

## Auto-memory

The user has substantial cross-project auto-memory at `~/.claude/projects/-Users-techfreakworm-Projects/memory/`. Notable entries that affect this project:

- **Subagents Opus Max** — every Agent dispatch uses `model="opus"`. Codified above.
- **Git authorship** — sole author, no Co-Authored-By. Codified above.
- **No conda** — venvs use `python3.x -m venv`, not conda. Codified in spec.
- **Tests folder** — `~/Projects/tests` for ad-hoc venvs; in-project `.venv` is fine for pyproject packages.
- **Background pipelines** — long-running work goes via background bash + Monitor.
- **Verify before fix** — reproduce bugs first with screenshots / logs before patching.
- **Deep thinking for bugs** — on the 2nd failed fix attempt, stop patching and invoke `sequential-thinking` MCP + brainstorming skill.

## Quick test commands

```bash
# Activate venv
source .venv/bin/activate

# Run all tests (29 cases, voice tests skip without whisper/piper)
pytest -v

# Single MCP server tests
pytest tests/mcp_servers/test_voice_stt.py -v
pytest tests/mcp_servers/test_voice_tts.py -v
pytest tests/mcp_servers/test_project_orchestrator.py -v
pytest tests/mcp_servers/test_project_orchestrator_registry.py -v
pytest tests/mcp_servers/test_orchestrator_spawner.py -v
pytest tests/mcp_servers/test_orchestrator_templates.py -v

# Smoke-launch an MCP server (stdio)
python -m claude_soma.mcp_servers.voice_stt.server         # Ctrl+D to exit
python -m claude_soma.mcp_servers.voice_tts.server
python -m claude_soma.mcp_servers.project_orchestrator.server

# Validate JSON / TOML
python3.12 -c "import json; json.load(open('.mcp.json')); print('OK')"
python3.12 -c "import tomllib; tomllib.load(open('pyproject.toml','rb')); print('OK')"

# Validate shell scripts
bash -n scripts/*.sh

# Validate YAML frontmatter on every skill
python3.12 -c "import yaml,glob; [yaml.safe_load(open(p).read().split('---')[1]) for p in glob.glob('skills/*/SKILL.md')]; print('OK')"

# Deploy to OCI VPS (when ready)
./scripts/deploy.sh
```

## Commit-then-push cadence

After every approved subagent-driven task:

```bash
git push origin main
```

That keeps the public repo in sync with each green task. Tag week-N-code-complete at the end of each week.
