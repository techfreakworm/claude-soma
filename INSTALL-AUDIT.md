# INSTALL-AUDIT.md

**Status:** FAIL — a fresh-VPS install today following README + NEXT.md would not produce a working system. At least 3 hard blockers + 10+ silent feature-degradations identified.

**Generated:** 2026-06-03 by soma-improver. Methodology: 4 parallel sonnet+max+sequential-thinking auditors covered docs accuracy (S1) / install tooling (S2) / dependencies+secrets (S3) / drift between repo and live VPS (S4). Per-subagent reports archived at `/tmp/PIA-S{1,2,3,4}*.md` on the lead host.

**Scope:** end-to-end audit of whether the README, install scripts, wizard, and supporting documentation accurately and completely describe how to install claude-soma on a fresh Ubuntu VPS. The user wants ZERO surprises.

**Verdict per area:**

| Area | Status | Severity |
|---|---|---|
| README.md | STALE | Quickstart step 4 is wrong; legacy paths/domains; 13+ undocumented features |
| NEXT.md | STALE | B11 unit names use legacy `hermes-*` prefix; B5 references the author's laptop path; B9 wrong domain |
| CLAUDE.md | STALE | Claims Weeks 3–4 not started; both shipped + live |
| Install scripts (`vps_bootstrap.sh`, `deploy.sh`, `wizard/init.py`, `install.py`) | PARTIAL — covers ~19/26 live units | 7 units have ZERO install-path coverage |
| Dependencies (apt/npm/python) | PARTIAL | `markserv` install missing from bootstrap; 3 CLIs (grok/codex/hf) have zero install docs |
| Secrets (`/etc/claude-soma/secrets.env`) | NO TEMPLATE | No `secrets.env.example` anywhere; 2 silently-required keys not prompted |
| Caddy templating | DRIFTED — SHOWSTOPPER | Repo Caddyfile lacks the `import /etc/caddy/conf.d/*.caddyfile` line; render script would corrupt downloads |
| Recent infra additions | MULTIPLE GAPS | 8 drift items including 3 showstoppers |

---

## Part 1 — Critical blockers (fresh install hard-fails)

Each of the following will hard-fail or silently break a major subsystem on a fresh VPS following the current docs.

### B1 — README Quickstart step 4 (`./scripts/deploy.sh`) is wrong for a fresh-VPS install

`scripts/deploy.sh` is a **developer-machine → remote rsync script** that assumes you're on a laptop pushing to a remote VPS named `soma-vps`. Run on the VPS itself, it either fails (unknown host `soma-vps`) or rsyncs the directory over itself — destructive. The README does not flag this.

**Correct path for a fresh VPS:** clone the repo into `/opt/claude-soma`, run `bash scripts/build_frontend.sh`, then run the wizard. `deploy.sh` is for hot-reload from a dev machine only.

### B2 — NEXT.md B11 unit names use the legacy `hermes-*` prefix

The block reads:
```
sudo systemctl daemon-reload && sudo systemctl enable --now \
    hermes-healthcheck hermes-cache-refresh hermes-secrets-backup \
    hermes-pw-refresh hermes-usage-snapshot
```
These units **do not exist**. All units are `claude-soma-*`. Running this block as documented yields `Failed to enable unit: Unit hermes-healthcheck.service does not exist.` for each. The user cannot bring the system up by following NEXT.md verbatim.

### B3 — Bootstrap step 15 silently kills `files.mayankgupta.in`

`vps_bootstrap.sh` step 15 (`sudo install -m 644 /opt/claude-soma/Caddyfile /etc/caddy/Caddyfile`) overwrites the live Caddyfile, which has been hand-edited to include `import /etc/caddy/conf.d/*.caddyfile` at the bottom. The **repo Caddyfile does not have this line**. After bootstrap, the file relay (FI-DOMAIN, `files.mayankgupta.in`) goes dark.

**Diff repo vs live `/etc/caddy/Caddyfile`:**
```
> (blank line)
> import /etc/caddy/conf.d/*.caddyfile
```

### B4 — `caddy/files.caddyfile.in` template cannot reproduce the live config

The repo template was last fully synced before commits eedd213 / e776d45 / 8d20fbd / 9ce51da; the live `/etc/caddy/conf.d/files.caddyfile` has been hand-edited to include:
- `@binary` matcher for office/zip/pdf → `file_server` (raw binary serving)
- `handle { reverse_proxy 127.0.0.1:18081 }` (markserv) for everything else

