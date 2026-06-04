#!/usr/bin/env bash
# scripts/finalize-caddy.sh — render site configs + reload Caddy after secrets are set
#
# Run AFTER bootstrap.sh completes AND /etc/claude-soma/secrets.env has
# SOMA_DOMAIN, HERMES_FILES_PASSWORD (and optionally FILES_DOMAIN) filled in.
#
# Usage: sudo bash /opt/claude-soma/scripts/finalize-caddy.sh
#
# What it does:
#   1. Sources secrets from /etc/claude-soma/secrets.env
#   2. Generates a bcrypt hash for HERMES_FILES_PASSWORD
#   3. Renders /etc/caddy/Caddyfile with the real soma.<domain>
#   4. Renders /etc/caddy/conf.d/files.caddyfile with the real files.<domain> + hash
#   5. Validates and reloads Caddy

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib-friendly.sh
source "$SCRIPT_DIR/lib-friendly.sh"

SECRETS=/etc/claude-soma/secrets.env

if [[ ! -r "$SECRETS" ]]; then
    friendly_halt "Secrets file not found or not readable" \
"$(cat <<MSG
$SECRETS is not readable.

This script must be run as root:
  sudo bash /opt/claude-soma/scripts/finalize-caddy.sh

If the file does not exist yet, create it first:
  sudo cp /opt/claude-soma/secrets.env.example $SECRETS
  sudo chmod 600 $SECRETS
  sudo chown ubuntu:ubuntu $SECRETS
  sudo nano $SECRETS   # fill in at minimum: SOMA_DOMAIN + HERMES_FILES_PASSWORD
MSG
)"
fi

# shellcheck disable=SC1090
source <(grep -E '^[A-Z_]+=' "$SECRETS")

if [[ -z "${SOMA_DOMAIN:-}" ]]; then
    friendly_halt "Missing required secret: SOMA_DOMAIN" \
"$(cat <<MSG
SOMA_DOMAIN is not set in $SECRETS.

Add this line to the file and re-run:
  SOMA_DOMAIN=<your-domain>   # e.g. example.com (without the soma. prefix)

Edit with:  sudo nano $SECRETS
Then re-run: sudo bash /opt/claude-soma/scripts/finalize-caddy.sh
MSG
)"
fi

if [[ -z "${HERMES_FILES_PASSWORD:-}" ]]; then
    friendly_halt "Missing required secret: HERMES_FILES_PASSWORD" \
"$(cat <<MSG
HERMES_FILES_PASSWORD is not set in $SECRETS.

Add a strong password for the files relay basicauth:
  HERMES_FILES_PASSWORD=<your-strong-password>

Edit with:  sudo nano $SECRETS
Then re-run: sudo bash /opt/claude-soma/scripts/finalize-caddy.sh
MSG
)"
fi

# Strip a leading "soma." prefix if the user accidentally included it.
SOMA_DOMAIN_BASE="${SOMA_DOMAIN#soma.}"
FILES_DOMAIN="${FILES_DOMAIN:-files.${SOMA_DOMAIN_BASE}}"

echo "Rendering site configs for:"
echo "  soma.${SOMA_DOMAIN_BASE}"
echo "  ${FILES_DOMAIN}"

HASH=$(caddy hash-password --plaintext "$HERMES_FILES_PASSWORD")

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

TMP_MAIN=$(mktemp)
TMP_FILES=$(mktemp)
# shellcheck disable=SC2064
trap "rm -f '$TMP_MAIN' '$TMP_FILES'" EXIT

# Render the main Caddyfile: substitute __SOMA_DOMAIN__ + __ACME_EMAIL__.
# Repo template uses placeholders so a fresh clone never ships with someone
# else's domain in it (and so the installed config is unambiguous about what
# was substituted vs left literal).
# The fallback to the old literal `soma.mayankgupta.in` / `mayank@…` keeps
# this script usable against pre-FI-DOMAIN-PLACEHOLDER checkouts.
ACME_EMAIL="${ACME_EMAIL:-soma-acme@${SOMA_DOMAIN_BASE}}"
sed -e "s|__SOMA_DOMAIN__|soma.${SOMA_DOMAIN_BASE}|g" \
    -e "s|__ACME_EMAIL__|${ACME_EMAIL}|g" \
    -e "s|soma\.mayankgupta\.in|soma.${SOMA_DOMAIN_BASE}|g" \
    -e "s|mayank@mayankgupta\.in|${ACME_EMAIL}|g" \
    "$REPO_ROOT/Caddyfile" > "$TMP_MAIN"

