#!/usr/bin/env bash
# scripts/vps_bootstrap.sh
#
# NOTE: This script is the PREDECESSOR to scripts/bootstrap.sh.
# For a fresh VPS install, run scripts/bootstrap.sh FIRST (it covers all
# required services + units + markserv + engagement dir + all 12 timers).
# Then run THIS script for optional extras:
#   - voice STT/TTS (whisper.cpp + piper)
#   - Docker
#   - playwright chromium
#   - bun runtime
#   - ngrok
# See: scripts/bootstrap.sh — canonical on-VPS installer (idempotent).
#
# Idempotent OS-level bootstrap for a fresh OCI Ubuntu 24.04 ARM VPS:
#   - 8 GB swap (sized for a 4-16 GB RAM box)
#   - iptables ingress for 80/443 INSERTED AT THE RIGHT POSITION
#   - apt: build deps, Python 3.12, ffmpeg, tmux, jq, Node 22, pnpm, Caddy,
#          Claude Code CLI, Bun (for the telegram plugin's MCP server),
#          gh (GitHub CLI), unzip (Bun installer requires it)
#   - claude-soma directories (/etc/claude-soma, /var/log/claude-soma, /home/ubuntu/hermes-work)
#
# The iptables-position bug this script fixes:
#   Oracle's default IPv4 INPUT chain ends with `REJECT all -- icmp-host-prohibited`
#   at position 5 (after lo, ESTABLISHED, ICMP, SSH). A naive
#   `iptables -I INPUT 6 ... ACCEPT --dport 80` inserts AFTER the REJECT —
#   so the new ACCEPT rule never fires, and the public can't reach Caddy.
#   The correct insertion point is BEFORE the REJECT, at position 5.
#
# Run as ubuntu user with sudo available:
#   bash /opt/claude-soma/scripts/vps_bootstrap.sh

set -euo pipefail

# ---------------------------------------------------------------------------
# CLI / env flags
# ---------------------------------------------------------------------------
# --cloud=oci  (or SOMA_CLOUD=oci) enables the OCI-specific iptables ordering
#              step (step 2/9).  On Oracle Cloud, the default INPUT chain ends
#              with a REJECT rule; new ACCEPT rules must be inserted BEFORE it.
#              If you are NOT on OCI (or you already handle this yourself), omit
#              this flag and the step is skipped with a short notice.
#
# --with-large-whisper  (or WHISPER_INCLUDE_LARGE=1) also downloads the
#              ggml-large-v3-turbo model in step 13/15 alongside base.en.
# ---------------------------------------------------------------------------
SOMA_CLOUD="${SOMA_CLOUD:-}"
WHISPER_INCLUDE_LARGE="${WHISPER_INCLUDE_LARGE:-0}"
for _arg in "$@"; do
    case "$_arg" in
        --cloud=*)  SOMA_CLOUD="${_arg#--cloud=}" ;;
        --with-large-whisper) WHISPER_INCLUDE_LARGE=1 ;;
    esac
done

LOG=/tmp/soma-bootstrap.log
exec > >(tee -a "$LOG") 2>&1

step() { echo; echo "==== $* ===="; }

step "1/9  add 8 GB swap"
if [ ! -f /swapfile ]; then
    sudo fallocate -l 8G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
    sudo sysctl -w vm.swappiness=10
    echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swappiness.conf >/dev/null
fi
free -h | head -3

step "2/9  open iptables 80 + 443 (BEFORE the default REJECT)"
# This step is OCI-specific: Oracle's default IPv4 INPUT chain ends with a
# REJECT rule; new ACCEPT rules must be inserted BEFORE it (not after).
# Gate on --cloud=oci (or SOMA_CLOUD=oci) so non-OCI operators can safely run
# this script without unintended iptables mutations.
if [ "${SOMA_CLOUD:-}" = "oci" ]; then
    # Find the line number of Oracle's REJECT rule (it's the LAST rule but its
    # position varies if anyone added rules before us). Insert the ACCEPTs just
    # before it.
    reject_line=$(sudo iptables -L INPUT --line-numbers -n | awk '/REJECT.*icmp-host-prohibited/ {print $1; exit}')
    if [ -n "${reject_line:-}" ]; then
        insert_at="$reject_line"
    else
        # No REJECT found — append at the end (Oracle removed it, or someone
        # already cleaned the chain). Use the table length + 1.
        insert_at=$(sudo iptables -L INPUT -n --line-numbers | wc -l)
    fi
    echo "REJECT rule at line ${reject_line:-(none)}; inserting ACCEPTs at $insert_at"

    for port in 443 80; do
        if ! sudo iptables -C INPUT -m state --state NEW -p tcp --dport "$port" -j ACCEPT 2>/dev/null; then
            sudo iptables -I INPUT "$insert_at" -m state --state NEW -p tcp --dport "$port" -j ACCEPT
        fi
    done
    sudo iptables -L INPUT -n --line-numbers | head -10
    sudo netfilter-persistent save 2>&1 | tail -2