`/opt/claude-soma/caddy/files.caddyfile.in` is also **modified vs origin** (uncommitted). The repo template, the /opt template, and the live config are now three different things, with a `DRIFT WARNING` comment left in /opt as a marker.

Running `scripts/caddy-files-render.sh` on a fresh install will produce a `file_server`-only config (no markserv fallback) — losing Markdown preview at `files.mayankgupta.in` for any `.md` file, and rendering the entire files-relay UX broken.

### B5 — `markserv` is not installed by any script

The unit `claude-soma-markserv.service` is enabled by bootstrap step 14f, but the bootstrap **never runs `sudo npm install -g markserv`**. On the live VPS, `markserv@1.17.4` is present at `/usr/bin/markserv` (installed by an out-of-band step). A fresh install would fail unit start with `markserv: command not found`.

### B6 — `vps_bootstrap.sh` needs `--cloud=oci` on Oracle Cloud (the documented free-tier target)

Without `--cloud=oci`, the iptables fix that makes Caddy publicly reachable on OCI is silently skipped. The dashboard at `soma.mayankgupta.in` will be inaccessible from the public internet despite Caddy running locally. README + NEXT.md do not flag the flag requirement.

### B7 — `systemd/claude-soma-daily-status.service` is not in the repo

Only `claude-soma-daily-status.timer` is committed. The matching service file exists ONLY in `/opt/claude-soma/systemd/` as an untracked file. The bootstrap rsyncs `systemd/` from the repo, so the timer is installed but its service is missing → systemd reports `unit not found` every day at 10:00 IST. The daily-status DM never sends.

### B8 — `/opt/claude-soma/google/soma-service-account.json` is untracked credentials of unknown purpose

Google API service-account credential file at `/opt/claude-soma/google/soma-service-account.json` is untracked. Repo-wide grep finds zero Python references. It may be consumed by the dynamically-loaded Google Calendar / Google Drive MCPs (which appear in the system MCP set). If any feature depends on it, a fresh install is missing the credential entirely. **Investigate before any reinstall.**

---

## Part 2 — Silent feature-degradations (fresh install boots, but features quietly broken)

| # | What's broken | Why |
|---|---|---|
| 1 | `listener-healthcheck` timer not installed | Units in repo + live, but NOT in `vps_bootstrap.sh`, `install.py`, or `wizard/init.py`. Port 9100 outages go undetected. |
| 2 | `engagement-drip` timer not installed + `/var/lib/claude-soma/engagement/` not created | Units in repo + live; runtime dir + `queue.jsonl` hand-created. Drip feature dead on arrival. |
| 3 | `channel-clear` timer not installed | Same pattern. Transcripts accumulate without weekly purge. |
| 4 | `pw-refresh` timer not installed (per S2 audit) | Playwright cookie freshness check never runs. |
| 5 | `HERMES_FILES_PASSWORD` not prompted by wizard | `caddy-files-render.sh` exits ERROR; files relay has no auth config. |
| 6 | `HERMES_AUTO_RESTART_WINDOW_UTC` not prompted by wizard | Auto-restart guard silently disabled; orchestrator's L-FINOTIFY trigger no-ops. |
| 7 | `HERMES_INTERACTIVE_CEILING` + `HERMES_AGENT_SDK_CEILING` not in any docs | Usage tab ceiling cards render "—" (graceful but undocumented for the operator). |
| 8 | `TELEGRAM_BOT_TOKEN` + `HERMES_NOTIFY_CHAT_ID` absent from `/etc/claude-soma/secrets.env` (bot reads from `~/.claude/channels/telegram/.env` fallback) | Works today by luck — but no clear single source of truth. |
| 9 | `CLAUDE_CODE_OAUTH_TOKEN` is in live secrets.env but the wizard never explains where to obtain it | Operator hits the chicken-and-egg: must run `claude` once interactively to mint the token, then copy it in. |
| 10 | Frontend service ExecStart mismatch | Live unit uses `/usr/bin/node .next/standalone/server.js`; wizard generates `bun run start`. Functionally different entrypoints. |
| 11 | `relay-cleanup` timer in install path but NOT live | Bootstrap and `install.py` would install it; somehow it was never enabled on the production server. Relay dir grows unbounded. |
| 12 | `claude-safe` wrapper has no install docs | The wrapper script is shipped but README does not mention it. |
| 13 | `hermes-notify` MCP setup has no install docs | The lead-mcp.json registration is implicit in the spawner; operator has no install-time guidance. |
| 14 | No `secrets.env.example` template anywhere | Operator has no canonical list of every required + optional env var. |
| 15 | 3 CLI binaries (`grok`, `codex`, `hf`) have ZERO install instructions in repo | Each is referenced in code but no install script + no README/NEXT mention. |
| 16 | `ngrok` auth token not provisioned by bootstrap (mentioned but skipped) | If any feature still uses ngrok (legacy file relay), it fails. |

