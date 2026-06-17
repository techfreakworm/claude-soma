#!/usr/bin/env bash
# scripts/enroll_vps_host.sh
#
# Enroll an additional VPS as a Claude Soma *lead-runtime* host: the orchestrator
# stays on this box (A); the new host (B) only RUNS leads, commanded over
# SSH-on-Tailscale via the forced-command guard. This codifies the by-hand
# VPS-B bootstrap into one idempotent, secure, self-verifying command.
#
# It does NOT install a second orchestrator/channel/listener/timers on B.
# Re-running it converges to the same state (safe to re-run on an enrolled host).
#
# Usage (run on A, the orchestrator host):
#   scripts/enroll_vps_host.sh \
#       --alias vps-b --tailnet-ip 100.102.145.110 \
#       [--ssh-user ubuntu] [--identity ~/.ssh/soma-orchestrator] \
#       [--admin-key ~/.ssh/id_ed25519] \
#       [--ram-mb auto] [--max-concurrent 3] \
#       [--degraded-webhook https://discord.com/api/webhooks/...] \
#       [--no-verify-spawn] [--skip-apt] [--dry-run]
#
# PREREQUISITES that stay operator-manual (outside the trust boundary):
#   1. The box exists and is reachable on the tailnet (tailscale up / ACL A<->B).
#   2. The --admin-key's PUBLIC key is in B:~/.ssh/authorized_keys (trust root).
#   3. Cloud firewall denies public inbound except tailnet (no 0.0.0.0 exposure).
#   4. A's notify listener binds the A-tailnet IP (HERMES_NOTIFY_BIND) — true once
#      Phase-1 is live; no A restart is needed to add a host.
#
# SECURITY (asserted by the script; see also docs/multi-vps.md):
#   - orchestrator key lands ONLY as a forced-command line (guard), never bare.
#   - secrets are an INCLUDE allowlist + a post-write EXCLUDE audit (fail on leak).
#   - claude auth copies claudeAiOauth WITH refreshToken (asserted), never mcpOAuth.
#   - no secret VALUE is ever printed/logged; every secret hop is a pipe.
set -euo pipefail

# ---------------------------------------------------------------------------
# defaults + arg parse
# ---------------------------------------------------------------------------
ALIAS="" ; TAILNET_IP="" ; SSH_USER="ubuntu"
IDENTITY="$HOME/.ssh/soma-orchestrator"          # forced-command (guard) key
ADMIN_KEY="$HOME/.ssh/id_ed25519"                # one-time provisioning key (full shell)
A_TAILNET_IP="${SOMA_A_TAILNET_IP:-100.103.37.115}"
NOTIFY_PORT="${HERMES_NOTIFY_PORT:-9100}"
RAM_MB="auto" ; MAX_CONCURRENT="3" ; DEGRADED_WEBHOOK=""
VERIFY_SPAWN=1 ; SKIP_APT=0 ; DRY_RUN=0
REPO_DIR="${SOMA_REPO_DIR:-/opt/claude-soma}"
SECRETS_A="/etc/claude-soma/secrets.env"
CREDS_A="$HOME/.claude/.credentials.json"

die() { echo "enroll: ERROR: $*" >&2; exit 1; }
info() { echo "==== $* ===="; }
run() { if [ "$DRY_RUN" = 1 ]; then echo "DRY: $*"; else eval "$@"; fi; }

while [ $# -gt 0 ]; do
  case "$1" in
    --alias) ALIAS="$2"; shift 2;;
    --tailnet-ip) TAILNET_IP="$2"; shift 2;;
    --ssh-user) SSH_USER="$2"; shift 2;;
    --identity) IDENTITY="$2"; shift 2;;
    --admin-key) ADMIN_KEY="$2"; shift 2;;
    --a-tailnet-ip) A_TAILNET_IP="$2"; shift 2;;
    --ram-mb) RAM_MB="$2"; shift 2;;
    --max-concurrent) MAX_CONCURRENT="$2"; shift 2;;
    --degraded-webhook) DEGRADED_WEBHOOK="$2"; shift 2;;
    --no-verify-spawn) VERIFY_SPAWN=0; shift;;
    --skip-apt) SKIP_APT=1; shift;;
    --dry-run) DRY_RUN=1; shift;;
    -h|--help) sed -n '2,40p' "$0"; exit 0;;
    *) die "unknown arg: $1";;
  esac
