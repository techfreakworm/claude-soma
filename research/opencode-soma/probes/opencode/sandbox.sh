# shellcheck shell=bash
# Source me (do not execute):  PROBE_ID=P1-headless; source research/opencode-soma/probes/opencode/sandbox.sh
#
# Builds an isolated opencode environment so no probe ever reads or writes the operator's real
# opencode/claude config, data, cache or state:
#   HOME, XDG_{CONFIG,DATA,CACHE,STATE}_HOME and TMPDIR all point under $SANDBOX
#   (opencode 1.18.8 resolves data/cache/config/state from XDG_* and its tmp dir from os.tmpdir()).
#   Secret-looking and CLAUDE*/OPENCODE_* env vars are unset, so tools the model runs cannot see them.
#   $XDG_CONFIG_HOME/opencode/opencode.json: autoupdate off, share disabled, ollama-only provider.
#   $PROJ is a throwaway git repo inside the sandbox (stops opencode's upward config/AGENTS walk).
#
# Inputs (all optional): PROBE_ID, SANDBOX, OC_BASEURL (default: direct ollama),
#   OC_DEFAULT_MODEL (default ollama/qwen3:32b), SANDBOX_FRESH=1 (wipe $SANDBOX first).

_SB_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export OCSOMA_REPO_ROOT="$(cd "$_SB_HERE/../../../.." && pwd)"
export OCSOMA_PROBES="$OCSOMA_REPO_ROOT/research/opencode-soma/probes/opencode"
export OCSOMA_EVID="$OCSOMA_REPO_ROOT/research/opencode-soma/evidence/opencode"

# Remember the real TMPDIR once (nested sourcing keeps the first value).
if [ -z "${OCSOMA_REAL_TMPDIR:-}" ]; then
  _t="${TMPDIR:-/tmp}"; export OCSOMA_REAL_TMPDIR="${_t%/}"
fi