else
    echo "skipping OCI iptables ordering; pass --cloud=oci (or set SOMA_CLOUD=oci) if on Oracle Cloud"
fi

step "3/9  apt: base packages"
sudo DEBIAN_FRONTEND=noninteractive apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    build-essential git curl wget pkg-config unzip \
    python3.12 python3.12-venv python3.12-dev python3-pip \
    ffmpeg cmake clang tmux jq sqlite3 libsox-dev libssl-dev \
    debian-keyring debian-archive-keyring apt-transport-https \
    rsync ca-certificates gnupg

step "4/9  Node 22 + pnpm"
if ! command -v node >/dev/null 2>&1; then
    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
fi
if ! command -v pnpm >/dev/null 2>&1; then
    sudo npm install -g pnpm
fi
node --version
pnpm --version

step "5/9  Caddy v2"
if ! command -v caddy >/dev/null 2>&1; then
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    sudo DEBIAN_FRONTEND=noninteractive apt-get update -y
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y caddy
fi
caddy version

step "6/9  Claude Code CLI (both wraps; native install adds --channels support)"
if ! command -v claude >/dev/null 2>&1; then
    sudo npm install -g @anthropic-ai/claude-code
fi
# Native install — required for `claude --channels` (the npm wrap rejects it).
if [ ! -x "$HOME/.local/bin/claude" ]; then
    claude install latest
fi
echo 'export PATH="$HOME/.local/bin:$PATH"' | tee -a "$HOME/.bashrc" >/dev/null
"$HOME/.local/bin/claude" --version 2>/dev/null || claude --version

step "6b/9  claude-safe wrapper (interactive claude must not hijack the bot poller)"
# A bare interactive `claude` loads the user-scope telegram plugin, which SIGTERMs
# the live bot's Telegram poller (docs/KNOWN_BUGS.md #1). Install a wrapper that
# injects --setting-sources project,local for non-bot sessions, and shadow
# interactive `claude` with it for the ubuntu user. systemd services and the
# orchestrator spawner call claude by absolute path, so they are unaffected; this
# only catches a human typing `claude` at a shell.
CLAUDE_SAFE_SRC="$(dirname "${BASH_SOURCE[0]}")/claude-safe.sh"
[ -f "$CLAUDE_SAFE_SRC" ] || CLAUDE_SAFE_SRC=/opt/claude-soma/scripts/claude-safe.sh
if [ -f "$CLAUDE_SAFE_SRC" ]; then
    sudo install -m 755 "$CLAUDE_SAFE_SRC" /usr/local/bin/claude-safe
    if ! grep -q 'claude-safe wrapper' "$HOME/.bashrc" 2>/dev/null; then
        cat >> "$HOME/.bashrc" <<'RC'

# claude-safe wrapper (docs/KNOWN_BUGS.md #1): a bare interactive `claude` would
# load the user-scope telegram plugin and hijack the live bot's poller. Route
# interactive invocations through the wrapper, which skips the user-scope plugin.
# Absolute-path callers (systemd services, the orchestrator spawner) are unaffected.
claude() { /usr/local/bin/claude-safe "$@"; }
RC
    fi
    echo "claude-safe installed at /usr/local/bin/claude-safe; interactive 'claude' shadowed for ubuntu"
else
    echo "WARN: claude-safe.sh not found next to bootstrap or in /opt/claude-soma; skipping wrapper install" >&2
fi

