#!/usr/bin/env bash
# scripts/bootstrap.sh — canonical on-VPS installer for claude-soma
#
# Run this on a FRESH Ubuntu 24.04 VPS to bring claude-soma up end-to-end.
# Assumes:
#   - You SSH'd in as ubuntu (with sudo)
#   - You git clone'd this repo to /opt/claude-soma
#   - Ports 80 + 443 open; public IP
#   - DNS A-records for soma.<your-domain> + files.<your-domain> already point at this VPS
#
# Use --cloud=oci on Oracle Cloud Free Tier to apply the iptables fix
# that makes Caddy publicly reachable.
#
# This script is IDEMPOTENT — safe to re-run after a failed step.
#
# After this runs successfully:
#   1. Copy secrets.env.example to /etc/claude-soma/secrets.env + edit it
#   2. Run: sudo bash scripts/smoke_install.sh
#
# DO NOT confuse with scripts/deploy.sh — that's a dev-machine→remote rsync tool.
#
# OPTIONAL EXTRAS not covered here (voice STT/TTS, Docker, playwright, bun, ngrok):
#   bash scripts/vps_bootstrap.sh [--cloud=oci]
#
# Relationship to other install tooling:
#   scripts/vps_bootstrap.sh  — predecessor partial installer; voice+Docker extras
#   src/claude_soma/install.py — Python installer (dry-run/apply mode; uses platform layer)
#   src/claude_soma/wizard/init.py — interactive wizard (domain/OAuth/Telegram prompts)
#   This script (bootstrap.sh) is the single recommended entry point for a fresh VPS.

set -euo pipefail

# ---------------------------------------------------------------------------
# CLI flags
# ---------------------------------------------------------------------------
SOMA_CLOUD="${SOMA_CLOUD:-}"
for _arg in "$@"; do
    case "$_arg" in
        --cloud=*) SOMA_CLOUD="${_arg#--cloud=}" ;;
    esac
done

LOG=/tmp/soma-bootstrap-$(date +%Y%m%d-%H%M%S).log
exec > >(tee -a "$LOG") 2>&1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SYSTEMD_SRC="${REPO_ROOT}/systemd"

step() { echo; echo "==== $* ===="; }

echo "claude-soma bootstrap starting at $(date)"
echo "REPO_ROOT: ${REPO_ROOT}"
echo "Log: ${LOG}"

# ---------------------------------------------------------------------------
step "1/15  apt: base packages"
# ---------------------------------------------------------------------------
sudo DEBIAN_FRONTEND=noninteractive apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    build-essential git curl wget pkg-config unzip \
    python3.12 python3.12-venv python3.12-dev python3-pip \
    ffmpeg cmake tmux jq sqlite3 \
    libssl-dev \
    debian-keyring debian-archive-keyring apt-transport-https \
    rsync ca-certificates gnupg
# Optional: libreoffice (needed by ppt-manager feature — ~500 MB download)
# Uncomment to install: sudo DEBIAN_FRONTEND=noninteractive apt-get install -y libreoffice

# ---------------------------------------------------------------------------
step "1b/15  Caddy v2 (custom apt source)"
# ---------------------------------------------------------------------------
if ! command -v caddy >/dev/null 2>&1; then
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    sudo DEBIAN_FRONTEND=noninteractive apt-get update -y
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y caddy
fi
caddy version

# ---------------------------------------------------------------------------
step "2/15  Node 22 + pnpm"
# ---------------------------------------------------------------------------
if ! command -v node >/dev/null 2>&1; then
    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
fi
node --version
if ! command -v pnpm >/dev/null 2>&1; then
    sudo npm install -g pnpm
fi
pnpm --version

# ---------------------------------------------------------------------------
step "3/15  Claude Code CLI (npm global + native binary for --channels)"
# ---------------------------------------------------------------------------
# npm global gives /usr/bin/claude; native binary at ~/.local/bin/claude is
# required for `claude --channels` routing (the npm wrap silently rejects it).
if ! npm ls -g @anthropic-ai/claude-code >/dev/null 2>&1; then
    sudo npm install -g @anthropic-ai/claude-code