: "${PROBE_ID:=adhoc}"
: "${SANDBOX:=$OCSOMA_REAL_TMPDIR/ocsoma-pe/$PROBE_ID}"
export PROBE_ID SANDBOX
export OCSOMA_PROBE_ID="$PROBE_ID"
case "$SANDBOX" in
  "$OCSOMA_REAL_TMPDIR"/ocsoma-pe/*) ;;
  *) echo "sandbox.sh: refusing SANDBOX outside \$TMPDIR/ocsoma-pe: $SANDBOX" >&2; return 1 ;;
esac
if [ "${SANDBOX_FRESH:-0}" = 1 ] && [ -d "$SANDBOX" ]; then rm -rf "$SANDBOX"; fi
mkdir -p "$SANDBOX"/home "$SANDBOX"/xdg/config "$SANDBOX"/xdg/data "$SANDBOX"/xdg/cache \
         "$SANDBOX"/xdg/state "$SANDBOX"/tmp "$SANDBOX"/project

# Scrub the inherited environment (names only matter; values never printed).
for _v in $(compgen -e); do
  case "$_v" in
    CLAUDE*|ANTHROPIC*|OPENAI*|OPENCODE_*|AWS_*|GOOGLE_*|GEMINI*|GITHUB_*|GH_*|AZURE_*|GROQ*|MISTRAL*|\
    *_API_KEY|*_TOKEN|*TOKEN*|*SECRET*|*PASSWORD*|XDG_*|BUN_*|NPM_CONFIG_*|npm_config_*|NODE_OPTIONS|TMUX|TMUX_PANE)
      unset "$_v" ;;
  esac
done
unset _v

export HOME="$SANDBOX/home"
export XDG_CONFIG_HOME="$SANDBOX/xdg/config"
export XDG_DATA_HOME="$SANDBOX/xdg/data"
export XDG_CACHE_HOME="$SANDBOX/xdg/cache"
export XDG_STATE_HOME="$SANDBOX/xdg/state"
export TMPDIR="$SANDBOX/tmp/"
export OPENCODE_DISABLE_AUTOUPDATE=1
export OPENCODE_DISABLE_SHARE=1
export OC=/Users/techfreakworm/.opencode/bin/opencode
export PROJ="$SANDBOX/project"
export TERM="${TERM:-xterm-256color}"
export GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=probe GIT_AUTHOR_EMAIL=probe@example.invalid
export GIT_COMMITTER_NAME=probe GIT_COMMITTER_EMAIL=probe@example.invalid

: "${OC_BASEURL:=http://127.0.0.1:11434/v1}"
: "${OC_DEFAULT_MODEL:=ollama/qwen3:32b}"
export OC_BASEURL OC_DEFAULT_MODEL

# Base global config. Probes add project-level config in $PROJ/opencode.json or $PROJ/.opencode/.
mkdir -p "$XDG_CONFIG_HOME/opencode"
cat > "$XDG_CONFIG_HOME/opencode/opencode.json" <<JSON
{
  "\$schema": "https://opencode.ai/config.json",
  "autoupdate": false,
  "share": "disabled",
  "enabled_providers": ["ollama"],
  "model": "$OC_DEFAULT_MODEL",
  "small_model": "$OC_DEFAULT_MODEL",
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (local)",
      "options": { "baseURL": "$OC_BASEURL" },
      "models": {
        "qwen3:32b": {
          "name": "Qwen3 32B (thinking off)",
          "tool_call": true,
          "reasoning": false,
          "options": { "reasoningEffort": "none" },
          "limit": { "context": 40960, "output": 8192 }
        },
        "lfm2.5-thinking": {
          "name": "LFM2.5 1.2B thinking",
          "tool_call": true,
          "reasoning": true,
          "limit": { "context": 128000, "output": 8192 }
        }
      }
    }
  }
}
JSON

# Throwaway project: its own git root so the upward config/AGENTS.md walk stops here.
if [ ! -d "$PROJ/.git" ]; then
  git -C "$PROJ" init -q -b main
  printf '# sandbox project for %s\n' "$OCSOMA_PROBE_ID" > "$PROJ/README.md"
  git -C "$PROJ" add README.md
  git -C "$PROJ" commit -q -m init
fi

oc_version() { "$OC" --version 2>/dev/null </dev/null; }

# ---------- evidence helpers ----------
# ev_open <relative-log-path> "<what this log shows>" "<model>"
ev_open() {
  EV_LOG="$OCSOMA_EVID/$1"; mkdir -p "$(dirname "$EV_LOG")"
  {
    echo "# $1"
    echo "date-utc: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "probe-script: ${OCSOMA_PROBE_SCRIPT:-unknown}"
    echo "opencode-version: $(oc_version)"
    echo "model: ${3:-n/a}"
    echo "sandbox: \$TMPDIR/ocsoma-pe/$OCSOMA_PROBE_ID (HOME, XDG_*, TMPDIR all inside it)"
    echo "purpose: $2"
    echo
  } > "$EV_LOG"
  export EV_LOG
}
ev() { printf '%s\n' "$*" >> "$EV_LOG"; }
# ev_run <cmd...>: log the exact command, run it, log output, exit code and wall-clock.
ev_run() {
  local t0 t1 rc
  printf '$ %s\n' "$*" >> "$EV_LOG"
  t0=$(python3.12 -I -c 'import time;print(time.time())')
  "$@" >> "$EV_LOG" 2>&1; rc=$?
  t1=$(python3.12 -I -c 'import time;print(time.time())')
  printf '\n[exit=%s wall=%.1fs]\n\n' "$rc" "$(python3.12 -I -c "print($t1-$t0)")" >> "$EV_LOG"
  return $rc
}
ev_verdict() { printf 'VERDICT: %s — %s\n' "$1" "$2" >> "$EV_LOG"; }
# redact token-like strings in a file in place
ev_redact() {
  python3.12 -I - "$@" <<'PY'
import re, sys
pat = re.compile(r'(sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{8,}|xox[a-z]-[A-Za-z0-9-]{8,}|Bearer [A-Za-z0-9._-]{16,}|Basic [A-Za-z0-9+/=]{12,})')
for p in sys.argv[1:]:
    s = open(p, encoding='utf-8', errors='replace').read()
    n = pat.sub('<REDACTED>', s)
    if n != s:
        open(p, 'w', encoding='utf-8').write(n)
PY
}

# ---------- logging proxy helpers ----------
# proxy_start <port> <jsonl-log>: start the ollama logging proxy (127.0.0.1 only) in background.
proxy_start() {
  local port="$1" log="$2"
  mkdir -p "$(dirname "$log")"
  python3.12 -I "$OCSOMA_PROBES/ollama_log_proxy.py" --port "$port" --log "$log" \
      >> "$SANDBOX/proxy-$port.stderr" 2>&1 &
  echo $! > "$SANDBOX/proxy-$port.pid"
  local i
  for i in $(seq 1 50); do
    curl -s "http://127.0.0.1:$port/__proxy_health" >/dev/null 2>&1 && return 0
    sleep 0.1
  done
  echo "proxy on $port did not come up" >&2; return 1
}
proxy_stop() {
  local port="$1"
  [ -f "$SANDBOX/proxy-$port.pid" ] && kill "$(cat "$SANDBOX/proxy-$port.pid")" 2>/dev/null
  rm -f "$SANDBOX/proxy-$port.pid"
}