# somux: list/attach/peek the per-socket project-lead tmux sessions. Each lead
# runs its OWN tmux server on socket soma-lead-<name>, so a plain `tmux ls`
# can't see them -- somux discovers them via the sockets + systemd units.
# Symlink it onto PATH pointing at the deploy-stable path, so a deploy that
# updates scripts/somux is picked up automatically. Idempotent (ln -sf).
if [ -e /opt/claude-soma/scripts/somux ]; then
    sudo ln -sf /opt/claude-soma/scripts/somux /usr/local/bin/somux
    echo "somux installed at /usr/local/bin/somux (somux ls | a <name> | peek <name>)"
else
    echo "WARN: /opt/claude-soma/scripts/somux not found; skipping somux symlink" >&2
fi

step "7/9  Bun (telegram plugin's MCP server runtime)"
if ! command -v bun >/dev/null 2>&1; then
    curl -fsSL https://bun.sh/install | bash
    # Make bun visible system-wide for systemd services + tmux subshells:
    sudo ln -sf "$HOME/.bun/bin/bun" /usr/local/bin/bun
fi
bun --version

step "8/9  gh (GitHub CLI for autonomous repo create + push)"
if ! command -v gh >/dev/null 2>&1; then
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg 2>/dev/null
    sudo chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
    sudo DEBIAN_FRONTEND=noninteractive apt-get update -y
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y gh
fi
gh --version | head -1

step "9/12  Docker (for the bot to spin up containers autonomously)"
if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
    sudo DEBIAN_FRONTEND=noninteractive apt-get update -y
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
        docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    sudo usermod -aG docker ubuntu
fi
docker --version
# Note: `usermod -aG docker ubuntu` doesn't refresh existing sessions. The
# ubuntu user must reconnect SSH (or use `sg docker -c '...'`) to invoke
# docker without sudo. The systemd channel.service inherits the active
# group set at start time — if it was started before this bootstrap,
# restart it AFTER all of bootstrap finishes, NOT mid-bootstrap.

step "10/12  Playwright MCP (web browsing for the bot, headless)"
if ! command -v playwright-mcp >/dev/null 2>&1; then
    sudo npm install -g @playwright/mcp
fi
playwright-mcp --version 2>/dev/null || echo "playwright-mcp: $(which playwright-mcp)"
# Chromium browser + system deps. Install system deps once with sudo, then
# install browser binary into each user's cache (system deps are shared).
sudo npx --yes playwright install-deps chromium 2>&1 | tail -3
# Browser binary for the ubuntu user (the one the bot runs as):
if [ ! -d "$HOME/.cache/ms-playwright" ] || [ -z "$(ls "$HOME/.cache/ms-playwright" 2>/dev/null)" ]; then
    npx --yes playwright install chromium 2>&1 | tail -3
fi
# Playwright MCP only accepts --browser={chrome,firefox,webkit,msedge}; "chrome"
# channel isn't shipped for Linux ARM64. Solution: point --executable-path at
# the chromium binary Playwright installed. A version-stable symlink lets the
# .mcp.json reference a path that survives chromium-version bumps (next bump
# just re-runs this step and points the symlink at the new path).
LATEST_CHROMIUM=$(ls -d "$HOME"/.cache/ms-playwright/chromium-*/chrome-linux/chrome 2>/dev/null | sort | tail -1)
if [ -n "$LATEST_CHROMIUM" ]; then
    sudo ln -sf "$LATEST_CHROMIUM" /usr/local/bin/playwright-chromium
    ls -la /usr/local/bin/playwright-chromium
else
    echo "WARN: no chromium binary found in $HOME/.cache/ms-playwright/"
fi

step "11/15  ngrok (random-URL tunnels for the bot's ad-hoc public endpoints)"
# Installed via the official apt repo so updates flow through apt. The
# auth token + tunnel config live in ~/.config/ngrok/ngrok.yml; rsync that
# file from your previous install — the binary alone is useless without it.
if ! command -v ngrok >/dev/null 2>&1; then
    curl -fsSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc \
        | sudo gpg --dearmor -o /etc/apt/keyrings/ngrok.gpg
    sudo chmod a+r /etc/apt/keyrings/ngrok.gpg
    echo "deb [signed-by=/etc/apt/keyrings/ngrok.gpg] https://ngrok-agent.s3.amazonaws.com bookworm main" \
        | sudo tee /etc/apt/sources.list.d/ngrok.list >/dev/null
    sudo DEBIAN_FRONTEND=noninteractive apt-get update -y
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y ngrok
fi
ngrok --version

