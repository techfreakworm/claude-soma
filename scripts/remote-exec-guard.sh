#!/usr/bin/env bash
# scripts/remote-exec-guard.sh
#
# SSH ForcedCommand guard for the A->B control channel (multi-VPS orchestration).
# Installed as authorized_keys command="..." for the soma-orchestrator key on B.
# It is the ONLY thing that constrains what A can do on B, because the spawn
# sudoers grant (`systemd-run *`) is arbitrary-root. See docs multi-vps plan §e.
#
# Contract (what A / RemoteRunner sends as $SSH_ORIGINAL_COMMAND):
#   spawn  <name> <mode> <uuid> <tier> <b64brief>
#   resume <name> <mode> <uuid> <tier> <b64prompt>
#   kill   <name>
#   capture <name>
#   list   <name>
#   has-session <name>
#   stat-transcript <name>
# where <name> matches NAME_RX, <mode> is in MODE_ALLOW, <tier> is in TIER_ALLOW,
# <uuid> is a UUID, and <b64*> is standard base64 of the prompt. Everything else
# is DENIED.
#
# Principle: never eval/sh -c A's input; validate leaf tokens, then BUILD the
# real argv array here with User=ubuntu + unit=claude-soma-lead-<name> fixed.

set -u
IFS=$' \t\n'
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
umask 077

# --- config (audit these) ---
A_TAILNET_IP="100.103.37.115"
NOTIFY_URL="http://${A_TAILNET_IP}:9100/notify"
LEAD_PATH="/opt/claude-soma/.venv/bin:/home/ubuntu/.local/bin:/home/ubuntu/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin"
LEAD_PROJECTS_BASE="/home/ubuntu/projects"
LOG_DIR="/var/log/claude-soma"
GUARD_LOG="${LOG_DIR}/remote-exec-guard.log"
LEAD_MCP_B="/opt/claude-soma/config/claude/lead-mcp-b.json"   # optional; omitted if absent
CLAUDE_SAFE="/usr/local/bin/claude-safe"
SUDO=/usr/bin/sudo; SYSTEMD_RUN=/usr/bin/systemd-run; SYSTEMCTL=/usr/bin/systemctl
TMUX=/usr/bin/tmux; BASE64=/usr/bin/base64

# Per-tier memory caps. The guard OWNS these (A cannot pass --property=...): A
# sends only a tier label and the guard maps it to MemoryMax/MemoryHigh here.
# MUST stay in sync with config/claude/hosts.json tier_caps on A (admission rule).
declare -A TIER_MAX=( [critical]=6000 [standard]=3000 )
declare -A TIER_HIGH=( [critical]=5000 [standard]=2500 )

# --- validation constants ---
NAME_RX='^[a-z][a-z0-9-]{0,63}$'
UUID_RX='^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
B64_RX='^[A-Za-z0-9+/=]+$'
CHARSET_RX='^[A-Za-z0-9 +/=._-]+$'   # NOTE: no ; & | < > ` $ ( ) ' " backslash newline
MODE_ALLOW=" acceptEdits plan default bypassPermissions "
TIER_ALLOW=" critical standard "
MAX_CMD_LEN=300000
MAX_B64_LEN=200000

_ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
_client() { printf '%s' "${SSH_CONNECTION%% *}"; }   # client IP, best-effort

# logged BEFORE any exec (exec replaces this process)
log_line() { # decision verb name reason brieflen
  mkdir -p "$LOG_DIR" 2>/dev/null || true
  printf '%s decision=%s verb=%s name=%s client=%s brieflen=%s reason=%q\n' \
    "$(_ts)" "$1" "${2:-}" "${3:-}" "$(_client)" "${5:-0}" "${4:-}" >> "$GUARD_LOG" 2>/dev/null || true
}
deny() { # reason [verb] [name]
  log_line DENY "${2:-}" "${3:-}" "$1" 0
  printf 'remote-exec-guard: DENY %s\n' "$1" >&2
  exit 99
}

CMD="${SSH_ORIGINAL_COMMAND-}"

