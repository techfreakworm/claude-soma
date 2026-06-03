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

SECRETS=/etc/claude-soma/secrets.env

if [[ ! -r "$SECRETS" ]]; then
    echo "ERROR: $SECRETS not readable. Run as root (sudo bash finalize-caddy.sh)." >&2
    exit 1
fi

# shellcheck disable=SC1090
source <(grep -E '^[A-Z_]+=' "$SECRETS")

: "${SOMA_DOMAIN:?SOMA_DOMAIN must be set in $SECRETS}"
: "${HERMES_FILES_PASSWORD:?HERMES_FILES_PASSWORD must be set in $SECRETS}"

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

# Render the main Caddyfile: replace hardcoded soma.mayankgupta.in with real domain.
sed -e "s|soma\.mayankgupta\.in|soma.${SOMA_DOMAIN_BASE}|g" \
    "$REPO_ROOT/Caddyfile" > "$TMP_MAIN"

# Render files.caddyfile: replace hardcoded domain + bcrypt hash.
# The sed pattern matches any $2a$14$ bcrypt hash (53 chars of ./[A-Za-z0-9]).
# The replacement uses mixed quoting so HASH expands via shell but the pattern
# stays in single quotes (preventing shell expansion of the $2a$14$ in the pattern).
sed -e "s|files\.mayankgupta\.in|${FILES_DOMAIN}|g" \
    -e 's|\$2a\$14\$[A-Za-z0-9./]\{53\}|'"${HASH}"'|g' \
    "$REPO_ROOT/caddy/files.caddyfile" > "$TMP_FILES"

# Install rendered configs.
install -m 644 "$TMP_MAIN" /etc/caddy/Caddyfile
install -d -m 755 /etc/caddy/conf.d
install -m 644 "$TMP_FILES" /etc/caddy/conf.d/files.caddyfile

echo "Validating Caddy config..."
caddy validate --config /etc/caddy/Caddyfile

echo "Reloading Caddy..."
systemctl reload caddy.service
echo "Caddy reloaded with site configs."

cat <<NEXT

Caddy is now configured for:
  https://soma.${SOMA_DOMAIN_BASE}
  https://${FILES_DOMAIN}

If DNS A records are not set yet, run:
  bash ${REPO_ROOT}/scripts/show-dns-setup.sh

Smoke verify the whole install:
  sudo bash ${REPO_ROOT}/scripts/smoke_install.sh

NEXT