step "12/15  piper (voice TTS — voice_tts MCP server)"
# Binary release from rhasspy/piper for Linux ARM64. The .mcp.json's
# HERMES_PIPER_BIN points at /opt/piper/piper and HERMES_PIPER_DEFAULT_VOICE
# points at /opt/piper/en_US-ryan-medium.onnx, so both must live there.
# The voice model + its .json sidecar both come from huggingface piper-voices.
PIPER_VERSION="2023.11.14-2"
if [ ! -x /opt/piper/piper ]; then
    sudo curl -fsSL "https://github.com/rhasspy/piper/releases/download/${PIPER_VERSION}/piper_linux_aarch64.tar.gz" \
        | sudo tar xz -C /opt
    sudo chown -R ubuntu:ubuntu /opt/piper
fi
PIPER_VOICE_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium"
if [ ! -f /opt/piper/en_US-ryan-medium.onnx ]; then
    sudo -u ubuntu curl -fsSL "${PIPER_VOICE_BASE}/en_US-ryan-medium.onnx" \
        -o /opt/piper/en_US-ryan-medium.onnx
fi
if [ ! -f /opt/piper/en_US-ryan-medium.onnx.json ]; then
    sudo -u ubuntu curl -fsSL "${PIPER_VOICE_BASE}/en_US-ryan-medium.onnx.json" \
        -o /opt/piper/en_US-ryan-medium.onnx.json
fi
ls -lh /opt/piper/piper /opt/piper/en_US-ryan-medium.onnx 2>&1 | head -2

step "13/15  whisper.cpp (voice STT — voice_stt MCP server + ggml-base.en.bin model (English-only, ~13x faster))"
# Git clone + cmake build. .mcp.json's HERMES_WHISPER_BIN expects
# /opt/whisper.cpp/build/bin/whisper-cli and HERMES_WHISPER_MODEL expects
# /opt/whisper.cpp/models/ggml-base.en.bin (English-only, ~13x faster:
# ~9s vs ~121s on a 77s note). This is the default and the value in .mcp.json.
# Pass --with-large-whisper (or set WHISPER_INCLUDE_LARGE=1) to also download
# ggml-large-v3-turbo.bin (~1.5 GB) for multilingual / higher-accuracy use;
# set HERMES_WHISPER_MODEL to activate it.
if [ ! -x /opt/whisper.cpp/build/bin/whisper-cli ]; then
    sudo install -d -m 755 -o ubuntu -g ubuntu /opt/whisper.cpp
    sudo -u ubuntu git clone https://github.com/ggerganov/whisper.cpp.git /opt/whisper.cpp.tmp
    sudo -u ubuntu cp -a /opt/whisper.cpp.tmp/. /opt/whisper.cpp/
    sudo rm -rf /opt/whisper.cpp.tmp
    sudo -u ubuntu cmake -S /opt/whisper.cpp -B /opt/whisper.cpp/build
    sudo -u ubuntu cmake --build /opt/whisper.cpp/build --config Release -j"$(nproc)"
fi
if [ ! -f /opt/whisper.cpp/models/ggml-base.en.bin ]; then
    sudo -u ubuntu bash -c 'cd /opt/whisper.cpp && bash ./models/download-ggml-model.sh base.en'
fi
if [ "${WHISPER_INCLUDE_LARGE:-0}" = "1" ]; then
    if [ ! -f /opt/whisper.cpp/models/ggml-large-v3-turbo.bin ]; then
        sudo -u ubuntu bash -c 'cd /opt/whisper.cpp && bash ./models/download-ggml-model.sh large-v3-turbo'
    fi
fi
echo "whisper-cli:"; ls -lh /opt/whisper.cpp/build/bin/whisper-cli 2>&1
echo "models:"; ls -lh /opt/whisper.cpp/models/ggml-*.bin 2>&1 | head -5

