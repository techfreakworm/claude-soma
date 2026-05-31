#!/usr/bin/env bash
# scripts/caddy-files-render.sh
#
# Renders caddy/files.caddyfile.in into /etc/caddy/conf.d/files.caddyfile
# by substituting the bcrypt hash for HERMES_FILES_PASSWORD.
# Also ensures /etc/caddy/Caddyfile has an import directive for conf.d/*.caddyfile.
# Reloads Caddy gracefully when done.
#
# Run:  bash scripts/caddy-files-render.sh
# Re-run after rotating HERMES_FILES_PASSWORD in /etc/claude-soma/secrets.env.
#
# Do NOT run until the DNS A record for FILES_DOMAIN resolves to the VPS IP.
# The cert will be provisioned by Caddy on the first HTTPS request after DNS propagates.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TEMPLATE="${REPO_ROOT}/caddy/files.caddyfile.in"
SECRETS="${HERMES_SECRETS_FILE:-/etc/claude-soma/secrets.env}"
CONF_D="${HERMES_CADDY_CONF_D:-/etc/caddy/conf.d}"
DEST="${CONF_D}/files.caddyfile"
CADDYFILE="${HERMES_CADDYFILE:-/etc/caddy/Caddyfile}"
IMPORT_LINE="import /etc/caddy/conf.d/*.caddyfile"

if [ ! -f "$TEMPLATE" ]; then
    echo "ERROR: template not found: $TEMPLATE" >&2
    exit 1
fi

if [ ! -f "$SECRETS" ]; then
    echo "ERROR: secrets file not found: $SECRETS" >&2
    exit 1
fi

# Source secrets without printing values
set -a
# shellcheck source=/dev/null
source "$SECRETS"
set +a

if [ -z "${HERMES_FILES_PASSWORD:-}" ]; then
    echo "ERROR: HERMES_FILES_PASSWORD not set in $SECRETS" >&2
    exit 1
fi

FILES_DOMAIN="${FILES_DOMAIN:-files.mayankgupta.in}"

# Generate bcrypt hash via caddy
if ! hash=$(caddy hash-password --plaintext "${HERMES_FILES_PASSWORD}"); then
    echo "ERROR: caddy hash-password failed" >&2
    exit 1
fi

if [ -z "$hash" ]; then
    echo "ERROR: caddy hash-password returned empty output" >&2
    exit 1
fi

# Create conf.d directory
sudo mkdir -p "$CONF_D"

# Render template: substitute __FILES_DOMAIN__ and __BCRYPT_HASH__.
# Using | as sed delimiter because / appears in bcrypt hashes ($2a$14$...).
sed "s|__FILES_DOMAIN__|${FILES_DOMAIN}|g; s|__BCRYPT_HASH__|${hash}|g" "$TEMPLATE" | sudo tee "$DEST" > /dev/null
sudo chmod 644 "$DEST"
sudo chown root:root "$DEST"
echo "Written: $DEST"

# Ensure main Caddyfile has the import directive (idempotent).
# Append after the last line so the global options block stays first.
if ! grep -qF "$IMPORT_LINE" "$CADDYFILE"; then
    printf '\n%s\n' "$IMPORT_LINE" | sudo tee -a "$CADDYFILE" > /dev/null
    echo "Added to $CADDYFILE: $IMPORT_LINE"
else
    echo "Already present in $CADDYFILE: $IMPORT_LINE"
fi

# Graceful reload — no dropped connections
sudo systemctl reload caddy
echo "caddy-files-render: done — ${FILES_DOMAIN} block is now active"
