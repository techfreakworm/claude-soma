#!/usr/bin/env bash
# scripts/setup-telegram.sh — guided Telegram bot setup + operator pairing
#
# Run AFTER bootstrap.sh + after TELEGRAM_BOT_TOKEN is written to secrets.env.
# What this script does:
#   1. Reads TELEGRAM_BOT_TOKEN from /etc/claude-soma/secrets.env
#   2. Mirrors the token to ~/.claude/channels/telegram/.env (where the channel plugin reads it)
#   3. Prompts the operator to send a message to the bot from Telegram
#   4. Polls getUpdates to auto-detect the operator's numeric chat ID
#   5. Writes the chat ID to ~/.claude/channels/telegram/access.json (plugin allowlist)
#   6. Writes HERMES_NOTIFY_CHAT_ID and TELEGRAM_CHAT_ID back to secrets.env
#   7. Restarts claude-soma-channel.service so it picks up the new config

set -euo pipefail

SECRETS_FILE="${SECRETS_FILE:-/etc/claude-soma/secrets.env}"
TELEGRAM_DIR="${TELEGRAM_DIR:-/home/ubuntu/.claude/channels/telegram}"
TIMEOUT="${PAIR_TIMEOUT:-120}"

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "[setup-telegram] $*"; }

# --- Step 1: read the bot token ---
if [[ ! -r "$SECRETS_FILE" ]]; then
    die "Cannot read $SECRETS_FILE. Run bootstrap.sh first, then fill in TELEGRAM_BOT_TOKEN."
fi

TOKEN="$(grep '^TELEGRAM_BOT_TOKEN=' "$SECRETS_FILE" 2>/dev/null | cut -d= -f2-)"
if [[ -z "$TOKEN" ]]; then
    die "TELEGRAM_BOT_TOKEN is not set in $SECRETS_FILE. Fill it in first (use @BotFather to create a bot)."
fi

info "Bot token found."

# --- Step 2: mirror token to channel plugin ---
mkdir -p "$TELEGRAM_DIR"
printf 'TELEGRAM_BOT_TOKEN=%s\n' "$TOKEN" > "$TELEGRAM_DIR/.env"
chmod 600 "$TELEGRAM_DIR/.env"
info "Token mirrored to $TELEGRAM_DIR/.env."

# --- Step 3: prompt operator to DM the bot ---
BOT_NAME="$(curl -s --max-time 5 "https://api.telegram.org/bot${TOKEN}/getMe" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('result',{}).get('username','your-bot'))" 2>/dev/null || echo "your-bot")"

echo
echo "========================================="
echo "  Telegram pairing"
echo "========================================="
echo
echo "Open Telegram and send any message to @${BOT_NAME} now."
echo "Waiting up to ${TIMEOUT} seconds for a message..."
echo

# --- Step 4: poll getUpdates for a message ---
DEADLINE=$(( $(date +%s) + TIMEOUT ))
CHAT_ID=""
while [[ $(date +%s) -lt $DEADLINE ]]; do
    UPDATES="$(curl -s --max-time 5 "https://api.telegram.org/bot${TOKEN}/getUpdates?timeout=10&limit=5" 2>/dev/null || echo "{}")"
    CHAT_ID="$(echo "$UPDATES" | python3 -c "
import json, sys
data = json.load(sys.stdin)
results = data.get('result', [])
for r in reversed(results):
    msg = r.get('message', {})
    chat = msg.get('chat', {})
    cid = chat.get('id')
    if cid:
        print(cid)
        break
" 2>/dev/null || echo "")"
    if [[ -n "$CHAT_ID" ]]; then
        break
    fi
done

if [[ -z "$CHAT_ID" ]]; then
    die "No message received within ${TIMEOUT}s. Make sure you sent a message to @${BOT_NAME} and that the token is correct."
fi

info "Detected chat ID: $CHAT_ID"

# --- Step 5: write access.json allowlist ---
cat > "$TELEGRAM_DIR/access.json" <<ACLJSON
{"dmPolicy":"allowlist","allowFrom":["${CHAT_ID}"],"groups":{},"pending":{}}
ACLJSON
info "access.json updated — only chat ID $CHAT_ID is allowed."

# --- Step 6: write chat ID back to secrets.env ---
if grep -q '^HERMES_NOTIFY_CHAT_ID=' "$SECRETS_FILE"; then
    sudo sed -i "s|^HERMES_NOTIFY_CHAT_ID=.*|HERMES_NOTIFY_CHAT_ID=${CHAT_ID}|" "$SECRETS_FILE"
else
    echo "HERMES_NOTIFY_CHAT_ID=${CHAT_ID}" | sudo tee -a "$SECRETS_FILE" > /dev/null
fi
if grep -q '^TELEGRAM_CHAT_ID=' "$SECRETS_FILE"; then
    sudo sed -i "s|^TELEGRAM_CHAT_ID=.*|TELEGRAM_CHAT_ID=${CHAT_ID}|" "$SECRETS_FILE"
else
    echo "TELEGRAM_CHAT_ID=${CHAT_ID}" | sudo tee -a "$SECRETS_FILE" > /dev/null
fi
info "HERMES_NOTIFY_CHAT_ID and TELEGRAM_CHAT_ID written to $SECRETS_FILE."

# --- Step 7: restart channel service ---
if systemctl is-active --quiet claude-soma-channel.service 2>/dev/null; then
    sudo systemctl restart claude-soma-channel.service
    info "claude-soma-channel.service restarted."
else
    info "claude-soma-channel.service is not running — skipping restart. Start it with:"
    info "  sudo systemctl start claude-soma-channel.service"
fi

echo
echo "========================================="
echo "  Pairing complete!"
echo "========================================="
echo "  Chat ID : $CHAT_ID"
echo "  Bot     : @${BOT_NAME}"
echo
echo "Send a message to @${BOT_NAME} to verify the bot responds."
echo "If it does not, check the channel log:"
echo "  sudo journalctl -u claude-soma-channel -f"