done

[ -n "$ALIAS" ] || die "--alias required"
[ -n "$TAILNET_IP" ] || die "--tailnet-ip required"
[[ "$ALIAS" =~ ^[a-z][a-z0-9-]{0,31}$ ]] || die "bad --alias (want ^[a-z][a-z0-9-]{0,31}\$)"
[ -f "$ADMIN_KEY" ] || die "admin key $ADMIN_KEY not found"
[ -f "$IDENTITY" ] || die "orchestrator key $IDENTITY not found"
[ -f "${IDENTITY}.pub" ] || die "orchestrator PUBLIC key ${IDENTITY}.pub not found"

ADMIN=( ssh -i "$ADMIN_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=12 "${SSH_USER}@${TAILNET_IP}" )
ORCH=( ssh -i "$IDENTITY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=12 "${SSH_USER}@${TAILNET_IP}" )
b_admin() { if [ "$DRY_RUN" = 1 ]; then echo "DRY(admin): $*"; else "${ADMIN[@]}" "$@"; fi; }

# ---------------------------------------------------------------------------
info "0/13  pre-flight: reachability over admin key"
# ---------------------------------------------------------------------------
# read-only probes run even under --dry-run so dry-run validates real reachability
HOSTINFO="$("${ADMIN[@]}" 'echo OK $(uname -m) $(. /etc/os-release; echo $VERSION_ID)')" \
  || die "cannot reach ${SSH_USER}@${TAILNET_IP} with admin key (tailnet up? key trusted?)"
echo "host: $HOSTINFO"
case "$HOSTINFO" in *aarch64*) :;; *) echo "  WARN: host not aarch64 — venv/wheel parity not guaranteed";; esac

if [ "$RAM_MB" = "auto" ]; then
  RAM_MB="$("${ADMIN[@]}" "awk '/MemTotal/{print int(\$2/1024)}' /proc/meminfo" 2>/dev/null || echo 8000)"
  echo "probed RAM_MB=$RAM_MB"
fi

# ---------------------------------------------------------------------------
info "1/13  apt base packages"
# ---------------------------------------------------------------------------
if [ "$SKIP_APT" = 1 ]; then echo "skipped (--skip-apt)"; else
  b_admin 'sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq && \
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
      build-essential python3.12 python3.12-venv python3.12-dev \
      tmux jq sqlite3 rsync git curl ca-certificates nodejs npm libssl-dev >/dev/null 2>&1' \
    || die "apt base install failed"
  echo "apt base ok"
fi

# ---------------------------------------------------------------------------
info "2/13  directories"
# ---------------------------------------------------------------------------
b_admin "sudo mkdir -p '$REPO_DIR' /etc/claude-soma /var/log/claude-soma && \
         sudo chown ${SSH_USER}:${SSH_USER} '$REPO_DIR' /var/log/claude-soma" || die "mkdir failed"

# ---------------------------------------------------------------------------
info "3/13  ship repo (git archive HEAD — exact commit, NO GitHub creds)"
# ---------------------------------------------------------------------------
HEAD_SHA="$(git -C "$REPO_DIR" rev-parse --short HEAD)"
echo "shipping commit $HEAD_SHA"
if [ "$DRY_RUN" = 1 ]; then echo "DRY: git archive HEAD | ssh ... tar -x -C $REPO_DIR"; else
  git -C "$REPO_DIR" archive HEAD | "${ADMIN[@]}" "tar -x -C '$REPO_DIR'" || die "repo ship failed"
fi
b_admin "chmod +x '$REPO_DIR'/scripts/*.sh 2>/dev/null; true"

