# What's Next — Resuming Claude Soma

State as of last session: **Week 1 + Week 2 code complete**, pushed to `origin/main` with tags `week-1-code-complete` and `week-2-code-complete`. Two work tracks are pending; they can run in parallel.

## Track A — Continue subagent-driven code implementation

Pick up the plan at Task 29 (Week 3 dashboard) or Task 42 (Week 4 polish).

### Option 1 — Fresh Claude Code session at this repo

```bash
cd ~/Projects/llm/hermes-claude
claude
```

When the session opens, paste this as your first message:

> Resume Claude Soma V1 implementation from Week 3. The spec is at `docs/superpowers/specs/2026-05-22-hermes-claude-design.md`, the plan is at `docs/superpowers/plans/2026-05-22-hermes-claude-v1.md`. CLAUDE.md has all the repo conventions. Week 1 + Week 2 code are complete (tags `week-1-code-complete`, `week-2-code-complete`). Start at Task 29 and dispatch subagents per `superpowers:subagent-driven-development`. All subagents on Opus. Push to `origin/main` after every approved task.

(For Week 4, substitute "Task 42" for "Task 29".)

### Option 2 — Resume the previous session

If the previous session is still around:

```bash
claude --resume
```

…and pick the Claude Soma session. It carries the full context. Tell it to continue with Week 3 (or wherever).

### What Week 3 builds

The dashboard at `claude.mayankgupta.in`. ~13 tasks:

| Task | What |
|---|---|
| 29 | `hermes_api` MCP server (unix-socket bridge + 5 MCP tools exposing Claude state) |
| 30 | FastAPI scaffold — `/api/healthz` + `/api/public/stats` (anonymized live stats) |
| 31 | API routes — `/api/projects`, `/api/conversations`, `/api/routines` |
| 32 | API routes — `/api/usage`, `/api/memory`, `/api/logs`, `/api/admin` |
| 33 | `/api/events` SSE stream tailing `~/.claude-soma/activity.jsonl` |
| 34 | `claude-soma-api.service` systemd unit |
| 35 | Next.js 16 scaffold + Tailwind + shadcn + Auth.js v5 GitHub OAuth |
| 36 | Public landing page (hero, live stats, architecture, thesis, demo placeholder) |
| 37 | `/admin` overview (KPI cards + SSE activity feed) |
| 38 | `/admin/projects` react-flow project tree visualization |
| 39 | `/admin/{conversations,routines,usage}` pages |
| 40 | `/admin/{memory,logs}` pages |
| 41 | `claude-soma-frontend.service` systemd unit + `Caddyfile` + GitHub OAuth app + DNS A record |

### What Week 4 builds

Polish, setup wizard, scheduled jobs, README+LICENSE+marketplace publish. ~6 tasks:

| Task | What |
|---|---|
| 42 | `agents/content-drafter.md` + `agents/tool-builder.md` subagent templates |
| 43 | `scripts/reaper.py` (24h idle hibernate / 7d hard-delete) + tests |
| 44 | `scripts/healthcheck.sh` + `scripts/cache_refresh.py` + `scripts/usage_snapshot.py` |
| 45 | 4 systemd timer pairs (healthcheck/cache/usage/reaper) |
| 46 | Create 3 RemoteTrigger routines from Telegram (user-action, no subagent) |
| 47 | `src/claude_soma/wizard/init.py` interactive setup wizard with tests |
| 48 | `LICENSE` + final README polish + `.claude-plugin/marketplace.json` |

---

## Track B — Bring the deployed code to life (user-action)

These are the manual steps no subagent can execute. They can happen any time after Week 1 code is shipped — start whenever you're ready to see the Telegram bot respond.

### B1 — OCI VPS provisioning

```text
Oracle Cloud Console → Compute → Create instance:
  • Shape: VM.Standard.A1.Flex (Ampere ARM)
  • OCPU: 4 · Memory: 24 GB · Boot volume: 50 GB
  • Image: Canonical Ubuntu 24.04
  • Public IPv4: assigned
  • SSH key: ~/.ssh/id_ed25519.pub

VCN default security list — add ingress:
  • TCP 22  from 0.0.0.0/0  (SSH)
  • TCP 80  from 0.0.0.0/0  (Let's Encrypt HTTP-01)
  • TCP 443 from 0.0.0.0/0  (HTTPS)
```

Add a local SSH alias in `~/.ssh/config`:

```
Host oci-hermes
    HostName <public-ipv4>
    User ubuntu
    IdentityFile ~/.ssh/id_ed25519
    ServerAliveInterval 60
```

Verify: `ssh oci-hermes 'uname -a && free -h && nproc'`.

### B2 — OS bootstrap on the VPS