fi
# Native binary (idempotent: skip if already present)
if [ ! -x "$HOME/.local/bin/claude" ]; then
    claude install latest || true
fi
if ! grep -q 'local/bin' "$HOME/.bashrc" 2>/dev/null; then
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
fi
"$HOME/.local/bin/claude" --version 2>/dev/null || claude --version || true

# ---------------------------------------------------------------------------
step "4/15  markserv@1.17.4  (pinned — markdown preview for files domain)"
# ---------------------------------------------------------------------------
if ! npm ls -g markserv 2>/dev/null | grep -q markserv; then
    sudo npm install -g markserv@1.17.4
fi
markserv --version 2>/dev/null || echo "markserv installed"

# ---------------------------------------------------------------------------
step "5/15  Python venv + claude-soma package + huggingface_hub[cli]"
# ---------------------------------------------------------------------------
if [[ ! -d "${REPO_ROOT}/.venv" ]]; then
    python3.12 -m venv "${REPO_ROOT}/.venv"
fi
"${REPO_ROOT}/.venv/bin/pip" install --upgrade pip
"${REPO_ROOT}/.venv/bin/pip" install -e "${REPO_ROOT}"
# huggingface_hub[cli] is NOT in pyproject.toml but required by the hf CLI
"${REPO_ROOT}/.venv/bin/pip" install 'huggingface_hub[cli]'

# ---------------------------------------------------------------------------
step "6/15  create runtime directories"
# ---------------------------------------------------------------------------
sudo install -d -m 755 -o ubuntu -g ubuntu /var/log/claude-soma
sudo install -d -m 755 -o ubuntu -g ubuntu /var/lib/claude-soma
sudo install -d -m 755 -o ubuntu -g ubuntu /var/lib/claude-soma/relay
sudo install -d -m 755 -o ubuntu -g ubuntu /var/lib/claude-soma/staging
sudo install -d -m 755 -o ubuntu -g ubuntu /var/lib/claude-soma/engagement
sudo install -d -m 700 -o ubuntu -g ubuntu /etc/claude-soma
sudo install -d -m 700 -o ubuntu -g ubuntu /home/ubuntu/secrets-backups
sudo install -d -m 755 -o ubuntu -g ubuntu /home/ubuntu/hermes-work
sudo install -d -m 755 -o ubuntu -g ubuntu /home/ubuntu/.claude-soma
# Seed the engagement queue (engagement-drip.service reads it at start)
if [[ ! -f /var/lib/claude-soma/engagement/queue.jsonl ]]; then
    sudo touch /var/lib/claude-soma/engagement/queue.jsonl
    sudo chown ubuntu:ubuntu /var/lib/claude-soma/engagement/queue.jsonl
    sudo chmod 644 /var/lib/claude-soma/engagement/queue.jsonl
    echo "  created /var/lib/claude-soma/engagement/queue.jsonl"
fi
ls -ld /var/log/claude-soma /var/lib/claude-soma /etc/claude-soma

# ---------------------------------------------------------------------------
step "7/15  frontend build (pnpm install + build_frontend.sh standalone copy)"
# ---------------------------------------------------------------------------
# build_frontend.sh does: pnpm install + pnpm build + copies .next/static +
# public/ next to server.js in .next/standalone/.
# pnpm 10+ blocks build scripts by default; frontend/package.json configures
# pnpm.onlyBuiltDependencies to allow sharp, msw, @tailwindcss/oxide.
if ! bash "${REPO_ROOT}/scripts/build_frontend.sh"; then
    echo
    echo "FATAL: frontend build failed at step 7." >&2
    echo "  Common causes:" >&2
    echo "    1. pnpm 10 ignored-builds — check frontend/package.json pnpm.onlyBuiltDependencies" >&2
    echo "    2. Insufficient memory (next build needs ~1-2 GB free during build)" >&2
    echo "    3. Network failure during pnpm install" >&2
    echo "  Bootstrap will exit. Fix the cause + re-run scripts/bootstrap.sh (idempotent)." >&2
    exit 7