# ---------------------------------------------------------------------------
info "4/13  python venv + editable install"
# ---------------------------------------------------------------------------
b_admin "cd '$REPO_DIR' && { [ -x .venv/bin/python ] || python3.12 -m venv .venv; } && \
  .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -e . 2>&1 | tail -2; true" \
  || die "venv/pip install failed"
echo "venv ready"

# ---------------------------------------------------------------------------
info "5/13  claude CLI (match A's pinned version) + claude-safe"
# ---------------------------------------------------------------------------
A_CLAUDE_VER="$("$HOME/.local/bin/claude" --version 2>/dev/null | awk '{print $1}')"
B_CLAUDE_VER="$(b_admin '~/.local/bin/claude --version 2>/dev/null | awk "{print \$1}"' || true)"
echo "claude: A=$A_CLAUDE_VER  B=${B_CLAUDE_VER:-none}"
if [ -n "$A_CLAUDE_VER" ] && [ "$A_CLAUDE_VER" != "${B_CLAUDE_VER:-}" ]; then
  echo "  installing claude $A_CLAUDE_VER on B ..."
  b_admin "curl -fsSL https://claude.ai/install.sh | bash -s -- $A_CLAUDE_VER >/dev/null 2>&1 || \
           ~/.local/bin/claude install $A_CLAUDE_VER >/dev/null 2>&1 || true"
  NOW="$(b_admin '~/.local/bin/claude --version 2>/dev/null | awk "{print \$1}"' || true)"
  [ "$NOW" = "$A_CLAUDE_VER" ] && echo "  claude now $NOW" || \
    echo "  WARN: could not pin claude to $A_CLAUDE_VER (B=$NOW); install manually if leads fail to boot"
fi
# claude-safe wrapper (shipped in repo) -> /usr/local/bin
b_admin "sudo install -m 0755 '$REPO_DIR/scripts/claude-safe.sh' /usr/local/bin/claude-safe" \
  || die "claude-safe install failed"

# ---------------------------------------------------------------------------
info "6/13  forced-command guard authorized_keys line (orchestrator key)"
# ---------------------------------------------------------------------------
GUARD="$REPO_DIR/scripts/remote-exec-guard.sh"
PUBKEY="$(cat "${IDENTITY}.pub")"
AK_LINE="command=\"$GUARD\",from=\"$A_TAILNET_IP\",restrict,no-agent-forwarding,no-pty,no-port-forwarding $PUBKEY"
# grep-before-append (idempotent); match on the pubkey body so we replace stale lines
PUB_BODY="$(awk '{print $2}' "${IDENTITY}.pub")"
if [ "$DRY_RUN" = 1 ]; then echo "DRY: ensure forced-command line for orchestrator key in B authorized_keys"; else
  printf '%s\n' "$AK_LINE" | "${ADMIN[@]}" "
    mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
    grep -v '$PUB_BODY' ~/.ssh/authorized_keys > ~/.ssh/authorized_keys.tmp || true
    cat >> ~/.ssh/authorized_keys.tmp
    mv ~/.ssh/authorized_keys.tmp ~/.ssh/authorized_keys" || die "authorized_keys update failed"
fi
echo "guard key installed (forced-command, from=$A_TAILNET_IP)"

# ---------------------------------------------------------------------------
info "7/13  scoped sudoers for the spawner"
# ---------------------------------------------------------------------------
SUDOERS_CONTENT='# Managed by enroll_vps_host.sh — scoped spawner privileges for the guard.
ubuntu ALL=(root) NOPASSWD: /usr/bin/systemd-run *
ubuntu ALL=(root) NOPASSWD: /bin/systemctl stop claude-soma-lead-*
ubuntu ALL=(root) NOPASSWD: /usr/bin/systemctl stop claude-soma-lead-*
ubuntu ALL=(root) NOPASSWD: /bin/systemctl reset-failed claude-soma-lead-*
ubuntu ALL=(root) NOPASSWD: /usr/bin/systemctl reset-failed claude-soma-lead-*
ubuntu ALL=(root) NOPASSWD: /bin/systemctl kill claude-soma-lead-*
ubuntu ALL=(root) NOPASSWD: /usr/bin/systemctl kill claude-soma-lead-*'
if [ "$DRY_RUN" = 1 ]; then echo "DRY: install /etc/sudoers.d/99-claude-soma-spawner (visudo-checked)"; else
  printf '%s\n' "$SUDOERS_CONTENT" | "${ADMIN[@]}" "
    cat > /tmp/99-claude-soma-spawner &&
    sudo visudo -cf /tmp/99-claude-soma-spawner &&
    sudo install -m 0440 -o root -g root /tmp/99-claude-soma-spawner /etc/sudoers.d/99-claude-soma-spawner &&
    rm -f /tmp/99-claude-soma-spawner" || die "sudoers install failed visudo check"