# Render files.caddyfile: substitute __FILES_DOMAIN__ + __BCRYPT_HASH__.
# Same fallback story for older checkouts that still contain the literal
# bcrypt hash + domain (regex matches any $2a$14$ hash).
sed -e "s|__FILES_DOMAIN__|${FILES_DOMAIN}|g" \
    -e "s|__BCRYPT_HASH__|${HASH}|g" \
    -e "s|files\.mayankgupta\.in|${FILES_DOMAIN}|g" \
    -e 's|\$2a\$14\$[A-Za-z0-9./]\{53\}|'"${HASH}"'|g' \
    "$REPO_ROOT/caddy/files.caddyfile" > "$TMP_FILES"

# Install rendered configs.
install -m 644 "$TMP_MAIN" /etc/caddy/Caddyfile
install -d -m 755 /etc/caddy/conf.d
install -m 644 "$TMP_FILES" /etc/caddy/conf.d/files.caddyfile

# Bug #5: caddy runs as the `caddy` user; access logs go to /var/log/caddy.
# Ensure the directory exists and is owned by caddy before caddy starts/reloads.
sudo mkdir -p /var/log/caddy
sudo chown -R caddy:caddy /var/log/caddy
sudo chmod 755 /var/log/caddy

echo "Validating Caddy config..."
if ! caddy validate --config /etc/caddy/Caddyfile; then
    friendly_halt "Caddy configuration validation failed" \
"$(cat <<MSG
caddy validate --config /etc/caddy/Caddyfile reported errors (see above).

Common causes:
  1. Malformed bcrypt hash — re-run this script to regenerate it:
       sudo bash /opt/claude-soma/scripts/finalize-caddy.sh
  2. Missing secrets — ensure SOMA_DOMAIN and HERMES_FILES_PASSWORD are set
  3. Template rendering issue — inspect the rendered file:
       cat /etc/caddy/Caddyfile
       cat /etc/caddy/conf.d/files.caddyfile

Fix the issue and re-run:
  sudo bash /opt/claude-soma/scripts/finalize-caddy.sh
MSG
)"
fi

# Bug #4: caddy.service may be enabled but not yet ACTIVE (bootstrap step 13
# did not start it because no site configs existed yet). Detect + start if needed.
if sudo systemctl is-active --quiet caddy.service; then
    if ! sudo systemctl reload caddy.service; then
        friendly_halt "Caddy reload failed" \
"$(cat <<MSG
systemctl reload caddy.service failed even though caddy.service is active.

Check Caddy's status and logs:
  sudo systemctl status caddy.service
  sudo journalctl -u caddy.service -n 50

Then re-run:
  sudo bash /opt/claude-soma/scripts/finalize-caddy.sh
MSG
)"
    fi
    echo "Caddy reloaded with site configs."
else
    if ! sudo systemctl enable --now caddy.service; then
        friendly_halt "Caddy failed to start" \
"$(cat <<MSG
systemctl enable --now caddy.service failed.

Check Caddy's status and logs:
  sudo systemctl status caddy.service
  sudo journalctl -u caddy.service -n 50

Common causes:
  1. /var/log/caddy not owned by caddy — should be fixed by this script, but verify:
       ls -la /var/log/caddy
  2. Port 80/443 already in use — check: sudo ss -tlnp | grep -E ':80|:443'
  3. Bad config (should have been caught by validate above)

Then re-run:
  sudo bash /opt/claude-soma/scripts/finalize-caddy.sh
MSG
)"
    fi
    echo "Caddy started with site configs."
fi

cat <<NEXT

Caddy is now configured for:
  https://soma.${SOMA_DOMAIN_BASE}
  https://${FILES_DOMAIN}

If DNS A records are not set yet, run:
  bash ${REPO_ROOT}/scripts/show-dns-setup.sh

Smoke verify the whole install:
  sudo bash ${REPO_ROOT}/scripts/smoke_install.sh

NEXT
