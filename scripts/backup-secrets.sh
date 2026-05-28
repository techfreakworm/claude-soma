#!/usr/bin/env bash
# scripts/backup-secrets.sh
#
# GPG-symmetric encrypted backup of /etc/claude-soma/secrets.env and the
# Telegram bot-token .env.  Run daily by claude-soma-secrets-backup.timer.
#
# Passphrase source: /etc/claude-soma/backup.pass (mode 0600, root-owned).
# Bootstrap creates an empty placeholder; the operator must populate it:
#   sudo bash -c 'echo "your-strong-passphrase" > /etc/claude-soma/backup.pass \
#                 && chmod 0600 /etc/claude-soma/backup.pass'
#
# Backup destination: $SECRETS_BACKUP_DIR (default /home/ubuntu/secrets-backups)
# Retention: keep last $SECRETS_BACKUP_KEEP backups per source (default 14).
#
# To rotate the Telegram bot token after a leak or expiry:
#   1. DM @BotFather → /revoke → choose your bot → copy the new token.
#   2. Update the live secrets:
#        sudo sed -i "s/^TELEGRAM_BOT_TOKEN=.*/TELEGRAM_BOT_TOKEN=<new>/" \
#             /etc/claude-soma/secrets.env
#        echo "TELEGRAM_BOT_TOKEN=<new>" \
#             > /home/ubuntu/.claude/channels/telegram/.env
#   3. Restart the channel:
#        sudo systemctl restart claude-soma-channel.service
#   4. Run this script manually to take a fresh backup:
#        /opt/claude-soma/scripts/backup-secrets.sh

set -euo pipefail

SECRETS_BACKUP_DIR="${SECRETS_BACKUP_DIR:-/home/ubuntu/secrets-backups}"
SECRETS_BACKUP_KEEP="${SECRETS_BACKUP_KEEP:-14}"
PASS_FILE="${PASS_FILE:-/etc/claude-soma/backup.pass}"
MAIN_SECRETS="/etc/claude-soma/secrets.env"
TG_ENV="/home/ubuntu/.claude/channels/telegram/.env"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

# Ensure destination dir exists with tight permissions.
install -d -m 700 -o ubuntu -g ubuntu "$SECRETS_BACKUP_DIR"

# Validate passphrase file.
if [ ! -f "$PASS_FILE" ]; then
    echo "ERROR: passphrase file $PASS_FILE does not exist." >&2
    echo "       Create it with: sudo bash -c 'echo your-passphrase > $PASS_FILE && chmod 0600 $PASS_FILE'" >&2
    exit 1
fi
if [ ! -s "$PASS_FILE" ]; then
    echo "ERROR: passphrase file $PASS_FILE is empty — populate it before running." >&2
    exit 1
fi

backup_file() {
    local src="$1"
    local label="$2"       # used in destination filename, e.g. "secrets-env"
    if [ ! -f "$src" ]; then
        echo "SKIP: $src not found"
        return 0
    fi
    local dest="${SECRETS_BACKUP_DIR}/${label}-${TS}.gpg"
    gpg --batch --yes --passphrase-file "$PASS_FILE" \
        --symmetric --cipher-algo AES256 \
        --output "$dest" "$src"
    chmod 600 "$dest"
    echo "backed up: $src -> $dest"

    # Retention: keep the newest $SECRETS_BACKUP_KEEP backups for this label.
    local old_count
    old_count=$(ls -1t "${SECRETS_BACKUP_DIR}/${label}"-*.gpg 2>/dev/null | wc -l)
    if [ "$old_count" -gt "$SECRETS_BACKUP_KEEP" ]; then
        ls -1t "${SECRETS_BACKUP_DIR}/${label}"-*.gpg \
            | tail -n +"$((SECRETS_BACKUP_KEEP + 1))" \
            | xargs -r rm --
        echo "pruned old ${label} backups (kept ${SECRETS_BACKUP_KEEP})"
    fi
}

backup_file "$MAIN_SECRETS" "secrets-env"
backup_file "$TG_ENV"       "telegram-env"

echo "backup complete at $TS"