step "14/15  pre-create claude-soma directories"
sudo install -d -m 700 -o ubuntu -g ubuntu /etc/claude-soma
sudo install -d -m 755 -o ubuntu -g ubuntu /var/log/claude-soma
sudo install -d -m 755 -o ubuntu -g ubuntu /home/ubuntu/hermes-work
sudo install -d -m 700 -o ubuntu -g ubuntu /home/ubuntu/secrets-backups
sudo install -d -m 755 -o ubuntu -g ubuntu /home/ubuntu/.claude-soma
ls -ld /etc/claude-soma /var/log/claude-soma /home/ubuntu/hermes-work \
       /home/ubuntu/secrets-backups /home/ubuntu/.claude-soma

step "14b/15  logrotate for /var/log/claude-soma"
# Install the logrotate config (repo-tracked at scripts/logrotate-claude-soma).
LOGROTATE_SRC="$(dirname "${BASH_SOURCE[0]}")/logrotate-claude-soma"
[ -f "$LOGROTATE_SRC" ] || LOGROTATE_SRC=/opt/claude-soma/scripts/logrotate-claude-soma
if [ -f "$LOGROTATE_SRC" ]; then
    sudo install -m 0644 -o root -g root "$LOGROTATE_SRC" /etc/logrotate.d/claude-soma
    # Validate (debug mode: parses but does not rotate)
    sudo logrotate -d /etc/logrotate.d/claude-soma 2>&1 | tail -5
    echo "logrotate config installed at /etc/logrotate.d/claude-soma"
else
    echo "WARN: logrotate-claude-soma not found next to bootstrap or in /opt/claude-soma; skipping" >&2
fi

step "14c/15  secrets-backup timer"
# Install the oneshot service + daily timer for encrypted secrets backup.
BOOTSTRAP_DIR="$(dirname "${BASH_SOURCE[0]}")"
[ -d "$BOOTSTRAP_DIR" ] || BOOTSTRAP_DIR=/opt/claude-soma/scripts
SYSTEMD_SRC_DIR="${BOOTSTRAP_DIR}/../systemd"
[ -d "$SYSTEMD_SRC_DIR" ] || SYSTEMD_SRC_DIR=/opt/claude-soma/systemd
for unit in claude-soma-secrets-backup.service claude-soma-secrets-backup.timer; do
    if [ -f "${SYSTEMD_SRC_DIR}/${unit}" ]; then
        sudo install -m 0644 -o root -g root "${SYSTEMD_SRC_DIR}/${unit}" "/etc/systemd/system/${unit}"
        echo "installed /etc/systemd/system/${unit}"
    else
        echo "WARN: ${unit} not found in ${SYSTEMD_SRC_DIR}; skipping" >&2
    fi
done
if [ -f /etc/systemd/system/claude-soma-secrets-backup.timer ]; then
    sudo systemctl daemon-reload
    sudo systemctl enable --now claude-soma-secrets-backup.timer
    echo "claude-soma-secrets-backup.timer enabled"
fi
# Ensure the backup passphrase file exists (operator must populate it).
PASS_FILE=/etc/claude-soma/backup.pass
if [ ! -f "$PASS_FILE" ]; then
    sudo install -m 0600 -o root -g root /dev/null "$PASS_FILE"
    echo "NOTICE: Created empty $PASS_FILE — populate with a strong passphrase before the first backup:"
    echo "  sudo bash -c 'echo \"your-passphrase\" > $PASS_FILE && chmod 0600 $PASS_FILE'"
fi

step "14d/15  rc-url-refresh timer"
# Install the oneshot service + daily timer for RC URL refresh.
for unit in claude-soma-rc-url-refresh.service claude-soma-rc-url-refresh.timer; do
    if [ -f "${SYSTEMD_SRC_DIR}/${unit}" ]; then
        sudo install -m 0644 -o root -g root "${SYSTEMD_SRC_DIR}/${unit}" "/etc/systemd/system/${unit}"
        echo "installed /etc/systemd/system/${unit}"
    else
        echo "WARN: ${unit} not found in ${SYSTEMD_SRC_DIR}; skipping" >&2
    fi
done
if [ -f /etc/systemd/system/claude-soma-rc-url-refresh.timer ]; then
    sudo systemctl daemon-reload
    sudo systemctl enable --now claude-soma-rc-url-refresh.timer
    echo "claude-soma-rc-url-refresh.timer enabled"
