#!/usr/bin/env bash
# scripts/smoke_install.sh — post-install verifier for claude-soma
#
# Run on the VPS after scripts/bootstrap.sh completes + secrets.env is populated.
# Reports PASS/FAIL per check + an overall summary at the end.
# Exit code: 0 if all PASS, 1 if any FAIL.

set -uo pipefail

if [[ -t 1 ]]; then
    GREEN="\033[32m"; RED="\033[31m"; YELLOW="\033[33m"; RESET="\033[0m"
else
    GREEN=""; RED=""; YELLOW=""; RESET=""
fi

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

check() {
    local label="$1"
    local cmd="$2"
    if eval "$cmd" >/dev/null 2>&1; then
        printf "${GREEN}[PASS]${RESET} %s\n" "$label"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        printf "${RED}[FAIL]${RESET} %s\n" "$label"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
}

check_optional() {
    local label="$1"
    local cmd="$2"
    if eval "$cmd" >/dev/null 2>&1; then
        printf "${GREEN}[PASS]${RESET} %s\n" "$label"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        printf "${YELLOW}[WARN]${RESET} %s (optional)\n" "$label"
        WARN_COUNT=$((WARN_COUNT + 1))
    fi
}

echo "=== claude-soma smoke install verification ==="

# Section 1: filesystem
check "/opt/claude-soma exists" "test -d /opt/claude-soma"
check "/etc/claude-soma/secrets.env exists + readable" "sudo test -r /etc/claude-soma/secrets.env"
check "/var/log/claude-soma exists" "test -d /var/log/claude-soma"
check "/var/lib/claude-soma/relay exists" "test -d /var/lib/claude-soma/relay"
check "/var/lib/claude-soma/engagement exists" "test -d /var/lib/claude-soma/engagement"
check "/var/lib/claude-soma/engagement/queue.jsonl exists" "test -e /var/lib/claude-soma/engagement/queue.jsonl"

# Section 2: services active
check "claude-soma-api.service active" "systemctl is-active claude-soma-api.service"
check "claude-soma-frontend.service active" "systemctl is-active claude-soma-frontend.service"
check "claude-soma-markserv.service active" "systemctl is-active claude-soma-markserv.service"
check "claude-soma-channel.service active" "systemctl is-active claude-soma-channel.service"
check "caddy.service active" "systemctl is-active caddy.service"

# Section 3: timers enabled (each one)
for timer in healthcheck cache-refresh secrets-backup pw-refresh usage-snapshot rc-url-refresh idle-reaper daily-status listener-healthcheck engagement-drip channel-clear relay-cleanup; do
    check "claude-soma-$timer.timer enabled" "systemctl is-enabled claude-soma-$timer.timer"
done

# Section 4: ports listening
check "api port 9000 listening" "ss -lntp 2>/dev/null | grep -q ':9000 '"
check "frontend port 3000 listening" "ss -lntp 2>/dev/null | grep -q ':3000 '"
check "markserv port 18081 listening" "ss -lntp 2>/dev/null | grep -q ':18081 '"
check "hermes-api listener port 9100 listening" "ss -lntp 2>/dev/null | grep -q ':9100 '"
check "caddy port 443 listening" "ss -lntp 2>/dev/null | grep -q ':443 '"
check "caddy port 80 listening" "ss -lntp 2>/dev/null | grep -q ':80 '"

# Section 5: HTTP responses (local)
check "/api/healthz returns 200" "curl -sf -o /dev/null -w '%{http_code}' http://127.0.0.1:9000/api/healthz | grep -q 200"
check "hermes-api /health returns 200" "curl -sf http://127.0.0.1:9100/health | grep -q '\"status\": *\"ok\"'"
check "markserv returns 200 on /" "curl -sf -o /dev/null -w '%{http_code}' http://127.0.0.1:18081/ | grep -q 200"
check "frontend returns 200 or 307 on /" "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/ | grep -qE '200|307'"

# Section 6: external CLI binaries (optional)
check_optional "claude CLI installed" "command -v claude"
check_optional "grok CLI installed" "command -v grok"
check_optional "codex CLI installed" "command -v codex"
check_optional "hf CLI installed" "command -v hf"
check_optional "markserv CLI installed" "command -v markserv"

# Section 7: Python venv
check "venv exists" "test -x /opt/claude-soma/.venv/bin/python"
check "claude_soma package importable" "/opt/claude-soma/.venv/bin/python -c 'import claude_soma' 2>/dev/null"

# Section 8: secrets keys (KEY existence only — NEVER echo values)
for key in CLAUDE_CODE_OAUTH_TOKEN AUTH_GITHUB_CLIENT_ID AUTH_GITHUB_CLIENT_SECRET NEXTAUTH_SECRET TELEGRAM_BOT_TOKEN HERMES_NOTIFY_CHAT_ID HERMES_FILES_PASSWORD; do
    check "$key set in secrets.env" "sudo grep -q '^$key=.\+' /etc/claude-soma/secrets.env"
done

# Section 9: public reachability (optional — depends on DNS config + cloud config)
check_optional "soma.<domain> reaches via Caddy (port 443)" "curl -sI -o /dev/null -w '%{http_code}' --max-time 5 https://soma.mayankgupta.in/ | grep -qE '200|307|401'"
check_optional "files.<domain> reaches via Caddy (port 443) — basicauth challenge" "curl -sI -o /dev/null -w '%{http_code}' --max-time 5 https://files.mayankgupta.in/ | grep -qE '401'"

# Summary
echo
echo "=== Summary ==="
printf "${GREEN}PASS: %d${RESET}\n" "$PASS_COUNT"
printf "${RED}FAIL: %d${RESET}\n" "$FAIL_COUNT"
if [[ "$WARN_COUNT" -gt 0 ]]; then
    printf "${YELLOW}WARN: %d (optional)${RESET}\n" "$WARN_COUNT"
fi

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    echo
    echo "Some checks FAILED. Review the output above + INSTALL.md troubleshooting section."
    exit 1
fi

echo
echo "All required checks passed."
exit 0