---

## Part 3 — Hand-applied state inventory

Concrete state currently on the live VPS that exists OUTSIDE the repo + install path:

### Files in `/opt/claude-soma` not tracked by git

| Path | Type | Risk |
|---|---|---|
| `caddy/files.caddyfile.in` | MODIFIED (uncommitted) | HIGH — affects files domain reproducibility |
| `systemd/claude-soma-daily-status.service` | NOT IN REPO | HIGH — referenced by timer; install copies missing |
| `.mcp.json.bak-1780047917` | Backup | Low |
| `broadcast.jsonl` | Runtime state | Low (expected) |
| `google/soma-service-account.json` | Untracked credentials | UNKNOWN — consumer not identified |
| `scripts/daily_status.sh.preW1D-backup` | Backup | Low |

### Files in `/etc/systemd/system/` from backups/manual installs

- `claude-soma-channel.service.bak-20260526-073557`
- `claude-soma-daily-status.timer.bak-1780119710`
- `claude-soma-pw-refresh.timer.bak-1780119710`
- `claude-soma-usage-snapshot.timer.bak-1780119710`

Clutter from prior in-place hotfixes; low risk.

### Live systemd units NOT covered by any install script (7 unit pairs = 14 unit files)

- `claude-soma-channel-clear.{service,timer}`
- `claude-soma-engagement-drip.{service,timer}`
- `claude-soma-daily-status.{service,timer}` (timer in repo, service NOT in repo)
- `claude-soma-listener-healthcheck.{service,timer}`
- `claude-soma-pw-refresh.{service,timer}`

### Runtime directories not created by any install script

- `/var/lib/claude-soma/engagement/` (created hand-on 2026-06-03)
- `/var/lib/claude-soma/engagement/queue.jsonl` (touched manually)
- `/var/lib/claude-soma/listener-healthcheck.state` (created on first healthcheck run)
- `/var/lib/claude-soma/relay/*` (server-side state for files domain — markserv writes here)

### Secrets keys in live `/etc/claude-soma/secrets.env` not prompted by wizard

- `HERMES_FILES_PASSWORD` (required for files domain bcrypt)
- `HERMES_AUTO_RESTART_WINDOW_UTC` (required for auto-restart guard)

### Secrets keys referenced by code but NOT in live secrets.env (work by fallback)

- `TELEGRAM_BOT_TOKEN` — falls back to `~/.claude/channels/telegram/.env`
- `HERMES_NOTIFY_CHAT_ID` — falls back to hardcoded chat id `935376085`
- `CLAUDE_CODE_OAUTH_TOKEN` — required but no fallback; if missing, claude-soma-channel fails at start

---

## Part 4 — Corrected end-to-end install runbook

This is the runbook that WOULD work on a bare Ubuntu 24.04 VPS today, assuming all the gaps above were filled. **Do not run this as-is yet — the gaps need to be fixed first.** This is the target shape.

### Step 0 — Pre-flight

External accounts you MUST provision first:
1. **Claude Max OAuth** — sign up at claude.ai; run `claude` once on a dev machine to mint `CLAUDE_CODE_OAUTH_TOKEN`; copy the value into a notes file.
2. **GitHub OAuth App** — create at `https://github.com/settings/developers`. Callback URL = `https://soma.mayankgupta.in/api/auth/callback/github`. Save `AUTH_GITHUB_CLIENT_ID` + `AUTH_GITHUB_CLIENT_SECRET`.
3. **Telegram Bot** — talk to `@BotFather`, save `TELEGRAM_BOT_TOKEN`; get your personal `HERMES_NOTIFY_CHAT_ID` (DM your bot, hit `https://api.telegram.org/bot<token>/getUpdates`, look for `chat.id`).
4. **xAI account** for `grok` CLI.
5. **ChatGPT subscription** for `codex` CLI.
6. **Hugging Face account** (free) for `hf` CLI.
7. **Domain** + DNS A-records for `soma.mayankgupta.in` and `files.mayankgupta.in` pointing at the VPS public IP.

### Step 1 — Provision VPS