# 1. no interactive shell
[ -n "$CMD" ] || deny "empty command (no interactive shell)"
# 2. no embedded newline (read -ra would silently drop the tail). NOTE: do NOT
#    test *$'\0'* — bash strips NUL so $'\0' is the empty string and *$'\0'*
#    degenerates to ** which matches EVERY command (would deny-all). A NUL can't
#    survive in a bash var anyway; the charset allowlist (step 4) rejects it too.
case "$CMD" in *$'\n'*) deny "newline in command" ;; esac
# 3. length cap
[ "${#CMD}" -le "$MAX_CMD_LEN" ] || deny "command too long (${#CMD})"
# 4. charset allowlist — kills ; & | < > ` $ ( ) quotes backslash up front
[[ "$CMD" =~ $CHARSET_RX ]] || deny "illegal character in command"
# 5. tokenize (safe: all tokens are whitespace-free; brief is base64)
read -r -a P <<< "$CMD"
VERB="${P[0]-}"
NAME="${P[1]-}"

# 6. defense-in-depth: forbid escalation substrings in the NON-brief args
#    (regex on each arg already prevents these; this satisfies the explicit
#    requirement and guards against a future contract change). Window :0:5 =
#    verb,name,mode,uuid,tier — deliberately EXCLUDES P[5] (the base64 brief),
#    whose alphabet [A-Za-z0-9+/=] can legitimately contain "User=root".
for tok in "${P[@]:0:5}"; do
  case "$tok" in
    *User=root*|*ExecStartPre*|*PermissionsStartOnly*|*--property*)
      deny "forbidden token: $tok" "$VERB" "$NAME" ;;
  esac
done

valid_name() { [[ "$1" =~ $NAME_RX ]]; }

case "$VERB" in
  spawn)
    [ "${#P[@]}" -eq 6 ] || deny "spawn needs 5 args" spawn "$NAME"
    MODE="${P[2]}"; UUID="${P[3]}"; TIER="${P[4]}"; B64="${P[5]}"
    valid_name "$NAME"            || deny "bad name" spawn "$NAME"
    [[ "$MODE_ALLOW" == *" $MODE "* ]] || deny "bad permission-mode" spawn "$NAME"
    [[ "$TIER_ALLOW" == *" $TIER "* ]] || deny "bad tier" spawn "$NAME"
    [[ "$UUID" =~ $UUID_RX ]]     || deny "bad session uuid" spawn "$NAME"
    [[ "$B64" =~ $B64_RX ]]       || deny "brief not base64" spawn "$NAME"
    [ "${#B64}" -le "$MAX_B64_LEN" ] || deny "brief too long" spawn "$NAME"
    BRIEF="$(printf '%s' "$B64" | "$BASE64" -d 2>/dev/null)" || deny "brief decode failed" spawn "$NAME"
    [ -n "$BRIEF" ]              || deny "empty brief" spawn "$NAME"

    UNIT="claude-soma-lead-${NAME}.service"
    SOCK="soma-lead-${NAME}"
    SESS="soma-proj-${NAME}"
    CWD="${LEAD_PROJECTS_BASE}/${NAME}"
    LOG="${LOG_DIR}/${NAME}.log"

    mkdir -p "$CWD" "$LOG_DIR" 2>/dev/null || true
    # pretrust cwd (mirror spawner._pretrust_cwd) so the detached lead never
    # hangs on the trust dialog.
    /usr/bin/python3 - "$CWD" <<'PY' 2>/dev/null || true
