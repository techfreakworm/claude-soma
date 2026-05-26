#!/usr/bin/env bash
# scripts/vps_bootstrap.sh
#
# Idempotent OS-level bootstrap for a fresh OCI Ubuntu 24.04 ARM VPS:
#   - 8 GB swap (sized for a 4-16 GB RAM box)
#   - iptables ingress for 80/443 INSERTED AT THE RIGHT POSITION
#   - apt: build deps, Python 3.12, ffmpeg, tmux, jq, Node 20, pnpm, Caddy,
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

step "3/9  apt: base packages"
sudo DEBIAN_FRONTEND=noninteractive apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    build-essential git curl wget pkg-config unzip \
    python3.12 python3.12-venv python3.12-dev python3-pip \
    ffmpeg cmake clang tmux jq sqlite3 libsox-dev libssl-dev \
    debian-keyring debian-archive-keyring apt-transport-https \
    rsync ca-certificates gnupg

step "4/9  Node 20 + pnpm"
if ! command -v node >/dev/null 2>&1; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
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

step "13/15  whisper.cpp (voice STT — voice_stt MCP server + ggml-large-v3-turbo model)"
# Git clone + cmake build. .mcp.json's HERMES_WHISPER_BIN expects
# /opt/whisper.cpp/build/bin/whisper-cli and HERMES_WHISPER_MODEL expects
# /opt/whisper.cpp/models/ggml-large-v3-turbo.bin (~1.5 GB download).
if [ ! -x /opt/whisper.cpp/build/bin/whisper-cli ]; then
    sudo install -d -m 755 -o ubuntu -g ubuntu /opt/whisper.cpp
    sudo -u ubuntu git clone https://github.com/ggerganov/whisper.cpp.git /opt/whisper.cpp.tmp
    sudo -u ubuntu cp -a /opt/whisper.cpp.tmp/. /opt/whisper.cpp/
    sudo rm -rf /opt/whisper.cpp.tmp
    sudo -u ubuntu cmake -S /opt/whisper.cpp -B /opt/whisper.cpp/build
    sudo -u ubuntu cmake --build /opt/whisper.cpp/build --config Release -j"$(nproc)"
fi
if [ ! -f /opt/whisper.cpp/models/ggml-large-v3-turbo.bin ]; then
    sudo -u ubuntu bash -c 'cd /opt/whisper.cpp && bash ./models/download-ggml-model.sh large-v3-turbo'
fi
ls -lh /opt/whisper.cpp/build/bin/whisper-cli /opt/whisper.cpp/models/ggml-large-v3-turbo.bin 2>&1 | head -2

step "14/15  pre-create claude-soma directories"
sudo install -d -m 700 -o ubuntu -g ubuntu /etc/claude-soma
sudo install -d -m 755 -o ubuntu -g ubuntu /var/log/claude-soma
sudo install -d -m 755 -o ubuntu -g ubuntu /home/ubuntu/hermes-work
ls -ld /etc/claude-soma /var/log/claude-soma /home/ubuntu/hermes-work

step "15/15  DONE  Next steps"
cat <<'NEXT'
1. claude auth login   # one-time browser OAuth for interactive --channels
2. codex login         # one-time browser OAuth for image-gen via ChatGPT
3. Place CLAUDE_CODE_OAUTH_TOKEN in /etc/claude-soma/secrets.env (for systemd)
4. ./scripts/deploy.sh from your dev machine to rsync claude-soma → /opt/claude-soma
5. Install systemd units from systemd/*.service / *.timer (see soma-init wizard or
   docs/CHECKLIST.md "Operational quick reference" section)
6. After DNS + GitHub OAuth app are ready:
     sudo install -m 644 /opt/claude-soma/Caddyfile /etc/caddy/Caddyfile
     sudo systemctl reload caddy
NEXT
