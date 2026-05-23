# Claude Soma — V1 Operations Checklist

State after the 2026-05-23 deployment to `soma.mayankgupta.in` (Oracle Cloud Ubuntu
24.04 ARM, 2 vCPU / 11 GB RAM / 96 GB disk, +8 GB swap). Tag at deploy time:
`week-4-code-complete`.

## Setup

### Done

- [x] OS bootstrap (apt deps, Node 20, pnpm, Caddy, Claude Code CLI, Bun)
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

### Pending

- [ ] **Item B — GitHub OAuth app + DNS + Caddy**
  - Pick a domain (e.g. `claude.mayankgupta.in`), add A record → `soma.mayankgupta.in`
  - Register OAuth app at <https://github.com/settings/developers>
    - Homepage: `https://<domain>/`
    - Callback: `https://<domain>/api/auth/callback/github`
  - Append `AUTH_GITHUB_ID` + `AUTH_GITHUB_SECRET` to `/etc/claude-soma/secrets.env`
  - Generate real `AUTH_SECRET=$(openssl rand -base64 32)`, replace placeholder
  - `sudo install -m 644 Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy`
  - Verify: `https://<domain>/` (public) + `https://<domain>/admin` (OAuth gate)

- [ ] **Item D — Replace `AUTH_SECRET` placeholder**
  - Currently `placeholder-soma-replace-on-deploy-XXXXXXXXXXXXXXXXXXXXXXXX`
  - `AUTH_SECRET=$(openssl rand -base64 32)`
  - Restart frontend: `sudo systemctl restart claude-soma-frontend.service`
  - Only matters once Item B is done (no one can log in until then)

- [ ] **Item F — Backup `secrets.env`**
  - Single point of failure. CLAUDE_CODE_OAUTH_TOKEN + Telegram bot token + AUTH_SECRET all live there.
  - Suggested: `scp soma-vps:/etc/claude-soma/secrets.env ~/Backups/soma-secrets-$(date +%Y%m%d).env.enc` after `gpg --symmetric` encryption

- [ ] **Item G — Revoke the Telegram bot token I leaked in chat (paranoia)**
  - Bot token shared in this transcript: `8706823712:AAG7…`
  - DM @BotFather → `/revoke` → pick `claude_soma_bot` → save new token → `echo 'TELEGRAM_BOT_TOKEN=<new>' > ~/.claude/channels/telegram/.env && sudo systemctl restart claude-soma-channel.service`

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
