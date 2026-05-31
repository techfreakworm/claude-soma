#!/usr/bin/env bash
# scripts/bluesky-login.sh -- one-shot interactive Bluesky app-password login.
#
# Prompts for a Bluesky handle and app password, validates by calling
# com.atproto.server.createSession, and stores plaintext credentials at
# ~/.claude-pw/bluesky.json (chmod 600).
#
# Compatible with S15 FI-PW encryption migration: pw-encrypt-existing.sh can
# wrap this file later; the plaintext format is intentional for now.
#
# Usage (operator only -- never run by agents):
#   bash scripts/bluesky-login.sh
#
# Env overrides (for testing):
#   BSKY_HOST         -- default: https://bsky.social
#   CLAUDE_PW_DIR     -- default: ~/.claude-pw

set -euo pipefail

BSKY_HOST="${BSKY_HOST:-https://bsky.social}"
PW_DIR="${CLAUDE_PW_DIR:-${HOME}/.claude-pw}"
CREDS_FILE="${PW_DIR}/bluesky.json"
XRPC="${BSKY_HOST}/xrpc"

mkdir -p "${PW_DIR}"
chmod 700 "${PW_DIR}"

echo "[bluesky-login] Bluesky AT Protocol app-password login"
echo "[bluesky-login] Credentials will be stored at: ${CREDS_FILE}"
echo ""
echo "  1. Go to bsky.app -> Settings -> Privacy and Security -> App Passwords"
echo "  2. Create a new app password (not your main account password)."
echo ""

read -rp "Bluesky handle (e.g. yourname.bsky.social): " IDENTIFIER
if [[ -z "${IDENTIFIER}" ]]; then
  echo "[bluesky-login] ERROR: handle must not be empty." >&2
  exit 1
fi

read -rsp "App password (xxxx-xxxx-xxxx-xxxx): " APP_PASSWORD
echo ""
if [[ -z "${APP_PASSWORD}" ]]; then
  echo "[bluesky-login] ERROR: app password must not be empty." >&2
  exit 1
fi

echo "[bluesky-login] Validating credentials against ${XRPC}/com.atproto.server.createSession ..."

RESPONSE=$(curl -sSf \
  -X POST \
  -H "Content-Type: application/json" \
  -d "{\"identifier\": \"${IDENTIFIER}\", \"password\": \"${APP_PASSWORD}\"}" \
  "${XRPC}/com.atproto.server.createSession" 2>&1) || {
    echo "[bluesky-login] ERROR: login failed. Check handle and app password." >&2
    echo "[bluesky-login] Response: ${RESPONSE}" >&2
    exit 1
  }

# Verify the response contains an accessJwt (basic sanity check without jq).
if ! echo "${RESPONSE}" | grep -q '"accessJwt"'; then
  echo "[bluesky-login] ERROR: unexpected response (no accessJwt). Full response:" >&2
  echo "${RESPONSE}" >&2
  exit 1
fi

# Write plaintext credentials. S15 pw-encrypt-existing.sh migrates this later.
python3 - <<PYEOF
import json, os, stat
creds = {"identifier": "${IDENTIFIER}", "app_password": "${APP_PASSWORD}"}
path = "${CREDS_FILE}"
with open(path, "w") as f:
    json.dump(creds, f, indent=2)
    f.write("\n")
os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
print(f"[bluesky-login] Saved credentials to {path} (mode 600)")
PYEOF

echo "[bluesky-login] Login successful. Run scripts/bluesky-post.py to verify posting."