import json,os,sys,tempfile,pathlib
cwd=sys.argv[1]; p=pathlib.Path.home()/".claude.json"
try: d=json.loads(p.read_text()) if p.exists() else {}
except Exception: d={}
e=d.setdefault("projects",{}).setdefault(cwd,{})
e["hasTrustDialogAccepted"]=True; e.setdefault("projectOnboardingSeenCount",0)
fd,t=tempfile.mkstemp(dir=str(p.parent),prefix=".claude.json.",suffix=".tmp")
import os as _o
with _o.fdopen(fd,"w") as f: json.dump(d,f,indent=2); f.flush(); _o.fsync(f.fileno())
_o.chmod(t,0o600); _o.replace(t,p)
PY

    claude_argv=( "$CLAUDE_SAFE"
      --session-id "$UUID" --remote-control "$SESS" --add-dir "$CWD"
      --permission-mode "$MODE" --dangerously-skip-permissions
      --effort max --setting-sources user,project,local )
    [ -f "$LEAD_MCP_B" ] && claude_argv+=( --mcp-config "$LEAD_MCP_B" )
    claude_argv+=( -- "$BRIEF" )

    cmd=( "$SUDO" -n "$SYSTEMD_RUN" --collect --quiet "--unit=${UNIT}"
      --property=Type=oneshot --property=RemainAfterExit=yes
      --property=User=ubuntu --property=Group=ubuntu
      --property=OOMScoreAdjust=800
      "--property=MemoryMax=${TIER_MAX[$TIER]}M" "--property=MemoryHigh=${TIER_HIGH[$TIER]}M"
      --property=EnvironmentFile=-/etc/claude-soma/secrets.env
      --setenv=HOME=/home/ubuntu "--setenv=PATH=${LEAD_PATH}"
      --setenv=CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
      --setenv=CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=0
      "--setenv=HERMES_LEAD_NAME=${NAME}"
      "--setenv=HERMES_NOTIFY_URL=${NOTIFY_URL}"
      --setenv=DISABLE_AUTOUPDATER=1
      -- "$TMUX" -L "$SOCK" new-session -d -s "$SESS" -c "$CWD"
      "${claude_argv[@]}"
      ";" pipe-pane -O -o -t "$SESS" "cat >> ${LOG}" )
    log_line ALLOW spawn "$NAME" ok "${#BRIEF}"
    exec "${cmd[@]}"
    ;;

  resume)
    [ "${#P[@]}" -eq 6 ] || deny "resume needs 5 args" resume "$NAME"
    MODE="${P[2]}"; UUID="${P[3]}"; TIER="${P[4]}"; B64="${P[5]}"
    valid_name "$NAME"            || deny "bad name" resume "$NAME"
    [[ "$MODE_ALLOW" == *" $MODE "* ]] || deny "bad permission-mode" resume "$NAME"
    [[ "$TIER_ALLOW" == *" $TIER "* ]] || deny "bad tier" resume "$NAME"
    [[ "$UUID" =~ $UUID_RX ]]     || deny "bad session uuid" resume "$NAME"
    [[ "$B64" =~ $B64_RX ]]       || deny "prompt not base64" resume "$NAME"
    [ "${#B64}" -le "$MAX_B64_LEN" ] || deny "prompt too long" resume "$NAME"
    PROMPT="$(printf '%s' "$B64" | "$BASE64" -d 2>/dev/null)" || deny "prompt decode failed" resume "$NAME"
    [ -n "$PROMPT" ]            || deny "empty prompt" resume "$NAME"

    UNIT="claude-soma-lead-${NAME}.service"
    SOCK="soma-lead-${NAME}"
    SESS="soma-proj-${NAME}"
    CWD="${LEAD_PROJECTS_BASE}/${NAME}"
    LOG="${LOG_DIR}/${NAME}.log"

    mkdir -p "$CWD" "$LOG_DIR" 2>/dev/null || true
    # pretrust cwd (mirror spawner._pretrust_cwd).
    /usr/bin/python3 - "$CWD" <<'PY' 2>/dev/null || true