- Ubuntu 24.04, 2+ GB RAM, 30+ GB disk, public IPv4.
- SSH in as `ubuntu` (or `root` then create `ubuntu`).
- Open ports 80 + 443.
- On Oracle Cloud: the iptables fix is critical (see B6 below in corrected bootstrap).

### Step 2 — Clone repo to `/opt`

```bash
sudo mkdir -p /opt/claude-soma
sudo chown ubuntu:ubuntu /opt/claude-soma
cd /opt/claude-soma
git clone https://github.com/techfreakworm/claude-soma.git .
```

### Step 3 — Run bootstrap (apt + node + python venv + Caddy install)

```bash
cd /opt/claude-soma
sudo bash scripts/vps_bootstrap.sh --cloud=oci   # --cloud=oci ONLY if on OCI
```

What this currently does:
- apt install: caddy, tmux, ffmpeg, sqlite3, jq, node, npm, python3.12-venv, build-essential, git, curl, ...
- npm install -g: claude (the @anthropic-ai/claude-code CLI), playwright-mcp
- Creates `/etc/claude-soma`, `/var/log/claude-soma`, `/var/lib/claude-soma/{relay,staging}`
- Renders Caddyfile (CURRENTLY BROKEN per B3+B4)
- Enables ~19 of 26 live units (gaps per Part 2)

What it does NOT do (gaps to fill):
- Install `markserv` globally (B5)
- Install `grok`, `codex`, `hf` CLIs (gap 15)
- Create `/var/lib/claude-soma/engagement/` + seed `queue.jsonl` (gap 2)
- Install the 7 missing systemd unit pairs (Part 2 items 1–4)
- Preserve `import /etc/caddy/conf.d/*.caddyfile` in Caddyfile (B3)
- Reconcile `files.caddyfile.in` template with the live binary-routing config (B4)

### Step 4 — Install Python package + Python deps

```bash
cd /opt/claude-soma
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
# Note: `hf` (Hugging Face CLI) is NOT in pyproject.toml — install separately:
pip install 'huggingface_hub[cli]'
```

### Step 5 — Build frontend (dashboard)

```bash
cd /opt/claude-soma
bash scripts/build_frontend.sh   # includes the .next/standalone/.next/static cp step
```

### Step 6 — Provision external CLIs (NOT in bootstrap)

```bash
# grok (xAI):
curl -L https://grok.com/cli/install.sh | bash   # confirm with vendor docs
# codex (OpenAI ChatGPT):
# install per https://github.com/openai/codex
# claude CLI:
sudo npm install -g @anthropic-ai/claude-code
# hf CLI (already covered in step 4):
# already installed via pip
```

### Step 7 — Mint OAuth tokens + log in to external CLIs

```bash
# Claude OAuth — interactive:
claude login   # follow browser flow; copy resulting token
# codex:
codex login   # ChatGPT browser flow
# grok:
grok login    # xAI flow
# hf:
hf auth login
```

### Step 8 — Provision `/etc/claude-soma/secrets.env`

Create `/etc/claude-soma/secrets.env` with the FULL key set (currently no template exists):

```bash
sudo install -o ubuntu -g ubuntu -m 600 /dev/null /etc/claude-soma/secrets.env
sudo -u ubuntu tee /etc/claude-soma/secrets.env <<'EOF'
# Claude
CLAUDE_CODE_OAUTH_TOKEN=<from step 7>

# GitHub OAuth (dashboard auth)
AUTH_GITHUB_CLIENT_ID=<from step 0>
AUTH_GITHUB_CLIENT_SECRET=<from step 0>
AUTH_GITHUB_OWNER=techfreakworm
AUTH_GITHUB_TEAM=                # leave empty if not using team gating
NEXTAUTH_SECRET=<openssl rand -base64 32>
NEXTAUTH_URL=https://soma.mayankgupta.in

# Telegram
TELEGRAM_BOT_TOKEN=<from step 0>
HERMES_NOTIFY_CHAT_ID=<your chat id from step 0>
TELEGRAM_CHAT_ID=<same as HERMES_NOTIFY_CHAT_ID for safety>

# Files relay (basicauth)
HERMES_FILES_PASSWORD=<choose a strong password>

# Auto-restart guard (optional — set when you want to enable a window)
# HERMES_AUTO_RESTART_WINDOW_UTC=<epoch seconds for window expiry>

# Usage-tab ceilings (optional — set to enable the usage-tab ceiling cards)
# HERMES_INTERACTIVE_CEILING=10000
# HERMES_AGENT_SDK_CEILING=50000

# Engagement drip (optional — defaults work for v1)
# HERMES_ENGAGEMENT_REFILL_THRESHOLD=6
EOF
```

