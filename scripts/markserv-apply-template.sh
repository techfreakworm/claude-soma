#!/usr/bin/env bash
# scripts/markserv-apply-template.sh
#
# Re-applies the repo-owned markserv HTML template every time the markserv
# service starts.  Invoked as root via the unit's ExecStartPre=+ directive so
# it can write the root-owned /usr/lib/node_modules template.  Runs before
# ExecStart (User=ubuntu), which means markserv always boots with the latest
# button even after npm reinstalls or repo redeploys.
#
# Design principles:
#   - NEVER exits non-zero.  If anything fails we log to stderr and return 0
#     so the ExecStartPre failure can never prevent markserv from starting.
#   - set -uo pipefail but NOT -e: individual command failures are caught
#     explicitly; unset-variable errors are surfaced but do not abort the
#     script without -e.
#   - Creates a .orig backup of the installed template the first time it runs,
#     so the original can be restored if needed.
#
# Environment overrides (useful for tests / CI):
#   SOMA_MARKSERV_TPL  — override the markserv template destination path
#   SOMA_MARKSERV_SRC  — override the repo source template path

set -uo pipefail

# ---------------------------------------------------------------------------
# Resolve destination (TPL)
# ---------------------------------------------------------------------------
if [ -n "${SOMA_MARKSERV_TPL:-}" ]; then
	TPL="${SOMA_MARKSERV_TPL}"
else
	NPM_ROOT="$(npm root -g 2>/dev/null)" || NPM_ROOT=""
	if [ -n "${NPM_ROOT}" ] && [ -f "${NPM_ROOT}/markserv/lib/templates/markdown.html" ]; then
		TPL="${NPM_ROOT}/markserv/lib/templates/markdown.html"
	else
		TPL="/usr/lib/node_modules/markserv/lib/templates/markdown.html"
	fi
fi

# ---------------------------------------------------------------------------
# Resolve source (SRC)
# ---------------------------------------------------------------------------
if [ -n "${SOMA_MARKSERV_SRC:-}" ]; then
	SRC="${SOMA_MARKSERV_SRC}"
else
	SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || SCRIPT_DIR=""
	if [ -z "${SCRIPT_DIR}" ]; then
		echo "markserv-apply-template: could not resolve script directory" >&2
		exit 0
	fi
	SRC="${SCRIPT_DIR}/../config/markserv/markdown.html"
fi

# ---------------------------------------------------------------------------
# Guard: source must exist
# ---------------------------------------------------------------------------
if [ ! -f "${SRC}" ]; then
	echo "markserv-apply-template: source template not found: ${SRC}" >&2
	exit 0
fi

# ---------------------------------------------------------------------------
# Guard: destination directory must exist
# ---------------------------------------------------------------------------
TPL_DIR="$(dirname "${TPL}")"
if [ ! -d "${TPL_DIR}" ]; then
	echo "markserv-apply-template: markserv template directory not found: ${TPL_DIR}" >&2
	exit 0
fi

# ---------------------------------------------------------------------------
# Backup original once
# ---------------------------------------------------------------------------
if [ -f "${TPL}" ] && [ ! -f "${TPL}.orig" ]; then
	cp -p "${TPL}" "${TPL}.orig" || true
fi

# ---------------------------------------------------------------------------
# No-op if already applied
# ---------------------------------------------------------------------------
if cmp -s "${SRC}" "${TPL}"; then
	exit 0
fi

# ---------------------------------------------------------------------------
# Apply template (best-effort)
# ---------------------------------------------------------------------------
if ! cp -f "${SRC}" "${TPL}"; then
	echo "markserv-apply-template: failed to copy template to ${TPL} (permission denied?)" >&2
fi

exit 0