import json,os,sys,tempfile,pathlib
cwd=sys.argv[1]; p=pathlib.Path.home()/".claude.json"
try: d=json.loads(p.read_text()) if p.exists() else {}
except Exception: d={}
e=d.setdefault("projects",{}).setdefault(cwd,{})
e["hasTrustDialogAccepted"]=True; e.setdefault("projectOnboardingSeenCount",0)
fd,t=tempfile.mkstemp(dir=str(p.parent),prefix=".claude.json.",suffix=".tmp")
import os as _o
with _o.fdopen(fd,"w") as f: json.dump(d,f,indent=2); f.flush(); _o.fsync(f.fileno())
_o.chmod(t,0o600); _o.replace(t,p)
PY

    # Clear any lingering active(exited) unit before re-running (mirror
    # spawner.resume_background_lead -> kill_session) so systemd-run won't
    # reject the unit name with "already exists".
    "$SUDO" -n "$SYSTEMCTL" stop "$UNIT" 2>/dev/null
    "$TMUX" -L "$SOCK" kill-session -t "$SESS" 2>/dev/null
    "$SUDO" -n "$SYSTEMCTL" reset-failed "$UNIT" 2>/dev/null

    claude_argv=( "$CLAUDE_SAFE"
      --resume "$UUID" --remote-control "$SESS" --add-dir "$CWD"
      --permission-mode "$MODE" --dangerously-skip-permissions
      --effort max --setting-sources user,project,local )
    [ -f "$LEAD_MCP_B" ] && claude_argv+=( --mcp-config "$LEAD_MCP_B" )
    claude_argv+=( -- "$PROMPT" )

    cmd=( "$SUDO" -n "$SYSTEMD_RUN" --collect --quiet "--unit=${UNIT}"
      --property=Type=oneshot --property=RemainAfterExit=yes
      --property=User=ubuntu --property=Group=ubuntu
      --property=OOMScoreAdjust=800
      "--property=MemoryMax=${TIER_MAX[$TIER]}M" "--property=MemoryHigh=${TIER_HIGH[$TIER]}M"
      --property=EnvironmentFile=-/etc/claude-soma/secrets.env
      --setenv=HOME=/home/ubuntu "--setenv=PATH=${LEAD_PATH}"
      --setenv=CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
      --setenv=CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=0
      "--setenv=HERMES_LEAD_NAME=${NAME}"
      "--setenv=HERMES_NOTIFY_URL=${NOTIFY_URL}"
      --setenv=DISABLE_AUTOUPDATER=1
      -- "$TMUX" -L "$SOCK" new-session -d -s "$SESS" -c "$CWD"
      "${claude_argv[@]}"
      ";" pipe-pane -O -o -t "$SESS" "cat >> ${LOG}" )
    log_line ALLOW resume "$NAME" ok "${#PROMPT}"
    exec "${cmd[@]}"
    ;;

  kill)
    [ "${#P[@]}" -eq 2 ] || deny "kill needs 1 arg" kill "$NAME"
    valid_name "$NAME"   || deny "bad name" kill "$NAME"
    log_line ALLOW kill "$NAME" ok 0
    "$SUDO" -n "$SYSTEMCTL" stop "claude-soma-lead-${NAME}.service" 2>/dev/null
    "$TMUX" -L "soma-lead-${NAME}" kill-session -t "soma-proj-${NAME}" 2>/dev/null
    "$SUDO" -n "$SYSTEMCTL" reset-failed "claude-soma-lead-${NAME}.service" 2>/dev/null
    exit 0
    ;;

  capture)
    [ "${#P[@]}" -eq 2 ] || deny "capture needs 1 arg" capture "$NAME"
    valid_name "$NAME"   || deny "bad name" capture "$NAME"
    log_line ALLOW capture "$NAME" ok 0
    # -S -200: include scrollback so the startup Remote-Control URL is still
    # captured after claude's TUI has redrawn past it (A parses RC_URL_RX).
    exec "$TMUX" -L "soma-lead-${NAME}" capture-pane -p -S -200 -t "soma-proj-${NAME}"
    ;;

  list)
    [ "${#P[@]}" -eq 2 ] || deny "list needs 1 arg" list "$NAME"
    valid_name "$NAME"   || deny "bad name" list "$NAME"
    log_line ALLOW list "$NAME" ok 0
    exec "$TMUX" -L "soma-lead-${NAME}" list-panes -s -t "soma-proj-${NAME}" \
        -F '#{pane_index}	#{pane_dead}	#{pane_title}'
    ;;

  has-session)
    [ "${#P[@]}" -eq 2 ] || deny "has-session needs 1 arg" has-session "$NAME"
    valid_name "$NAME"   || deny "bad name" has-session "$NAME"
    log_line ALLOW has-session "$NAME" ok 0
    # exec so tmux's exit code (0=alive / nonzero=gone) propagates to A's
    # tri-state liveness — distinct from an ssh-unreachable failure.
    exec "$TMUX" -L "soma-lead-${NAME}" has-session -t "soma-proj-${NAME}"
    ;;

  stat-transcript)
    [ "${#P[@]}" -eq 2 ] || deny "stat-transcript needs 1 arg" stat-transcript "$NAME"
    valid_name "$NAME"   || deny "bad name" stat-transcript "$NAME"
    log_line ALLOW stat-transcript "$NAME" ok 0
    exec /usr/bin/stat -c '%s %Y' "${LOG_DIR}/${NAME}.log"
    ;;

  rc-url)
    # The Remote-Control URL is printed once at startup and lands in the
    # pipe-pane log (NOT the live tmux pane after claude's TUI redraws), so read
    # it from the log. Emits only the URL; grep exits non-zero until it appears.
    [ "${#P[@]}" -eq 2 ] || deny "rc-url needs 1 arg" rc-url "$NAME"
    valid_name "$NAME"   || deny "bad name" rc-url "$NAME"
    log_line ALLOW rc-url "$NAME" ok 0
    exec /usr/bin/grep -m1 -oE 'https://(claude\.ai/code/session_[A-Za-z0-9_-]+|rc\.claude\.com/[^[:space:]]+)' "${LOG_DIR}/${NAME}.log"
    ;;

  *)
    deny "unknown verb: ${VERB}" "$VERB" "$NAME"
    ;;
esac