### Step 9 — Render Caddyfile + reload Caddy

```bash
# Install repo Caddyfile preserving the conf.d import line:
sudo install -m 644 /opt/claude-soma/Caddyfile /etc/caddy/Caddyfile
echo "" | sudo tee -a /etc/caddy/Caddyfile
echo "import /etc/caddy/conf.d/*.caddyfile" | sudo tee -a /etc/caddy/Caddyfile

# Render files.caddyfile from the live (NOT template) — DO NOT run caddy-files-render.sh until B4 is fixed:
# (For now: manually copy the live config out of band, or wait for the template to be unbroken.)

sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy.service
```

### Step 10 — Install systemd units (ALL 26)

```bash
for unit in /opt/claude-soma/systemd/claude-soma-*.service /opt/claude-soma/systemd/claude-soma-*.timer; do
    sudo install -m 644 "$unit" /etc/systemd/system/
done

# Plus the 1 service NOT in repo (must commit first per D1):
# sudo install -m 644 /opt/claude-soma/systemd/claude-soma-daily-status.service /etc/systemd/system/

sudo systemctl daemon-reload

# Enable the long-running services:
sudo systemctl enable --now \
    claude-soma-api.service \
    claude-soma-frontend.service \
    claude-soma-markserv.service \
    claude-soma-channel.service

# Enable the timers:
sudo systemctl enable --now \
    claude-soma-healthcheck.timer \
    claude-soma-cache-refresh.timer \
    claude-soma-secrets-backup.timer \
    claude-soma-pw-refresh.timer \
    claude-soma-usage-snapshot.timer \
    claude-soma-rc-url-refresh.timer \
    claude-soma-idle-reaper.timer \
    claude-soma-daily-status.timer \
    claude-soma-listener-healthcheck.timer \
    claude-soma-engagement-drip.timer \
    claude-soma-channel-clear.timer \
    claude-soma-relay-cleanup.timer
```

### Step 11 — Create the engagement runtime directory

```bash
sudo mkdir -p /var/lib/claude-soma/engagement
sudo chown ubuntu:ubuntu /var/lib/claude-soma/engagement
sudo touch /var/lib/claude-soma/engagement/queue.jsonl
sudo chown ubuntu:ubuntu /var/lib/claude-soma/engagement/queue.jsonl
sudo chmod 644 /var/lib/claude-soma/engagement/queue.jsonl
```

### Step 12 — Pair the Telegram bot

Open Telegram, DM your bot, send `/start`. The channel session picks up the chat id automatically.

### Step 13 — Smoke verify

```bash
# Services up:
systemctl is-active claude-soma-api claude-soma-frontend claude-soma-markserv claude-soma-channel

# Public URLs:
curl -sI https://soma.mayankgupta.in/ | head -3
curl -sI -u "soma:$HERMES_FILES_PASSWORD" https://files.mayankgupta.in/ | head -3

# Timers:
systemctl list-timers 'claude-soma-*' --no-pager

# Logs clean:
sudo journalctl -u claude-soma-channel --since '5 min ago' | tail -20
```

If all green: the system is up. Open `soma.mayankgupta.in`, sign in with GitHub, browse `/admin`.

---

## Part 5 — Prioritized fix list (DO NOT IMPLEMENT YET — for user review)

Ordered by impact + effort. Each entry is "what to fix" + "file(s) to change" + "rough effort".

### Tier 1 — Showstoppers (without these, fresh install hard-fails)

1. **Fix B3 (Caddyfile import line):** add `import /etc/caddy/conf.d/*.caddyfile` to the repo `Caddyfile` at the bottom. Effort: S (1-line edit).
2. **Fix B4 (`files.caddyfile.in` drift):** decide what the canonical template should be. Either (a) sync the template up to match the live config (preserving the `@binary` matcher + markserv routing), or (b) abandon the template and commit `caddy/files.caddyfile` directly (verbatim, no rendering). Recommend (b) for simplicity. Effort: S-M.
3. **Fix B5 (markserv install):** add `sudo npm install -g markserv` to `vps_bootstrap.sh` step 14f (BEFORE the systemd unit install). Effort: S (1 line).
4. **Fix B6 (`--cloud=oci` flag):** document in README + NEXT that OCI users MUST pass `--cloud=oci`. Effort: S.
5. **Fix B7 (`daily-status.service` not in repo):** commit `systemd/claude-soma-daily-status.service`. Effort: S.
6. **Fix B8 (`google/soma-service-account.json`):** investigate consumer. Either commit the path (with secret-management) or remove if unused. Effort: M.
7. **Fix B1 (README Quickstart):** rewrite Quickstart to use the corrected runbook in Part 4. Effort: M.
8. **Fix B2 (NEXT.md B11 unit names):** rename `hermes-*` → `claude-soma-*` throughout NEXT.md. Effort: S.