```bash
ssh oci-hermes
# On VPS:
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80  -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save

sudo apt-get update
sudo apt-get install -y build-essential git curl wget pkg-config \
    python3.12 python3.12-venv python3.12-dev python3-pip \
    ffmpeg cmake clang tmux jq sqlite3 libsox-dev libssl-dev \
    debian-keyring debian-archive-keyring apt-transport-https

# Node 20 + pnpm
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
sudo npm install -g pnpm

# Caddy
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | \
    sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | \
    sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy

# Claude Code globally
sudo npm install -g @anthropic-ai/claude-code
```

### B3 — Mint and install the Max OAuth token

```bash
# Locally on M5 Max (browser-capable machine):
claude setup-token
# OAuth flow → prints oat-... (valid 1 year)

# On VPS:
sudo install -d -m 700 -o ubuntu -g ubuntu /etc/claude-soma
echo 'CLAUDE_CODE_OAUTH_TOKEN=oat-paste-the-token-here' | sudo tee /etc/claude-soma/secrets.env
sudo chmod 600 /etc/claude-soma/secrets.env
sudo chown ubuntu:ubuntu /etc/claude-soma/secrets.env

# Verify:
export $(cat /etc/claude-soma/secrets.env)
claude -p 'reply with exactly the word OK' --output-format text
# expected output: OK
```

### B4 — Install whisper.cpp + piper on the VPS

```bash
ssh oci-hermes

# whisper.cpp ARM build
sudo install -d -o ubuntu -g ubuntu /opt
cd /opt
git clone https://github.com/ggml-org/whisper.cpp.git
cd whisper.cpp && make -j4
bash ./models/download-ggml-model.sh large-v3-turbo
# Smoke: ./build/bin/whisper-cli -m models/ggml-large-v3-turbo.bin -f samples/jfk.wav -otxt -of /tmp/jfk -nt

# piper ARM binary + ryan voice
sudo install -d -o ubuntu -g ubuntu /opt/piper && cd /opt/piper
wget https://github.com/rhasspy/piper/releases/latest/download/piper_linux_aarch64.tar.gz
tar xzf piper_linux_aarch64.tar.gz --strip-components=1 && rm piper_linux_aarch64.tar.gz
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx.json
# Smoke: echo "Hello from piper" | ./piper --model en_US-ryan-medium.onnx --output_file /tmp/test.wav
```

### B5 — Deploy the plugin to the VPS

From the local repo:

```bash
cd ~/Projects/llm/hermes-claude
./scripts/deploy.sh   # rsync, venv + pip install, build frontend (+ copy static), restart frontend
```

Verify: `ssh oci-hermes /opt/claude-soma/.venv/bin/python -c "from claude_soma.mcp_servers.voice_stt.server import transcribe_impl; print('OK')"`.

### B6 — Telegram bot

```text
1. In Telegram, message @BotFather:
     /newbot
     Name: Claude Soma (Mayank)
     Username: choose any available, e.g. claude_soma_mayank_bot
   Save the bot token (format: 123456:ABC...).

2. Install the channel plugin on the VPS:
     ssh oci-hermes
     claude --plugin install telegram@claude-plugins-official
     claude   # opens interactive session
     /telegram:configure <bot-token>
     /quit

3. DM your new bot from Telegram. It replies with a pairing code (e.g. ABCDE).

4. Pair:
     ssh oci-hermes claude
     /telegram:access pair ABCDE
     /telegram:access policy allowlist
     /quit
```

### B7 — Install channel systemd unit and start

```bash
# From local repo:
scp systemd/claude-soma-channel.service oci-hermes:/tmp/

ssh oci-hermes
sudo install -m 644 /tmp/claude-soma-channel.service /etc/systemd/system/
sudo install -d -o ubuntu -g ubuntu /home/ubuntu/hermes-work /var/log/claude-soma
sudo systemctl daemon-reload
sudo systemctl enable --now claude-soma-channel.service
sleep 10
sudo systemctl status claude-soma-channel.service --no-pager | head -20
# expect: active (running)

# Watch the log:
sudo tail -f /var/log/claude-soma/channel.log
```

### B8 — Smoke test from your phone

1. Open Telegram, message your bot: `What am I working on?`
   - Expected: bot replies (text or voice) with portfolio summary.
2. Send: `Build me a tiny demo app called demo-smoke that prints hello`.
   - Expected: bot replies with a Remote Control URL for `demo-smoke`. Open the URL in your phone's Claude app to attach.
3. Send: `Shut down demo-smoke`.
   - Expected: bot confirms hibernation. Subsequent `What's running?` shows nothing.

If anything fails, attach the channel via SSH for debugging:

```bash
ssh oci-hermes tmux a -t hermes   # detach with Ctrl+B, then D
```

### B9 — Dashboard (after Week 3 code lands)

When Week 3 code is committed:

