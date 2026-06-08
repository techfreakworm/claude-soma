#!/usr/bin/env bash
# scripts/deploy-systemd.sh
#
# Syncs systemd unit files from the repo (systemd/) to /etc/systemd/system and
# reloads the daemon.  Fixes the /etc-vs-/opt drift bug: bootstrap installs units
# from the repo, but subsequent `git pull` deploys update the repo copy only —
# /etc/systemd/system/*.{service,timer} silently lag behind.
#
# CHANNEL SELF-RESTART HAZARD: claude-soma-channel.service is the running bot
# process.  If this script were invoked from a Telegram-triggered deploy, it runs
# as a child process inside the channel's cgroup.  Restarting the channel unit
# would send SIGTERM to the whole cgroup, killing this script and the calling bot
# session mid-flight.  Therefore claude-soma-channel.service is HARD-EXCLUDED from
# auto-restart in ALL modes.  The operator must restart it manually.
#
# Usage (standalone on the VPS):
#   sudo bash scripts/deploy-systemd.sh            # sync + safe auto-restart
#   sudo bash scripts/deploy-systemd.sh --restart-services  # also restart changed .service units
#   sudo bash scripts/deploy-systemd.sh --dry-run  # print intended actions, change nothing
#
# Env overrides (useful for tests or the future engagement-pipeline migration):
#   SYSTEMD_REPO     — source directory of unit files (default: <script_dir>/../systemd)
#   SYSTEMD_DEST     — destination directory          (default: /etc/systemd/system)
#   SYSTEMCTL_BIN    — systemctl binary               (default: systemctl)
#   SUDO             — privilege escalation command   (default: sudo; tests set to "")
#
# Idempotent: re-running when all units are already up-to-date is a no-op.
# Exits non-zero on genuine failure (e.g. a unit fails to restart after reload).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSTEMD_REPO="${SYSTEMD_REPO:-$(cd "$SCRIPT_DIR/.." && pwd)/systemd}"
SYSTEMD_DEST="${SYSTEMD_DEST:-/etc/systemd/system}"
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-systemctl}"
SUDO="${SUDO:-sudo}"

DRY_RUN=0
RESTART_SERVICES=0
for arg in "$@"; do
    case "$arg" in
        --dry-run)          DRY_RUN=1 ;;
        --restart-services) RESTART_SERVICES=1 ;;
    esac
done

CHANNEL_SVC="claude-soma-channel.service"

_run() {
    if [[ $DRY_RUN -eq 1 ]]; then
        echo "[DRY-RUN] $*"
    else
        "$@"
    fi
}

_priv() {
    if [[ -n "${SUDO}" ]]; then
        _run "$SUDO" "$@"
    else
        _run "$@"
    fi
}

_info()  { echo "[INFO]    $*"; }
_skip()  { echo "[SKIP]    $*"; }
_action(){ echo "[ACTION]  $*"; }
_warn()  { echo "[WARN]    $*" >&2; }
_fail()  { echo "[FAIL]    $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Sync units
# ---------------------------------------------------------------------------

if [[ $DRY_RUN -eq 1 ]]; then
    echo "=== DRY-RUN MODE — no changes will be applied ==="
fi

_info "Syncing unit files: $SYSTEMD_REPO -> $SYSTEMD_DEST"

changed_services=()
changed_timers=()

for unit_file in "$SYSTEMD_REPO"/*.service "$SYSTEMD_REPO"/*.timer; do
    [[ -f "$unit_file" ]] || continue
    name="$(basename "$unit_file")"
    dest="$SYSTEMD_DEST/$name"

    if [[ -f "$dest" ]] && cmp -s "$unit_file" "$dest"; then
        _skip "$name (unchanged)"
        continue
    fi

    _action "Copy $name -> $dest"
    _priv cp -p "$unit_file" "$dest"

    case "$name" in
        *.timer)   changed_timers+=("$name") ;;
        *.service) changed_services+=("$name") ;;
    esac
done

# ---------------------------------------------------------------------------
# daemon-reload (only if anything changed)
# ---------------------------------------------------------------------------

total_changed=$(( ${#changed_services[@]} + ${#changed_timers[@]} ))

if [[ $total_changed -eq 0 ]]; then
    _skip "All unit files already up-to-date — nothing to reload"
    exit 0
fi

_info "Running daemon-reload (${total_changed} unit(s) changed)"
_priv "$SYSTEMCTL_BIN" daemon-reload

# ---------------------------------------------------------------------------
# Restart changed timers (safe: no running process is killed)
# Condition: sibling .service must exist in DEST (mirrors migrate-to-ist.sh)
# ---------------------------------------------------------------------------

for name in "${changed_timers[@]}"; do
    svc="${name%.timer}.service"
    if [[ -f "$SYSTEMD_DEST/$svc" ]]; then
        _action "Restart $name"
        _priv "$SYSTEMCTL_BIN" restart "$name"
        if [[ $DRY_RUN -eq 0 ]]; then
            if ! "$SYSTEMCTL_BIN" is-active "$name" >/dev/null 2>&1; then
                _fail "$name failed to become active after restart"
            fi
            _info "$name active"
        fi
    else
        _skip "Restart $name — sibling $svc not in $SYSTEMD_DEST, skipping"
    fi
done

# ---------------------------------------------------------------------------
# Handle changed .service files
# ---------------------------------------------------------------------------

for name in "${changed_services[@]}"; do
    if [[ "$name" == "$CHANNEL_SVC" ]]; then
        echo "RESTART REQUIRED (manual, never auto): $name"
        _warn "$name is the running bot; restart it manually once safe:"
        _warn "  sudo systemctl restart $name"
        continue
    fi

    if [[ $RESTART_SERVICES -eq 1 ]]; then
        _action "Restart $name"
        _priv "$SYSTEMCTL_BIN" restart "$name"
        if [[ $DRY_RUN -eq 0 ]]; then
            _info "$name restarted"
        fi
    else
        echo "RESTART REQUIRED: $name"
        _info "Run: sudo systemctl restart $name  (or re-run with --restart-services)"
    fi
done

if [[ $DRY_RUN -eq 1 ]]; then
    echo "=== DRY-RUN complete — nothing was changed ==="
else
    echo "=== Unit sync complete ==="
fi
