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

step "9/9  pre-create claude-soma directories"
sudo install -d -m 700 -o ubuntu -g ubuntu /etc/claude-soma
sudo install -d -m 755 -o ubuntu -g ubuntu /var/log/claude-soma
sudo install -d -m 755 -o ubuntu -g ubuntu /home/ubuntu/hermes-work
ls -ld /etc/claude-soma /var/log/claude-soma /home/ubuntu/hermes-work

step "DONE  Next steps"
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
