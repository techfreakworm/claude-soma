#!/usr/bin/env bash
# P8b security headers (NO LLM): CORS behaviour of `opencode serve` for a cross-origin browser
# client, with and without OPENCODE_SERVER_PASSWORD. Checks a preflight (OPTIONS) and a plain GET
# for Origin https://x.opencode.ai, a non-opencode origin, and localhost; records
# Access-Control-Allow-Origin/Credentials + status. Also confirms POST /global/upgrade is listed in
# the OpenAPI (NOT called).
set -u
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P8-serve/cors.sh"
HERE="$(cd "$(dirname "$0")" && pwd)"
OPEN_PORT=$(( ${OCSOMA_PORT_BASE:-47100} + 86 )); AUTH_PORT=$(( ${OCSOMA_PORT_BASE:-47100} + 87 ))
PROBE_ID=P8-cors; SANDBOX_FRESH=1
source "$HERE/../sandbox.sh"
cd "$PROJ" || exit 1
EVD="$OCSOMA_EVID/P8-serve"; mkdir -p "$EVD"

hdr() {  # hdr <base> <origin> <method GET|OPTIONS> [netrc]
  local base="$1" origin="$2" meth="$3" netrc="${4:-}"
  local args=(-s -o /dev/null -D - -H "Origin: $origin")
  [ "$meth" = OPTIONS ] && args+=(-X OPTIONS -H 'Access-Control-Request-Method: POST' -H 'Access-Control-Request-Headers: authorization,content-type')
  [ -n "$netrc" ] && args+=(--netrc-file "$netrc")
  curl "${args[@]}" "$base/global/health" 2>/dev/null \
    | grep -iE '^HTTP/|^access-control-|^www-authenticate|^vary' | sed 's/\r$//'
}

ev_open P8-serve/cors.log "CORS + auth headers of opencode serve for a cross-origin browser client; /global/upgrade listed not called" "none (no LLM)"

ev "## unsecured serve on 127.0.0.1:$OPEN_PORT (no password)"
"$OC" serve --port "$OPEN_PORT" --hostname 127.0.0.1 > "$SANDBOX/open.out" 2>&1 &
OP=$!
for i in $(seq 1 100); do curl -s -o /dev/null "http://127.0.0.1:$OPEN_PORT/global/health" && break; sleep 0.2; done
OPEN="http://127.0.0.1:$OPEN_PORT"
for o in "https://x.opencode.ai" "https://evil.example.com" "http://localhost:5173"; do
  ev "### OPTIONS preflight, Origin $o"; ev_run sh -c ":"; hdr "$OPEN" "$o" OPTIONS >> "$EV_LOG"
  ev "### GET, Origin $o";             hdr "$OPEN" "$o" GET     >> "$EV_LOG"; ev ""
done
kill "$OP" 2>/dev/null; wait "$OP" 2>/dev/null

ev "## password-protected serve on 127.0.0.1:$AUTH_PORT (CORS headers are applied BEFORE auth per the 1.18.x changelog)"
PW=$(python3.12 -I -c 'import secrets;print(secrets.token_hex(12))')
printf 'machine 127.0.0.1 login opencode password %s\n' "$PW" > "$SANDBOX/netrc"; chmod 600 "$SANDBOX/netrc"
OPENCODE_SERVER_PASSWORD="$PW" "$OC" serve --port "$AUTH_PORT" --hostname 127.0.0.1 > "$SANDBOX/auth.out" 2>&1 &
AP=$!
for i in $(seq 1 100); do curl -s -o /dev/null --netrc-file "$SANDBOX/netrc" "http://127.0.0.1:$AUTH_PORT/global/health" && break; sleep 0.2; done
AUTH="http://127.0.0.1:$AUTH_PORT"
for o in "https://x.opencode.ai" "https://evil.example.com"; do
  ev "### OPTIONS preflight (no creds), Origin $o"; hdr "$AUTH" "$o" OPTIONS >> "$EV_LOG"; ev ""
  ev "### GET (no creds), Origin $o";               hdr "$AUTH" "$o" GET     >> "$EV_LOG"; ev ""
done
ev "### GET (with creds, --netrc), Origin https://x.opencode.ai"
hdr "$AUTH" "https://x.opencode.ai" GET "$SANDBOX/netrc" >> "$EV_LOG"; ev ""
kill "$AP" 2>/dev/null; wait "$AP" 2>/dev/null

ev "## /global/upgrade is LISTED in the OpenAPI (NOT called here)"
ev_run sh -c "jq -r '.paths | to_entries[] | select(.key==\"/global/upgrade\") | .key + \": \" + ([.value | keys[]] | join(\",\"))' '$EVD/openapi.json' 2>/dev/null || echo 'openapi.json not present (run P8-serve/run.sh first)'"

ev "## config knob: server.cors (whitelist additional origins); default allows https://*.opencode.ai + localhost"
preflight_ok=$(grep -ciE '^access-control-allow-origin' "$EV_LOG")
sed -i '' "s/$PW/<REDACTED-PW>/g" "$EV_LOG" 2>/dev/null
if [ "$preflight_ok" -ge 1 ]; then
  ev_verdict PASS "serve emits Access-Control-Allow-Origin for opencode.ai/localhost origins (headers captured, both with and without a password; CORS is applied before auth). A rebuild that binds non-loopback must set server.cors tightly; evil.example.com behaviour is in the transcript."
else
  ev_verdict PARTIAL "no Access-Control-Allow-Origin header seen; see raw header dumps above."
fi
ev_redact "$EV_LOG"
tail -1 "$EV_LOG"