1. GitHub OAuth app: <https://github.com/settings/developers> → New OAuth App
   - Homepage: `https://claude.mayankgupta.in`
   - Callback: `https://claude.mayankgupta.in/api/auth/callback/github`
   - Copy Client ID + Secret.

2. DNS A record: in your domain DNS provider, add
   - Name: `claude` · Type: A · Value: `<OCI public IP>` · TTL: 300

3. Append secrets:
   ```bash
   ssh oci-hermes sudo bash -c '
     echo "AUTH_GITHUB_ID=<id>" >> /etc/claude-soma/secrets.env
     echo "AUTH_GITHUB_SECRET=<secret>" >> /etc/claude-soma/secrets.env
     echo "AUTH_SECRET=$(openssl rand -hex 32)" >> /etc/claude-soma/secrets.env
     echo "AUTH_URL=https://claude.mayankgupta.in" >> /etc/claude-soma/secrets.env
     echo "AUTH_TRUST_HOST=true" >> /etc/claude-soma/secrets.env
   '
   ```

4. Install the api + frontend systemd units, Caddyfile, build frontend:
   ```bash
   scp systemd/claude-soma-api.service systemd/claude-soma-frontend.service Caddyfile oci-hermes:/tmp/
   ssh oci-hermes bash <<'EOF'
     sudo install -m 644 /tmp/claude-soma-api.service /etc/systemd/system/
     sudo install -m 644 /tmp/claude-soma-frontend.service /etc/systemd/system/
     sudo install -m 644 /tmp/Caddyfile /etc/caddy/Caddyfile
     sudo systemctl daemon-reload

     # build_frontend.sh runs pnpm install + build AND copies .next/static +
     # public next to the standalone server.js. A bare `pnpm build` skips that
     # copy, so the dashboard serves unstyled (every /_next/static/* 404s).
     bash /opt/claude-soma/scripts/build_frontend.sh

     sudo systemctl enable --now claude-soma-api.service claude-soma-frontend.service
     sudo systemctl reload caddy
   EOF

   sleep 10
   curl -s https://claude.mayankgupta.in/api/healthz | jq .
   # expect: {"status":"ok",...}
   ```

5. Open https://claude.mayankgupta.in/ — landing renders with live stats.
   Open https://claude.mayankgupta.in/admin — GitHub OAuth → admin loads.

### B10 — Scheduled routines (after Week 4 code lands)

From Telegram, send the bot:

> "Schedule a weekday morning brief at 8am IST. Have it run the portfolio-status skill and send the result to me here on Telegram. Name it morning-brief."

> "Every Sunday at 11am IST, run a memory-consolidation routine: review my MEMORY.md across projects, prune stale entries, and report what changed. Name it memory-consolidation."

> "Every month on the 1st at 10am IST, check how many days until my OAuth token expires. If less than 60 days, tell me to refresh. Name it oauth-expiry-check."

Each invocation produces a routine on Anthropic's infrastructure. Verify in the dashboard at `/admin/routines` once Week 3 is live.

### B11 — Install scheduled timers on VPS (after Week 4)

```bash
scp systemd/hermes-{healthcheck,cache-refresh,usage-snapshot,idle-reaper}.{service,timer} oci-hermes:/tmp/
ssh oci-hermes bash <<'EOF'
  sudo install -m 644 /tmp/hermes-*.service /etc/systemd/system/
  sudo install -m 644 /tmp/hermes-*.timer   /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable --now hermes-healthcheck.timer hermes-cache-refresh.timer \
                              hermes-usage-snapshot.timer hermes-idle-reaper.timer
  sudo systemctl list-timers --no-pager | grep hermes
EOF
```

---

## V1.5 ship checklist (after Tracks A + B finish)

- [ ] `LICENSE` file (MIT) committed.
- [ ] README polished with screenshot of the live dashboard.
- [ ] 60-second Loom demo: voice memo → spawn-project → dashboard reflects state.
- [ ] Blog post on mayankgupta.in: "Why I built Claude Soma instead of forking Hermes-Agent".
- [ ] `.claude-plugin/marketplace.json` committed; test `/plugin marketplace add techfreakworm/claude-soma` works.
- [ ] Pinned X thread linking to repo + blog + demo.
- [ ] Tag `v0.1.0`, GitHub release with the demo video attached.

---

## Quick references

- Repo: <https://github.com/techfreakworm/claude-soma>
- Dashboard (when shipped): <https://claude.mayankgupta.in>
- Design spec: `docs/superpowers/specs/2026-05-22-hermes-claude-design.md`
- Implementation plan: `docs/superpowers/plans/2026-05-22-hermes-claude-v1.md`
- Repo conventions: `CLAUDE.md`
- Hermes-Agent upstream (the inspiration): <https://github.com/NousResearch/hermes-agent>