fi
echo "sudoers ok"

# ---------------------------------------------------------------------------
info "8/13  ~/.claude/settings.json (skipDangerousModePermissionPrompt)"
# ---------------------------------------------------------------------------
b_admin 'python3 - <<PY
import json,os
p=os.path.expanduser("~/.claude/settings.json")
os.makedirs(os.path.dirname(p),exist_ok=True)
try: d=json.load(open(p))
except Exception: d={}
d["skipDangerousModePermissionPrompt"]=True
json.dump(d,open(p,"w"),indent=2)
print("settings.json ok")
PY' || die "settings.json update failed"

# ---------------------------------------------------------------------------
info "9/13  lead-mcp-b.json present (shipped via repo)"
# ---------------------------------------------------------------------------
b_admin "test -f '$REPO_DIR/config/claude/lead-mcp-b.json' && echo present || { echo MISSING; exit 1; }" \
  || die "lead-mcp-b.json missing after repo ship (commit it on A?)"

# ---------------------------------------------------------------------------
info "10/13 secrets.env subset (INCLUDE allowlist) + EXCLUDE audit"
# ---------------------------------------------------------------------------
# INCLUDE allowlist — only what a lead-runtime host needs. Auth is the native
# store (~/.claude/.credentials.json), NOT secrets.env.
read_a() { sudo sed -n "s/^$1=//p" "$SECRETS_A" 2>/dev/null | head -1; }
NOTIFY_TOKEN="$(read_a HERMES_NOTIFY_TOKEN)"
RELAY_DOMAIN="$(read_a SOMA_RELAY_DOMAIN)"
MAXPROj="$(read_a HERMES_MAX_CONCURRENT_PROJECTS)"; [ -n "$MAXPROj" ] || MAXPROj="$MAX_CONCURRENT"
[ -n "$NOTIFY_TOKEN" ] || die "HERMES_NOTIFY_TOKEN not found in $SECRETS_A (Phase-1 staged?)"
if [ "$DRY_RUN" = 1 ]; then echo "DRY: write B /etc/claude-soma/secrets.env (3-4 keys, 0600) + audit"; else
  {
    printf 'HERMES_NOTIFY_TOKEN=%s\n' "$NOTIFY_TOKEN"
    [ -n "$RELAY_DOMAIN" ] && printf 'SOMA_RELAY_DOMAIN=%s\n' "$RELAY_DOMAIN"
    printf 'HERMES_MAX_CONCURRENT_PROJECTS=%s\n' "$MAXPROj"
    [ -n "$DEGRADED_WEBHOOK" ] && printf 'HERMES_DEGRADED_WEBHOOK=%s\n' "$DEGRADED_WEBHOOK"
    true   # ensure the brace group exits 0 (empty optional vars must not fail the pipe)
  } | "${ADMIN[@]}" "umask 077; sudo mkdir -p /etc/claude-soma; sudo tee /etc/claude-soma/secrets.env >/dev/null && sudo chmod 600 /etc/claude-soma/secrets.env && sudo chown ${SSH_USER}:${SSH_USER} /etc/claude-soma/secrets.env" \
    || die "secrets write failed"
  unset NOTIFY_TOKEN RELAY_DOMAIN
  # AUDIT: fail if any EXCLUDE-set key leaked onto B
  LEAK="$(b_admin 'sudo grep -oE "^(AUTH_[A-Z_]*|GITHUB_TOKEN|TELEGRAM_BOT_TOKEN|DISCORD_BOT_TOKEN|HERMES_FILES_PASSWORD|HF_TOKEN|HERMES_ALLOWED_GITHUB_HANDLES|HERMES_NOTIFY_CHAT_ID)=" /etc/claude-soma/secrets.env 2>/dev/null | sort -u' || true)"
  [ -z "$LEAK" ] || die "SECRET LEAK on B secrets.env: $LEAK — aborting enroll"
  echo "secrets subset written (0600); EXCLUDE audit clean"
