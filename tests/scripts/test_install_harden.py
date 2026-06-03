"""tests/scripts/test_install_harden.py — fresh-VPS install hardening assertions.

Covers:
  - Bug 2 (ownership): as_ubuntu helper + defensive chown in bootstrap.sh
  - Bug 3 (Caddy ordering): non-fatal Caddy reload at step 13 + finalize-caddy.sh
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
BOOTSTRAP = REPO_ROOT / "scripts" / "bootstrap.sh"
FINALIZE_CADDY = REPO_ROOT / "scripts" / "finalize-caddy.sh"
SMOKE_INSTALL = REPO_ROOT / "scripts" / "smoke_install.sh"
ENV_COPILOT = REPO_ROOT / "scripts" / "env-copilot-prompt.txt"
INSTALL_MD = REPO_ROOT / "INSTALL.md"


# ---------------------------------------------------------------------------
# bootstrap.sh — ownership model
# ---------------------------------------------------------------------------


def test_bootstrap_has_as_ubuntu_helper():
    content = BOOTSTRAP.read_text()
    assert "as_ubuntu()" in content, (
        "scripts/bootstrap.sh must define the as_ubuntu() helper function"
    )


def test_bootstrap_step_5_uses_as_ubuntu_for_pip():
    content = BOOTSTRAP.read_text()
    assert "as_ubuntu" in content and ".venv/bin/pip" in content, (
        "scripts/bootstrap.sh step 5 must use as_ubuntu for pip installs"
    )
    # Confirm as_ubuntu wraps the pip invocation (not just present elsewhere)
    lines = content.splitlines()
    pip_with_as_ubuntu = any(
        "as_ubuntu" in line and ".venv/bin/pip" in line for line in lines
    )
    assert pip_with_as_ubuntu, (
        "scripts/bootstrap.sh must have a line using as_ubuntu with .venv/bin/pip"
    )


def test_bootstrap_step_7_uses_as_ubuntu():
    content = BOOTSTRAP.read_text()
    lines = content.splitlines()
    build_with_as_ubuntu = any(
        "as_ubuntu" in line and "build_frontend.sh" in line for line in lines
    )
    assert build_with_as_ubuntu, (
        "scripts/bootstrap.sh step 7 must call as_ubuntu bash build_frontend.sh"
    )


def test_bootstrap_defensive_chown_present():
    content = BOOTSTRAP.read_text()
    assert "chown -R ubuntu:ubuntu /opt/claude-soma" in content, (
        "scripts/bootstrap.sh must contain a defensive chown -R ubuntu:ubuntu /opt/claude-soma"
    )


# ---------------------------------------------------------------------------
# bootstrap.sh — Caddy ordering (non-fatal reload)
# ---------------------------------------------------------------------------


def test_bootstrap_caddy_step_non_fatal():
    content = BOOTSTRAP.read_text()
    # The caddy reload must NOT hard-exit on failure.
    # A bare `|| exit` or `|| exit 1` after systemctl reload is the fail pattern.
    # The safe pattern uses `if ! systemctl reload ...` so no exit on failure.
    assert "systemctl reload caddy.service || exit" not in content, (
        "scripts/bootstrap.sh must not hard-exit on Caddy reload failure (use if/else)"
    )
    assert "systemctl reload caddy.service || exit 1" not in content, (
        "scripts/bootstrap.sh must not hard-exit on Caddy reload failure (use if/else)"
    )
    # Confirm the non-fatal warning text is present
    assert "NON-FATAL" in content, (
        "scripts/bootstrap.sh must print a NON-FATAL warning when Caddy reload fails"
    )


def test_bootstrap_bash_syntax():
    result = subprocess.run(
        ["bash", "-n", str(BOOTSTRAP)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"bash -n scripts/bootstrap.sh failed:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# scripts/finalize-caddy.sh
# ---------------------------------------------------------------------------


def test_finalize_caddy_exists():
    assert FINALIZE_CADDY.exists(), "scripts/finalize-caddy.sh must exist"
    assert os.access(FINALIZE_CADDY, os.X_OK), (
        "scripts/finalize-caddy.sh must be executable (chmod 755)"
    )


def test_finalize_caddy_bash_syntax():
    result = subprocess.run(
        ["bash", "-n", str(FINALIZE_CADDY)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"bash -n scripts/finalize-caddy.sh failed:\n{result.stderr}"
    )


def test_finalize_caddy_uses_secrets_env():
    content = FINALIZE_CADDY.read_text()
    assert "/etc/claude-soma/secrets.env" in content, (
        "scripts/finalize-caddy.sh must source /etc/claude-soma/secrets.env"
    )


def test_finalize_caddy_renders_bcrypt():
    content = FINALIZE_CADDY.read_text()
    assert "caddy hash-password" in content, (
        "scripts/finalize-caddy.sh must generate bcrypt hash via caddy hash-password"
    )


def test_finalize_caddy_validates_before_reload():
    content = FINALIZE_CADDY.read_text()
    assert "caddy validate" in content, (
        "scripts/finalize-caddy.sh must run caddy validate before reload"
    )
    # validate must appear before reload in the file
    validate_pos = content.index("caddy validate")
    reload_pos = content.index("systemctl reload caddy")
    assert validate_pos < reload_pos, (
        "scripts/finalize-caddy.sh must run caddy validate BEFORE systemctl reload caddy"
    )


def test_finalize_caddy_replaces_domain():
    content = FINALIZE_CADDY.read_text()
    assert "SOMA_DOMAIN" in content, (
        "scripts/finalize-caddy.sh must use SOMA_DOMAIN to render the site configs"
    )
    assert "FILES_DOMAIN" in content, (
        "scripts/finalize-caddy.sh must set or use FILES_DOMAIN"
    )


# ---------------------------------------------------------------------------
# scripts/env-copilot-prompt.txt
# ---------------------------------------------------------------------------


def test_env_copilot_references_finalize_caddy():
    content = ENV_COPILOT.read_text()
    assert "finalize-caddy.sh" in content, (
        "scripts/env-copilot-prompt.txt must reference finalize-caddy.sh"
    )


# ---------------------------------------------------------------------------
# scripts/smoke_install.sh
# ---------------------------------------------------------------------------


def test_smoke_install_checks_opt_ownership():
    content = SMOKE_INSTALL.read_text()
    assert "/opt/claude-soma" in content and "ubuntu" in content, (
        "scripts/smoke_install.sh must check /opt/claude-soma ownership"
    )
    # The specific ownership check pattern
    assert "stat -c '%U' /opt/claude-soma" in content or \
           "stat -c" in content and "/opt/claude-soma" in content, (
        "scripts/smoke_install.sh must use stat to verify /opt/claude-soma is ubuntu-owned"
    )


def test_smoke_install_bash_syntax():
    result = subprocess.run(
        ["bash", "-n", str(SMOKE_INSTALL)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"bash -n scripts/smoke_install.sh failed:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# INSTALL.md
# ---------------------------------------------------------------------------


def test_install_md_references_finalize_caddy():
    content = INSTALL_MD.read_text()
    assert "finalize-caddy.sh" in content, (
        "INSTALL.md must reference scripts/finalize-caddy.sh"
    )