fi

step "14e/15  relay-cleanup timer and relay directory"
# Create /var/lib/claude-soma/relay/ for the Caddy file relay.
sudo install -d -m 0755 -o ubuntu -g ubuntu /var/lib/claude-soma/relay
echo "Created /var/lib/claude-soma/relay"

# Write README explaining the relay bundle layout (idempotent).
RELAY_README=/var/lib/claude-soma/relay/README.md
if [ ! -f "$RELAY_README" ]; then
    sudo tee "$RELAY_README" > /dev/null <<'RELAY_README_EOF'
# Relay bundle — /var/lib/claude-soma/relay/

Managed by scripts/soma-relay; cleaned by claude-soma-relay-cleanup.timer.

Layout:
  <lead-name>/      per-lead artifacts (soma-relay publish)
  pub/<12-hex>/     share-link namespace (soma-relay publish --public)
  README.md         this file

Retention: HERMES_RELAY_RETENTION_DAYS (default 7 days).
Pin a directory from cleanup: touch <dir>/.pin
Served at: https://files.mayankgupta.in/ (Caddy basicauth, password in secrets.env)
RELAY_README_EOF
    echo "Created $RELAY_README"
fi

# Install relay-cleanup systemd units (same pattern as 14c/14d).
for unit in claude-soma-relay-cleanup.service claude-soma-relay-cleanup.timer; do
    if [ -f "${SYSTEMD_SRC_DIR}/${unit}" ]; then
        sudo install -m 0644 -o root -g root "${SYSTEMD_SRC_DIR}/${unit}" "/etc/systemd/system/${unit}"
        echo "installed /etc/systemd/system/${unit}"
    else
        echo "WARN: ${unit} not found in ${SYSTEMD_SRC_DIR}; skipping" >&2
    fi
done
if [ -f /etc/systemd/system/claude-soma-relay-cleanup.timer ]; then
    sudo systemctl daemon-reload
    sudo systemctl enable --now claude-soma-relay-cleanup.timer
    echo "claude-soma-relay-cleanup.timer enabled"
fi

step "14f/15  markserv staging dir + claude-soma-markserv.service"
# Create /var/lib/claude-soma/staging/ for markserv-served documents.
sudo install -d -m 0755 -o ubuntu -g ubuntu /var/lib/claude-soma/staging
echo "Created /var/lib/claude-soma/staging"

# Install the markserv long-running service (same loop pattern as 14c/14d/14e).
for unit in claude-soma-markserv.service; do
    if [ -f "${SYSTEMD_SRC_DIR}/${unit}" ]; then
        sudo install -m 0644 -o root -g root "${SYSTEMD_SRC_DIR}/${unit}" "/etc/systemd/system/${unit}"
        echo "installed /etc/systemd/system/${unit}"
    else
        echo "WARN: ${unit} not found in ${SYSTEMD_SRC_DIR}; skipping" >&2
    fi
done
if [ -f /etc/systemd/system/claude-soma-markserv.service ]; then
    sudo systemctl daemon-reload
    sudo systemctl enable claude-soma-markserv.service
    echo "claude-soma-markserv.service enabled (not started — run migrate-staging.sh first)"
fi

step "15/15  DONE  Next steps"
cat <<'NEXT'
1. claude auth login   # one-time browser OAuth for interactive --channels
2. codex login         # one-time browser OAuth for image-gen via ChatGPT
3. Place CLAUDE_CODE_OAUTH_TOKEN in /etc/claude-soma/secrets.env (for systemd)
4. Populate the backup passphrase:
     sudo bash -c 'echo "your-strong-passphrase" > /etc/claude-soma/backup.pass && chmod 0600 /etc/claude-soma/backup.pass'
5. ./scripts/deploy.sh from your dev machine to rsync claude-soma → /opt/claude-soma
6. Install systemd units from systemd/*.service / *.timer (see soma-init wizard or
   docs/CHECKLIST.md "Operational quick reference" section)
7. After DNS + GitHub OAuth app are ready:
     sudo install -m 644 /opt/claude-soma/Caddyfile /etc/caddy/Caddyfile
     sudo systemctl reload caddy
NEXT