fi

# ---------------------------------------------------------------------------
info "11/13 claude auth: claudeAiOauth WITH refreshToken (the B-401 fix)"
# ---------------------------------------------------------------------------
# Extract ONLY claudeAiOauth (never mcpOAuth); assert BOTH tokens; ship via pipe; 0600.
if [ "$DRY_RUN" = 1 ]; then echo "DRY: ship claudeAiOauth (with refreshToken) to B ~/.claude/.credentials.json"; else
  AUTH_JSON="$(python3 - "$CREDS_A" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
o=d.get("claudeAiOauth") or {}
assert o.get("accessToken"), "A creds missing accessToken"
assert o.get("refreshToken"), "A creds missing refreshToken (cannot enroll — B would 401 on expiry)"
print(json.dumps({"claudeAiOauth":o}))
PY
)" || die "auth extract failed (need claudeAiOauth WITH refreshToken on A)"
  # Ship a NO-SECRET merge helper first (heredoc = cat's stdin), THEN pipe the
  # secret to it on stdin. (A bare `python3 - <<PY` over ssh would make the
  # heredoc python's stdin, so the piped secret would never reach json.load.)
  "${ADMIN[@]}" 'cat > /tmp/_cred_merge.py' <<'PYHELP'
import json, os, sys
home = os.path.expanduser("~/.claude/.credentials.json")
os.makedirs(os.path.dirname(home), exist_ok=True)
new = json.load(sys.stdin)
try:
    cur = json.load(open(home))
except Exception:
    cur = {}
