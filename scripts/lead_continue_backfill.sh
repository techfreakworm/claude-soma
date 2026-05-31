#!/usr/bin/env bash
# scripts/lead_continue_backfill.sh
#
# One-time backfill for existing claude-soma-lead-* transient units:
#
#   1. Insert --continue into ExecStart so the next restart preserves session
#      state (original FI-LEAD-CONTINUE backfill, d5a24c8).
#
#   2. Inject FI-NOTIFY env vars so leads can call
#      mcp__hermes-notify__notify_orchestrator (FI-ENV-BACKFILL, wave 1):
#        Environment="HERMES_LEAD_NAME=<extracted-from-unit-filename>"
#        Environment="HERMES_NOTIFY_ENDPOINT=http://127.0.0.1:9100"
#
# Both injections are idempotent: each is independently skipped if already
# present. Does NOT restart any unit.
#
# Usage:
#   sudo bash scripts/lead_continue_backfill.sh
#
# Environment variables (test-only knobs):
#   LEAD_CONTINUE_BACKFILL_DIR    Override the directory to scan instead of
#                                 /run/systemd/transient (default).
#   LEAD_CONTINUE_BACKFILL_NOSUDO=1
#                                 When set AND BACKFILL_DIR is a non-default
#                                 path, skip sudo on sed and daemon-reload so
#                                 tests can run without root. NEVER set this
#                                 against the real transient dir.
#
# Note: the Description= line in each unit also echoes the command as human-
# readable text; it is intentionally NOT patched here. Description is cosmetic
# and has a different (unquoted) format that is harder to match reliably. The
# binding command is the ExecStart= line — patching that is sufficient.

set -euo pipefail

CLAUDE_BIN="/home/ubuntu/.local/bin/claude"
TRANSIENT_DIR="${LEAD_CONTINUE_BACKFILL_DIR:-/run/systemd/transient}"

# Determine whether to prefix commands with sudo.
# When LEAD_CONTINUE_BACKFILL_NOSUDO=1 AND TRANSIENT_DIR is not the real
# transient dir, run without sudo (test-isolation mode). In all other cases,
# use sudo because /run/systemd/transient is root-owned.
USE_SUDO=1
if [[ -n "${LEAD_CONTINUE_BACKFILL_NOSUDO:-}" && "$TRANSIENT_DIR" != "/run/systemd/transient" ]]; then
    USE_SUDO=0
fi

run_cmd() {
    if [[ "$USE_SUDO" -eq 1 ]]; then
        sudo "$@"
    else
        "$@"
    fi
}

patched=0
skipped=0
env_patched=0
env_skipped=0
errored=0

for unit in "$TRANSIENT_DIR"/claude-soma-lead-*.service; do
    [[ -f "$unit" ]] || continue
    name=$(basename "$unit")

    # Extract the lead name from the filename:
    #   claude-soma-lead-<name>.service  →  <name>
    unit_basename=$(basename "$unit" .service)
    lead_name="${unit_basename#claude-soma-lead-}"

    # --- --continue injection (original logic) ---
    if grep -q '"--continue"' "$unit"; then
        echo "skip: $name (already has --continue)"
        skipped=$((skipped + 1))
        # Fall through to env injection — --continue present does not imply env present.
    else
        # Check that the expected quoted binary+flag token pair is present.
        # The ExecStart line uses shell-quoted tokens, so we match the exact form:
        #   "/home/ubuntu/.local/bin/claude" "--remote-control"
        if ! run_cmd grep -q "\"$CLAUDE_BIN\" \"--remote-control\"" "$unit"; then
            echo "warn: $name — binary token pattern not found; leaving alone"
            errored=$((errored + 1))
            continue
        fi

        # Patch: insert "--continue" token immediately after the claude binary token.
        # Preserves the quoted-token shape of the ExecStart line.
        run_cmd sed -i \
            "s|\"$CLAUDE_BIN\" \"--remote-control\"|\"$CLAUDE_BIN\" \"--continue\" \"--remote-control\"|g" \
            "$unit"
        echo "patched: $name"
        patched=$((patched + 1))
    fi

    # --- FI-NOTIFY env injection ---
    # Inject into [Service] block so the restarted lead can call
    # mcp__hermes-notify__notify_orchestrator. Idempotent: skip if already present.
    if grep -qF "HERMES_LEAD_NAME=$lead_name" "$unit"; then
        echo "skip (env): $name (env vars already present)"
        env_skipped=$((env_skipped + 1))
    else
        # Insert Environment= lines right after the [Service] heading.
        # Two separate sed passes keeps each append atomic and avoids
        # multi-line sed portability concerns.
        run_cmd sed -i "/^\[Service\]$/a Environment=\"HERMES_LEAD_NAME=$lead_name\"" "$unit"
        run_cmd sed -i "/Environment=\"HERMES_LEAD_NAME=$lead_name\"/a Environment=\"HERMES_NOTIFY_ENDPOINT=http://127.0.0.1:9100\"" "$unit"
        echo "env patched: $name"
        env_patched=$((env_patched + 1))
    fi
done

if [[ ( "$patched" -gt 0 || "$env_patched" -gt 0 ) && "$USE_SUDO" -eq 1 ]]; then
    echo "running systemctl daemon-reload..."
    sudo systemctl daemon-reload
fi

echo
echo "backfill summary: patched=$patched env_patched=$env_patched skipped=$skipped env_skipped=$env_skipped errored=$errored"
echo
if [[ "$patched" -gt 0 || "$env_patched" -gt 0 ]]; then
    echo "Done. Next time you restart any patched unit (sudo systemctl"
    echo "restart claude-soma-lead-<name>.service), the lead will resume"
    echo "its prior transcript via --continue AND gain notify capability"
    echo "via HERMES_LEAD_NAME / HERMES_NOTIFY_ENDPOINT. NO units have"
    echo "been restarted automatically."
fi
