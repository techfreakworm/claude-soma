#!/usr/bin/env bash
# scripts/migrate-to-ist.sh
#
# OPERATOR runs this. Do NOT auto-execute from any subagent session.
# System-wide timezone change is too high-impact for the auto-restart window.
#
# Migrates the VPS from UTC to Asia/Kolkata (IST, +05:30) and sets LC_TIME=en_IN.UTF-8.
# Also installs updated systemd timer files from the repo and reloads affected timers.
#
# Usage:
#   sudo bash scripts/migrate-to-ist.sh          # apply
#   sudo bash scripts/migrate-to-ist.sh --dry-run # print every change without applying
#
# Idempotent: re-running when already on IST is a no-op for each step.
# Exits non-zero on the first failure; validates each step's post-condition.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
SYSTEMD_REPO="${SYSTEMD_REPO:-$REPO_ROOT/systemd}"
SYSTEMD_DEST="${SYSTEMD_DEST:-/etc/systemd/system}"

DRY_RUN=0
for arg in "$@"; do
    [[ "$arg" == "--dry-run" ]] && DRY_RUN=1
done

_run() {
    if [[ $DRY_RUN -eq 1 ]]; then
        echo "[DRY-RUN] $*"
    else
        "$@"
    fi
}

_info()  { echo "[INFO]   $*"; }
_skip()  { echo "[SKIP]   $*"; }
_action(){ echo "[ACTION] $*"; }
_fail()  { echo "[FAIL]   $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Step 1: timezone
# ---------------------------------------------------------------------------
step_timezone() {
    _info "Step 1: timezone"
    local current
    current=$(timedatectl show --property=Timezone --value 2>/dev/null \
              || timedatectl status 2>/dev/null | awk '/Time zone/{print $3}' \
              || echo "unknown")
    if [[ "$current" == "Asia/Kolkata" ]]; then
        _skip "Timezone already Asia/Kolkata"
        return 0
    fi
    _action "Set timezone Asia/Kolkata (current: $current)"
    _run sudo timedatectl set-timezone Asia/Kolkata
    if [[ $DRY_RUN -eq 0 ]]; then
        local new
        new=$(timedatectl show --property=Timezone --value 2>/dev/null || echo "")
        [[ "$new" == "Asia/Kolkata" ]] || _fail "Timezone post-check failed: got '$new'"
        _info "Timezone confirmed: $new"
    fi
}

# ---------------------------------------------------------------------------
# Step 2: locale — generate en_IN.UTF-8 and set LC_TIME
# ---------------------------------------------------------------------------
step_locale() {
    _info "Step 2: locale"

    # 2a: generate locale if missing
    if locale -a 2>/dev/null | grep -qi "en_IN.utf8\|en_IN.UTF-8\|en_IN.utf-8"; then
        _skip "en_IN.UTF-8 locale already present"
    else
        _action "Generate en_IN.UTF-8 locale"
        _run sudo locale-gen en_IN.UTF-8
        if [[ $DRY_RUN -eq 0 ]]; then
            locale -a 2>/dev/null | grep -qi "en_IN.utf8\|en_IN.UTF-8" \
                || _fail "en_IN.UTF-8 locale not found after locale-gen"
            _info "Locale en_IN.UTF-8 generated"
        fi
    fi

    # 2b: set LC_TIME in system locale config
    local locale_file
    if [[ -f /etc/locale.conf ]]; then
        locale_file="/etc/locale.conf"
    elif [[ -f /etc/default/locale ]]; then
        locale_file="/etc/default/locale"
    else
        locale_file="/etc/default/locale"
    fi

    if grep -q "^LC_TIME=en_IN.UTF-8" "$locale_file" 2>/dev/null; then
        _skip "LC_TIME=en_IN.UTF-8 already set in $locale_file"
    else
        _action "Set LC_TIME=en_IN.UTF-8 via update-locale (file: $locale_file)"
        _run sudo update-locale LANG=en_US.UTF-8 LC_TIME=en_IN.UTF-8
        if [[ $DRY_RUN -eq 0 ]]; then
            grep -q "^LC_TIME=en_IN.UTF-8" "$locale_file" \
                || _fail "LC_TIME not found in $locale_file after update-locale"
            _info "LC_TIME=en_IN.UTF-8 confirmed in $locale_file"
        fi
    fi
}

# ---------------------------------------------------------------------------
# Step 3: install timer files from repo to SYSTEMD_DEST
# ---------------------------------------------------------------------------
step_install_timers() {
    _info "Step 3: install timer files ($SYSTEMD_REPO → $SYSTEMD_DEST)"
    local changed_timers=()
    local timer name dest

    for timer in "$SYSTEMD_REPO"/*.timer; do
        [[ -f "$timer" ]] || continue
        name="$(basename "$timer")"
        dest="$SYSTEMD_DEST/$name"

        if [[ -f "$dest" ]] && cmp -s "$timer" "$dest"; then
            _skip "$name (unchanged)"
        else
            _action "Install $name → $dest"
            _run sudo cp -p "$timer" "$dest"
            changed_timers+=("$name")
        fi
    done

    if [[ ${#changed_timers[@]} -eq 0 ]]; then
        _skip "All timer files already up-to-date"
        return 0
    fi

    # Step 4: daemon-reload
    _info "Step 4: systemctl daemon-reload"
    _run sudo systemctl daemon-reload

    # Step 5: restart changed timers if their .service is installed
    _info "Step 5: restart changed timers"
    for name in "${changed_timers[@]}"; do
        local svc="${name%.timer}.service"
        if [[ -f "$SYSTEMD_DEST/$svc" ]]; then
            _action "Restart $name"
            _run sudo systemctl restart "$name"
            if [[ $DRY_RUN -eq 0 ]]; then
                sleep 1
                systemctl is-active "$name" >/dev/null 2>&1 \
                    || _fail "$name failed to restart"
                _info "$name active"
            fi
        else
            _skip "Restart $name — .service not installed, skipping"
        fi
    done
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if [[ $DRY_RUN -eq 1 ]]; then
    echo "=== DRY-RUN MODE — no changes will be applied ==="
fi

step_timezone
step_locale
step_install_timers

if [[ $DRY_RUN -eq 1 ]]; then
    echo "=== DRY-RUN complete — nothing was changed ==="
else
    echo "=== Migration complete ==="
    echo "    Restart your shell (or run: exec \$SHELL -l) to pick up LC_TIME."
    echo "    Verify with: timedatectl && locale"
fi