cur["claudeAiOauth"] = new["claudeAiOauth"]  # replace only this key; keep any mcpOAuth B had
fd = os.open(home, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.write(fd, json.dumps(cur).encode()); os.close(fd)
o = cur["claudeAiOauth"]
print("auth ok: refreshToken=%s" % ("present" if o.get("refreshToken") else "MISSING"))
PYHELP
  printf '%s' "$AUTH_JSON" | "${ADMIN[@]}" 'python3 /tmp/_cred_merge.py; rc=$?; rm -f /tmp/_cred_merge.py; exit $rc' \
    || die "auth write failed"
  unset AUTH_JSON
fi

# ---------------------------------------------------------------------------
info "12/13 register host in A's hosts.json (status=unverified)"
# ---------------------------------------------------------------------------
HOSTS_PY_ARGS="alias='$ALIAS';ip='$TAILNET_IP';user='$SSH_USER';ident='$IDENTITY';ram=$RAM_MB;mc=$MAX_CONCURRENT"
if [ "$DRY_RUN" = 1 ]; then echo "DRY: upsert_host($ALIAS, ...) status=unverified"; else
  "$REPO_DIR/.venv/bin/python" - "$ALIAS" "$TAILNET_IP" "$SSH_USER" "$IDENTITY" "$RAM_MB" "$MAX_CONCURRENT" <<'PY' || die "hosts.json upsert failed"
import sys
sys.path.insert(0,"/opt/claude-soma/src")
from claude_soma.mcp_servers.project_orchestrator import hosts as H
alias,ip,user,ident,ram,mc=sys.argv[1],sys.argv[2],sys.argv[3],sys.argv[4],int(sys.argv[5]),int(sys.argv[6])
cfg=H.build_host_cfg(tailnet_ip=ip, ssh_user=user, ssh_identity=ident, ram_mb=ram, max_concurrent=mc, status="unverified")
H.upsert_host(alias, cfg, check_identity_files=True)
print("hosts.json upserted:", alias, "status=unverified")
PY
fi

# ---------------------------------------------------------------------------
info "13/13 self-verify (guard probe + notify round-trip) -> mark verified"
# ---------------------------------------------------------------------------
VERIFIED=1
# (a) guard reachable over the orchestrator key (forced-command contract)
if [ "$DRY_RUN" = 1 ]; then echo "DRY: guard has-session probe + notify round-trip"; else
  set +e +o pipefail   # verify is best-effort: expected non-zero (no-match greps,
                       # has-session=1) must set VERIFIED, never kill the script
  if "${ORCH[@]}" 'has-session enrollprobe' >/dev/null 2>&1; then :; else
    rc=$?; [ "$rc" = 1 ] || { echo "  guard probe: ssh/guard error rc=$rc"; VERIFIED=0; }
  fi
  echo "  guard contract reachable: $([ $VERIFIED = 1 ] && echo yes || echo NO)"

  # (b) optional full lead spawn (proves lead-runtime + LLM-from-B), then kill
  if [ "$VERIFY_SPAWN" = 1 ] && [ "$VERIFIED" = 1 ]; then
    PROBE_UUID="$(cat /proc/sys/kernel/random/uuid)"
    PROBE_B64="$(printf 'Reply with the single word READY and then stop.' | base64 -w0)"
    "${ORCH[@]}" "spawn enrollprobe acceptEdits $PROBE_UUID standard $PROBE_B64" >/dev/null 2>&1 || true
    URL=""
    for _ in $(seq 1 20); do
      URL="$("${ORCH[@]}" 'rc-url enrollprobe' 2>/dev/null | grep -oE 'https://claude.ai/code/session_[A-Za-z0-9]+' | head -1)"
      [ -n "$URL" ] && break; sleep 3
    done
    "${ORCH[@]}" 'kill enrollprobe' >/dev/null 2>&1 || true
    if [ -n "$URL" ]; then echo "  spawn+LLM probe: OK ($URL)"; else echo "  spawn+LLM probe: NO RC URL (LLM auth?)"; VERIFIED=0; fi
  fi

  # (c) notify round-trip from B -> A listener (bearer; token read on B, not printed)
  RT="$(b_admin "TOK=\$(sudo sed -n 's/^HERMES_NOTIFY_TOKEN=//p' /etc/claude-soma/secrets.env); \
    curl -s -o /dev/null -w '%{http_code}' --max-time 10 -X POST http://$A_TAILNET_IP:$NOTIFY_PORT/notify \
    -H \"Authorization: Bearer \$TOK\" -H 'Content-Type: application/json' \
    -d '{\"lead\":\"$ALIAS\",\"type\":\"MILESTONE\",\"payload_json\":\"{\\\"progress\\\":\\\"enroll self-verify notify round-trip\\\"}\"}'" || true)"
  echo "  notify round-trip HTTP: ${RT:-error}"
  [ "$RT" = "202" ] || VERIFIED=0

  if [ "$VERIFIED" = 1 ]; then
    "$REPO_DIR/.venv/bin/python" - "$ALIAS" <<'PY'
import sys; sys.path.insert(0,"/opt/claude-soma/src")
from claude_soma.mcp_servers.project_orchestrator import hosts as H
H.set_host_status(sys.argv[1], "verified")
print("hosts.json:", sys.argv[1], "-> verified")
PY
  fi
fi

echo
if [ "$DRY_RUN" = 1 ]; then
  info "DRY-RUN complete — no changes made"
elif [ "$VERIFIED" = 1 ]; then
  info "ENROLLED + VERIFIED: $ALIAS ($TAILNET_IP) commit $HEAD_SHA"
  echo "place a lead with:  spawn_project(name=..., host=$ALIAS, tier=critical|standard)"
else
  info "ENROLLED but UNVERIFIED: $ALIAS — see failures above; host left status=unverified"
  exit 2
fi
