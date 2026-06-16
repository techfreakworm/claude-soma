#!/usr/bin/env bash
# scripts/setup-discord.sh — guided Discord bot setup + operator pairing
#
# Run AFTER bootstrap.sh + after DISCORD_BOT_TOKEN is written to secrets.env.
# What this script does:
#   1. Reads DISCORD_BOT_TOKEN from /etc/claude-soma/secrets.env
#   2. Mirrors the token to ~/.claude/channels/discord/.env (where the channel plugin reads it)
#   3. Verifies the token against the Discord API and prints the bot's username
#   4. Sets SOMA_ACTIVE_CHANNEL=discord in secrets.env (so the channel launches discord)
#   5. Restarts claude-soma-channel.service so it picks up the new channel
#   6. Prints the in-session pairing steps (Discord pairs via the gateway, not a poll)
#
# Discord differs from Telegram: there is no getUpdates long-poll, so the
# operator's numeric "snowflake" is captured IN-SESSION via the plugin's
# /discord:access pair flow, not auto-detected here.

set -euo pipefail

SECRETS_FILE="${SECRETS_FILE:-/etc/claude-soma/secrets.env}"
DISCORD_DIR="${DISCORD_DIR:-/home/ubuntu/.claude/channels/discord}"

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "[setup-discord] $*"; }

# --- Step 1: read the bot token ---
if [[ ! -r "$SECRETS_FILE" ]]; then
    die "Cannot read $SECRETS_FILE. Run bootstrap.sh first, then fill in DISCORD_BOT_TOKEN."
fi

TOKEN="$(grep '^DISCORD_BOT_TOKEN=' "$SECRETS_FILE" 2>/dev/null | cut -d= -f2- || true)"
if [[ -z "$TOKEN" ]]; then
    die "DISCORD_BOT_TOKEN is not set in $SECRETS_FILE. Create a bot at https://discord.com/developers/applications (enable Message Content Intent), Reset Token, and put it in secrets.env."
fi

info "Bot token found."

# --- Step 2: mirror token to channel plugin ---
mkdir -p "$DISCORD_DIR"
printf 'DISCORD_BOT_TOKEN=%s\n' "$TOKEN" > "$DISCORD_DIR/.env"
chmod 600 "$DISCORD_DIR/.env"
info "Token mirrored to $DISCORD_DIR/.env."

# --- Step 3: verify the token + read the bot username (token never printed) ---
BOT_NAME="$(curl -s --max-time 8 -H "Authorization: Bot ${TOKEN}" \
    "https://discord.com/api/v10/users/@me" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('username') or d.get('id') or '')" 2>/dev/null || echo "")"
if [[ -z "$BOT_NAME" ]]; then
    die "Discord API rejected the token (GET /users/@me failed). Re-check DISCORD_BOT_TOKEN (it expires on Reset Token; copy the latest)."
fi
info "Token verified. Bot user: ${BOT_NAME}"

# --- Step 3b: install the discord plugin from the upstream marketplace ---
# A channel plugin only boots if it is INSTALLED; enabling it via --settings is
# not enough (and --plugin-dir alone does not install a channel plugin). Idempotent.
CLAUDE_BIN="${CLAUDE_BIN:-/home/ubuntu/.local/bin/claude}"
if "$CLAUDE_BIN" plugin list 2>/dev/null | grep -q 'discord@claude-plugins-official'; then
    info "discord@claude-plugins-official already installed."
else
    info "Installing discord@claude-plugins-official ..."
    "$CLAUDE_BIN" plugin install discord@claude-plugins-official 2>&1 | tail -3 \
        || die "Failed to install discord@claude-plugins-official (try: $CLAUDE_BIN plugin marketplace update claude-plugins-official, then re-run)."
fi

# --- Step 4: select discord as the active channel ---
if grep -q '^SOMA_ACTIVE_CHANNEL=' "$SECRETS_FILE"; then
    sed -i "s|^SOMA_ACTIVE_CHANNEL=.*|SOMA_ACTIVE_CHANNEL=discord|" "$SECRETS_FILE" 2>/dev/null \
        || sudo sed -i "s|^SOMA_ACTIVE_CHANNEL=.*|SOMA_ACTIVE_CHANNEL=discord|" "$SECRETS_FILE"
else
    printf 'SOMA_ACTIVE_CHANNEL=discord\n' >> "$SECRETS_FILE" 2>/dev/null \
        || printf 'SOMA_ACTIVE_CHANNEL=discord\n' | sudo tee -a "$SECRETS_FILE" > /dev/null
fi
info "SOMA_ACTIVE_CHANNEL=discord set in $SECRETS_FILE."

# --- Step 5: restart channel service (operator-invoked via this script) ---
if systemctl is-active --quiet claude-soma-channel.service 2>/dev/null; then
    sudo systemctl restart claude-soma-channel.service
    info "claude-soma-channel.service restarted (now on the discord channel)."
else
    info "claude-soma-channel.service is not running — start it with:"
    info "  sudo systemctl start claude-soma-channel.service"
fi

# --- Step 6: pairing instructions (in-session) ---
echo
echo "========================================="
echo "  Discord pairing"
echo "========================================="
echo
echo "1. Make sure the bot is in a server you share (DMs to a bot require a"
echo "   shared server — OAuth2 -> URL Generator -> scope 'bot' -> invite it)."
echo "2. DM your bot (${BOT_NAME}) on Discord. It replies with a 6-character"
echo "   pairing code (default policy is 'pairing')."
echo "3. In the bot's Claude Code session, run:"
echo "       /discord:access pair <code>"
echo "4. Lock it down so strangers don't get pairing replies:"
echo "       /discord:access policy allowlist"
echo
echo "If the bot does not respond to your DM, check the channel log:"
echo "  sudo journalctl -u claude-soma-channel -f"
echo "and confirm SOMA_ACTIVE_CHANNEL=discord in $SECRETS_FILE."