fi

# ---------------------------------------------------------------------------
step "8/15  install systemd unit files (all claude-soma-* .service + .timer)"
# ---------------------------------------------------------------------------
_units_installed=0
for unit in "${SYSTEMD_SRC}"/claude-soma-*.service "${SYSTEMD_SRC}"/claude-soma-*.timer; do
    [[ -f "$unit" ]] || continue
    unitname="$(basename "$unit")"
    sudo install -m 0644 -o root -g root "$unit" "/etc/systemd/system/${unitname}"
    echo "  installed ${unitname}"
    (( _units_installed++ )) || true
done
echo "  total units installed: ${_units_installed}"

# ---------------------------------------------------------------------------
step "9/15  systemctl daemon-reload"
# ---------------------------------------------------------------------------
sudo systemctl daemon-reload

# ---------------------------------------------------------------------------
step "10/15  enable long-running services (start immediately)"
# ---------------------------------------------------------------------------
# These are the 4 persistent services that must be running at all times.
# Secrets must be in /etc/claude-soma/secrets.env before starting channel.service.
sudo systemctl enable --now \
    claude-soma-api.service \
    claude-soma-frontend.service \
    claude-soma-markserv.service \
    claude-soma-channel.service

# ---------------------------------------------------------------------------
step "11/15  enable timers (start immediately)"
# ---------------------------------------------------------------------------
# All 12 production timers. Re-enabling an already-enabled timer is a no-op.
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

# ---------------------------------------------------------------------------
step "12/15  install Caddyfile"
# ---------------------------------------------------------------------------
# NOTE: The repo Caddyfile must include `import /etc/caddy/conf.d/*.caddyfile`
# at the bottom (added by W2 task). Without that line, the files relay domain
# (files.<your-domain>) will be unreachable after this install step.
sudo install -m 644 "${REPO_ROOT}/Caddyfile" /etc/caddy/Caddyfile

# ---------------------------------------------------------------------------
step "13/15  caddy validate + reload"
# ---------------------------------------------------------------------------
if ! sudo caddy validate --config /etc/caddy/Caddyfile 2>&1; then
    echo "WARNING: caddy config validation failed — skipping reload." >&2
    echo "  Fix /etc/caddy/Caddyfile or check DNS before proceeding." >&2
    echo "  To reload manually: sudo caddy validate && sudo systemctl reload caddy" >&2
else
    sudo systemctl reload caddy.service
    echo "  Caddy reloaded OK"
fi

# ---------------------------------------------------------------------------
step "14/15  OCI iptables (--cloud=oci only)"
# ---------------------------------------------------------------------------
# Oracle Cloud's default INPUT chain ends with REJECT all icmp-host-prohibited.
# New ACCEPT rules must be inserted BEFORE that REJECT, not after.
# Pass --cloud=oci (or export SOMA_CLOUD=oci) to run this step.
if [ "${SOMA_CLOUD:-}" = "oci" ]; then
    reject_line=$(sudo iptables -L INPUT --line-numbers -n \
        | awk '/REJECT.*icmp-host-prohibited/ {print $1; exit}')
    if [ -n "${reject_line:-}" ]; then
        insert_at="$reject_line"
    else
        insert_at=$(sudo iptables -L INPUT -n --line-numbers | wc -l)
    fi
    echo "  REJECT rule at line ${reject_line:-(none)}; inserting ACCEPTs at ${insert_at}"
    for port in 443 80; do
        if ! sudo iptables -C INPUT -m state --state NEW -p tcp \
                --dport "$port" -j ACCEPT 2>/dev/null; then
            sudo iptables -I INPUT "$insert_at" -m state --state NEW \
                -p tcp --dport "$port" -j ACCEPT
        fi
    done
    sudo iptables -L INPUT -n --line-numbers | head -10
    sudo netfilter-persistent save 2>&1 | tail -2 || true
