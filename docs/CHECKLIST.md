# Claude Soma — V1 Operations Checklist

State after the 2026-05-24 deployment to `soma.mayankgupta.in` (Oracle Cloud Ubuntu
24.04 ARM, 2 vCPU / 11 GB RAM / 96 GB disk, +8 GB swap). Domain:
**<https://soma.mayankgupta.in>** (Caddy + Let's Encrypt). Tag at code-merge
time: `week-4-code-complete`; plus V1.5 spawner + routines registry landed
post-tag on `main`.

## Setup

### Done

- [x] OS bootstrap (apt deps, Node 22, pnpm, Caddy, Claude Code CLI, Bun)
- [x] 8 GB swap added at `/swapfile` (sysctl `vm.swappiness=10`)
- [x] iptables ingress for tcp/80 + tcp/443, persisted
- [x] whisper.cpp ARM build + `ggml-large-v3-turbo.bin` model
- [x] piper aarch64 + `en_US-ryan-medium` voice
- [x] `git clone` to `/opt/claude-soma`, venv, `pip install -e .[dev]` (43 tests pass on VPS)
- [x] Next.js standalone build (with `.next/static` + `public` copied next to `server.js` — see `scripts/build_frontend.sh`)
- [x] systemd: `claude-soma-api.service` + `claude-soma-frontend.service` enabled + running
- [x] systemd timers: healthcheck (10m), cache-refresh (5m), idle-reaper (6h), usage-snapshot (daily 23:55 UTC) — all active
- [x] Max OAuth token at `/etc/claude-soma/secrets.env` (mode 600, owner ubuntu)
- [x] `claude auth login` (saves persistent credentials for interactive mode)
- [x] Telegram bot created via @BotFather, plugin installed, token wired
- [x] Telegram `dmPolicy: allowlist`, owner ID `935376085` in `allowFrom`
- [x] `claude-soma-channel.service` running with `--dangerously-skip-permissions --effort max`
      - native binary `~/.local/bin/claude` (NOT the npm wrap)
      - `Type=oneshot` + `RemainAfterExit=yes` (tmux daemonizes past systemd's
        tracking; healthcheck.sh acts as watchdog)
      - tmux session `hermes`, log at `/var/log/claude-soma/channel.log` via
        `tmux pipe-pane` (NOT `| tee` — that breaks claude's TTY detection)
- [x] Codex CLI installed + `codex login` complete (ChatGPT auth, `~/.codex/auth.json`)
- [x] plugin.json + marketplace.json: `author` as object (was string, failed Zod validation)
- [x] **Item B — GitHub OAuth + DNS + Caddy** done. DNS A record `soma` → `soma.mayankgupta.in` at GoDaddy. OAuth app `<oauth-client-id>` callback `https://soma.mayankgupta.in/api/auth/callback/github`. Caddyfile installed; Let's Encrypt cert auto-acquired. Real `AUTH_SECRET` (44-byte base64) replaces the placeholder. Both `https://soma.mayankgupta.in/` (landing) and `/admin` (Next-auth) work end-to-end.
- [x] **Item C — usage-snapshot timer enabled** (fires daily 23:55 UTC).
- [x] **Item F — secrets.env backed up** to `~/Backups/claude-soma/secrets-<timestamp>.env` on M5 Max (encrypt before storing off-site).
- [x] **GitHub deploy key for claude-soma** (push-only, scoped to one repo) — bot can self-update its own repo via SSH.
- [x] **GitHub fine-grained PAT + `gh` CLI** — bot can `gh repo create` new repos and push to them (used to autonomously create projects). Token also exported as `GITHUB_TOKEN` in secrets.env.
- [x] **Caddy routing fixes:** `handle /api/auth/*` → Next-auth (port 3000), `handle /api/*` → FastAPI (port 9000), everything else → Next.js. Was initially `handle_path` (stripped prefix and 404'd FastAPI) and `/api/auth/*` was caught by the FastAPI rule (404'd Next-auth).
- [x] **`next.config.mjs` rewrite removed** — was forwarding `/api/*` to FastAPI inside Next.js, shadowing Next-auth's own `/api/auth/*` routes.
- [x] **iptables fix:** Phase 1 inserted ACCEPT 80/443 at position 6, AFTER Oracle's default REJECT at position 5 — so the public couldn't reach Caddy. Re-inserted at position 5 (now: SSH → 80 → 443 → REJECT). Bake into next bootstrap script (see V1.5 followups).
- [x] **`portfolio-oneliner` weekday brief** timer added by the bot itself (Mon–Fri 03:30 UTC). 5 systemd timers total.

### Pending

- [x] **Item F — Backup `secrets.env`** (automated)
  - `scripts/backup-secrets.sh` + `systemd/claude-soma-secrets-backup.{service,timer}` GPG-encrypt and store backups in `/home/ubuntu/secrets-backups/` (chmod 700), daily at 03:30 UTC, keeping last 14.
  - **Operator action required**: populate the passphrase file before first run:
    ```bash
    sudo bash -c 'echo "your-strong-passphrase" > /etc/claude-soma/backup.pass && chmod 0600 /etc/claude-soma/backup.pass'
    ```
  - To rotate the Telegram token: DM @BotFather → `/revoke` → pick your bot → update both `/etc/claude-soma/secrets.env` (`TELEGRAM_BOT_TOKEN=<new>`) and `/home/ubuntu/.claude/channels/telegram/.env` → `sudo systemctl restart claude-soma-channel.service`.

- [ ] **Item G — Revoke the Telegram bot token I leaked in chat (paranoia)**
  - Bot token shared in this transcript: `8706823712:AAG7…`
  - DM @BotFather → `/revoke` → pick `claude_soma_bot` → save new token → update both secrets locations (see Item F rotation note above) → restart channel.

- [x] **Item H — Logrotate for `/var/log/claude-soma/*.log`**
  - `scripts/logrotate-claude-soma` installed by bootstrap at `/etc/logrotate.d/claude-soma` (daily, 14 rotations, 50 MB cap, `copytruncate`).

- [x] **Item I — NEEDS_REAUTH surfacing**
  - `scripts/healthcheck.sh` (section 5) scans `~/.claude-pw/NEEDS_REAUTH-*` and DMs the user via Telegram Bot API once per platform per day. Dedupe state: `/home/ubuntu/.claude-soma/needs_reauth_pinged.txt`.

## Verification tests

### Done

- [x] **T0 — Voice round-trip**: voice memo → whisper STT → claude → piper TTS → opus reply in Telegram (confirmed end-to-end ~4 s)

### Hooks status

- `UserPromptSubmit` (`scripts/voice_intake.sh`) — currently a silent no-op (`exit 0`). Original schema (`{decision: "continue", user_prompt: ..., meta_inject: ...}`) was rejected as invalid by Claude Code 2.1.150's UserPromptSubmit validator, and the `if: event.meta.audio_path != null` condition in `hooks/hooks.json` is silently ignored by current Claude Code, so it was firing for every text DM and raising visible errors in the bot UI. Voice routing works fine without it (telegram plugin downloads the .oga, claude calls `mcp__voice-stt__transcribe` directly). V1.5: rewrite against the new schema if a real need emerges.
- `PostToolUse` (`scripts/log_activity.sh`) — works correctly, populates `~/.claude-soma/activity.jsonl`.
- `SessionStart` (`scripts/session_start_context.sh`) — works, injects active projects + recent commits into the session's first context.

### Pending

- [~] **T1 — Project spawn** — **soft pass via fallback; orchestrator path BLOCKED**
  - DM: *"Build me a tiny demo app called demo-smoke that just prints hello."*
  - Expected: bot replies with a Remote Control URL for `demo-smoke`
  - **Actual on 2026-05-23**: spawner fails, bot self-recovers and delivers the functional outcome (built `/tmp/demo-smoke/index.html`, served via `python3 -m http.server 8000`, exposed via ngrok). User-facing flow works, but no project-lead was registered. Root cause: `src/claude_soma/mcp_servers/project_orchestrator/spawner.py` invokes `claude --bg --output-format json <brief>`, but Claude Code 2.1.150 removed the `--bg` flag entirely. Replacement (`claude agents`) is interactive-only — no non-interactive `create` equivalent yet.
  - **V1.5 fix path**: either (a) rewrite spawner to launch a tmux-wrapped `claude` per project (same pattern as `claude-soma-channel.service`) and scrape the session ID from claude's startup output, or (b) wait for upstream `claude agents create` non-interactive subcommand.

- [ ] **T2 — Portfolio status skill**
  - DM (text): *"What am I working on?"*
  - Expect: list of active project-leads (will be empty before T1, lists demo-smoke after)

- [ ] **T3 — Project kill**
  - After T1: DM *"Shut down demo-smoke."*
  - Expect: bot confirms hibernation
  - Verify: `sqlite3 /opt/claude-soma/registry.sqlite "SELECT name,status FROM projects WHERE name='demo-smoke'"` returns `killed`

- [ ] **T4 — Message a running project**
  - After T1: DM *"Tell demo-smoke to add a README explaining what it prints."*
  - Expect: bot routes the message into the demo-smoke project-lead's session via `SendMessage`

- [ ] **T5 — Schedule a routine**
  - DM: *"Every weekday at 9am IST, send me a one-line summary of what's running. Name it morning-brief."*
  - Expect: bot uses `RemoteTrigger.create()`; confirms creation
  - Verify in dashboard `/admin/routines` (after Item B) or via `claude` CLI: `/routines` lists `morning-brief`

- [ ] **T6 — SSE live activity feed**
  - Requires Item B (admin auth)
  - Open `/admin` → bottom-right shows "Live activity"
  - DM the bot → events appear in the feed within ~2 s

- [ ] **T7 — Healthcheck self-healing**
  - `ssh soma-vps "sudo systemctl stop claude-soma-frontend.service"`
  - Wait 10 min (or `sudo systemctl start claude-soma-healthcheck.service` for an immediate poke)
  - Verify: `tail /var/log/claude-soma/healthcheck.log` shows "frontend: UNHEALTHY, restarting"
  - Verify: `sudo systemctl is-active claude-soma-frontend.service` → `active`

- [ ] **T8 — VPS reboot survival**
  - `ssh soma-vps "sudo reboot"` (wait ~2 min)
  - Verify on return: `sudo systemctl is-active claude-soma-{api,frontend,channel}.service` → all `active`
  - DM the bot → expect response (proves channel survived reboot)
  - Verify: `sudo systemctl list-timers --no-pager | grep claude-soma` shows 4 timers armed

- [ ] **T9 — Codex image-gen**
  - DM: *"Draw me a diagram of the Claude Soma system architecture."*
  - Expect: bot delegates to `codex-image-gen` skill → Codex CLI synthesizes via your ChatGPT subscription → image returned as a Telegram photo

- [ ] **T10 — Memory consolidation**
  - DM: *"Remember that I prefer JSON over YAML for config files."*
  - Expect: auto-memory captures
  - Verify: `cat ~/.claude/projects/<encoded-slug>/memory/MEMORY.md` shows the new entry

- [ ] **T11 — Usage-snapshot timer first fire (after 23:55 UTC)**
  - `sqlite3 /opt/claude-soma/usage.sqlite 'SELECT * FROM daily_snapshots ORDER BY date DESC LIMIT 1'`
  - Expect: one row with today's date and non-zero credit numbers

## V1.5 backlog

- [x] **`spawner.py` rewrite** — done (commit `d31df9a`). tmux-wrapped per-project sessions using native `claude` at `~/.local/bin/claude`. 10 new tests. `agent_id` is now `soma-proj-<name>` (tmux session name).

- [x] **Unified routines registry** — done (commit `87cb741`). `routines` table + 5 Registry methods + `/api/routines` endpoint merging registry/systemd/RemoteTrigger sources. 10 new tests.

- [x] **#36 Wire `kill_session()` into `kill_project_impl`** — done in commit `238f78f`; `kill_project_impl` now calls `kill_session(agent_id)` before flipping registry status.

- [ ] **#37 Bot-side `register_routine()` calls** — the routines table exists but no creator populates it. Wire:
  1. `skills/schedule-routine/` skill — after creating a RemoteTrigger, call `register_routine(name, kind="cloud", schedule=..., target_skill=..., metadata={"trigger_id": "..."}, created_by="user")`
  2. portfolio-oneliner provisioning (and any future bot-created local timer) — `register_routine(name, kind="local", schedule=..., target_skill=..., created_by="bot")`
  3. `soma-init` wizard (when installing default timers) — `register_routine(..., created_by="system")` for each of healthcheck, cache-refresh, usage-snapshot, idle-reaper
  - Until this lands, `/api/routines` falls back to synthesized entries from systemd/RemoteTrigger (correct shape, but `created_by` always says `"system"`/`"cloud"` — never canonical `"bot"`/`"user"`).

- [x] **#38 Fix Phase 1 bootstrap iptables order** — `vps_bootstrap.sh` step 2/9 already inserts ACCEPTs before Oracle's REJECT (dynamic position detection). Now gated on `--cloud=oci` / `SOMA_CLOUD=oci` to avoid unintended iptables mutations on non-OCI hosts. Documented in NEXT.md B2.

- [ ] **Routines registry: store `systemd unit name` in `metadata.unit`** so the merger doesn't rely on heuristic name aliasing (`<name>` ↔ `claude-soma-<name>.timer`). Will come naturally when #37 lands.

- [ ] **Pre-existing `F401` in `project_orchestrator/server.py`** — `InvalidProjectName`, `BriefTooLong` imports unused. One-line cleanup. Flagged by both subagents, left alone per "don't reformat unrelated code".

## Operational quick reference

```bash
# SSH alias is set on the M5 Max
ssh soma-vps                          # interactive shell on VPS

# Watch the live channel session
ssh soma-vps -t tmux a -t hermes      # detach: Ctrl+B, D

# Tunnel the dashboard locally (run on M5 Max)
ssh -fN -L 3000:127.0.0.1:3000 -L 9000:127.0.0.1:9000 soma-vps
pkill -f "ssh -fN -L 3000"            # stop tunnel

# Service control on VPS
sudo systemctl status  claude-soma-{api,frontend,channel}.service
sudo systemctl restart claude-soma-channel.service
sudo systemctl list-timers --no-pager | grep claude-soma

# Logs
sudo tail -f /var/log/claude-soma/channel.log       # claude --channels UI
sudo tail -f /var/log/claude-soma/api.log           # FastAPI
sudo tail -f /var/log/claude-soma/healthcheck.log   # watchdog decisions
tail -f ~/.claude-soma/activity.jsonl               # all tool calls
```