### Tier 2 — Silent feature-degradation gaps

9. **Add 4 missing unit pairs to install path** (listener-healthcheck, engagement-drip, channel-clear, pw-refresh): edit `vps_bootstrap.sh` and `install.py` to cp + enable them. Add the `engagement` runtime dir + queue seed. Effort: M.
10. **Add `secrets.env.example`** at repo root with every required + optional key (use Step 8 of the corrected runbook as the source of truth). Effort: S.
11. **Add wizard prompts for `HERMES_FILES_PASSWORD` + `HERMES_AUTO_RESTART_WINDOW_UTC`** in `wizard/init.py`. Effort: S.
12. **Document the 3 external CLIs (`grok`, `codex`, `hf`)** in README + add install commands to a new `scripts/install_external_clis.sh`. Effort: M.
13. **Reconcile frontend service ExecStart** (live: `/usr/bin/node .next/standalone/server.js`; wizard: `bun run start`). Pick one + sync. Effort: S.
14. **Fix CLAUDE.md status block** (claims Weeks 3–4 not started; both shipped). Effort: S.
15. **Domain mismatch sweep** — replace `claude.mayankgupta.in` with `soma.mayankgupta.in` (and any `files.mayankgupta.in` references) in NEXT.md and any other docs. Effort: S.

### Tier 3 — Polish + future-proofing

16. **Add a single `scripts/full_install.sh`** that wraps Steps 2–13 of the corrected runbook into one command. Effort: M.
17. **Add post-install smoke-test script** (`scripts/smoke_install.sh`) that runs the Step 13 verifications and reports pass/fail per check. Effort: M.
18. **Document `claude-safe` wrapper** + `hermes-notify` MCP setup in README. Effort: S.
19. **Document `relay-cleanup` enable step** (it's installed but not enabled live). Effort: S.

---

## Part 6 — Open questions for user (before any fix)

1. **`files.caddyfile.in` strategy** — option (a) keep templating + sync template to live config; option (b) commit live `files.caddyfile` verbatim + retire the renderer. Default recommend (b).
2. **`google/soma-service-account.json`** — what is it for? Should it be committed (secret-encrypted), placed under `/etc/claude-soma/`, or removed?
3. **`claude` CLI install** — is it OK to add `sudo npm install -g @anthropic-ai/claude-code` to bootstrap, or should the Quickstart instead instruct the operator to follow Anthropic's official install path?
4. **Wizard scope** — should the wizard be expanded to a full one-shot installer (`scripts/full_install.sh`), or kept as a partial "after-bootstrap" config tool?
5. **Should `secrets.env.example` include all optional keys** (HERMES_ENGAGEMENT_*, HERMES_*_CEILING, HERMES_ALARM_*, etc.) commented out, or only the required ones?

---

## Part 7 — Per-subagent reports (appendix)

Full details from each auditor:
- **PIA-S1 (docs accuracy):** `/tmp/PIA-S1-docs.md` — 14 recommended doc edits
- **PIA-S2 (install tooling):** `/tmp/PIA-S2-install.md` — coverage matrix; 7 units no install path
- **PIA-S3 (deps + secrets):** `/tmp/PIA-S3-deps.md` — 6 gaps; no `secrets.env.example`
- **PIA-S4 (drift):** `/tmp/PIA-S4-drift.md` — 8 drift items, 3 showstoppers + classified 2-week commit log

---

## Recommendation

Do NOT attempt a fresh install on a new VPS today. The work in this audit must land first:
- At minimum: Tier 1 fixes (8 items).
- Strongly recommended: Tier 2 fixes (7 items).
- Then run a dry-install in an isolated environment (LXC container or fresh OCI free-tier VM) to validate.

Once the audit fixes land, the corrected runbook in Part 4 should produce a working system from a bare Ubuntu 24.04 VPS in under 30 minutes (excluding external-account setup).

NO IMPLEMENTATION in this push. Awaiting user direction on the fix priorities + the 5 open questions.