else
    echo "  skipping OCI iptables; pass --cloud=oci if on Oracle Cloud"
fi

# ---------------------------------------------------------------------------
step "15/17  DONE — next steps"
# ---------------------------------------------------------------------------
cat <<'NEXT'

Bootstrap complete. Required next steps:

  1. Provision the secrets file (REQUIRED before services will start cleanly):
       sudo install -o ubuntu -g ubuntu -m 600 /dev/null /etc/claude-soma/secrets.env
       sudo -u ubuntu nano /etc/claude-soma/secrets.env
     Keys needed (minimum):
       CLAUDE_CODE_OAUTH_TOKEN=<from: claude login on a dev machine>
       AUTH_GITHUB_CLIENT_ID=<GitHub OAuth app>
       AUTH_GITHUB_CLIENT_SECRET=<GitHub OAuth app>
       AUTH_GITHUB_OWNER=techfreakworm
       NEXTAUTH_SECRET=<openssl rand -base64 32>
       NEXTAUTH_URL=https://soma.<your-domain>
       TELEGRAM_BOT_TOKEN=<from @BotFather>
       HERMES_NOTIFY_CHAT_ID=<your Telegram chat id>
       HERMES_FILES_PASSWORD=<choose a strong password>

  2. Restart services after filling secrets:
       sudo systemctl restart claude-soma-api.service
       sudo systemctl restart claude-soma-channel.service

  3. Run the smoke-install checker (once it exists):
       sudo bash scripts/smoke_install.sh

  4. Optional extras (voice STT/TTS, Docker, playwright, bun, ngrok):
       bash scripts/vps_bootstrap.sh [--cloud=oci]

  5. On Oracle Cloud only: run with --cloud=oci flag:
       bash scripts/bootstrap.sh --cloud=oci

NEXT

# ---------------------------------------------------------------------------
step "16/17  DNS guidance — A records the operator must add"
# ---------------------------------------------------------------------------
if [[ -x "${REPO_ROOT}/scripts/show-dns-setup.sh" ]]; then
    bash "${REPO_ROOT}/scripts/show-dns-setup.sh"
fi

# ---------------------------------------------------------------------------
step "17/17  Configure secrets (Option A: nano · Option B: claude copilot)"
# ---------------------------------------------------------------------------
cat <<'EOF'

==============================================================
FINAL STEP — configure your secrets
==============================================================

You still need to fill in /etc/claude-soma/secrets.env with your real
credentials before the dashboard + bot can fully start. Two ways:

  OPTION A — do it yourself
  --------------------------
  1.  sudo cp /opt/claude-soma/secrets.env.example /etc/claude-soma/secrets.env
  2.  sudo chmod 600 /etc/claude-soma/secrets.env
  3.  sudo chown ubuntu:ubuntu /etc/claude-soma/secrets.env
  4.  sudo nano /etc/claude-soma/secrets.env   # fill in every required key
  5.  sudo systemctl restart claude-soma-channel.service \
         claude-soma-api.service claude-soma-frontend.service
  6.  sudo bash /opt/claude-soma/scripts/smoke_install.sh

  See INSTALL.md "Secrets" section for what each key means and where
  to obtain it.

  OPTION B — let Claude copilot it for you
  -----------------------------------------
  If 'claude --version' works on this box (Claude Code CLI installed
  + authenticated), run:

    claude

  Then paste the contents of:

    /opt/claude-soma/scripts/env-copilot-prompt.txt

  (You can preview it first with:  cat /opt/claude-soma/scripts/env-copilot-prompt.txt)

  Claude will then walk you through each secret one at a time,
  explain what it is + where to obtain it, write your values to
  /etc/claude-soma/secrets.env with the right permissions, validate
  nothing is missing, and offer to restart the services + run the
  smoke verifier — all in a single hand-held conversation.

==============================================================

EOF

echo "Log written to: ${LOG}"
