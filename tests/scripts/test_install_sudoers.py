"""tests/scripts/test_install_sudoers.py — sudoers grant for lead spawn (INSTALL-BUG-14).

Covers:
  - systemd/sudoers.d/99-claude-soma-spawner exists and is visudo-clean
  - sudoers file grants the right commands (systemd-run, systemctl stop/reset-failed)
  - scripts/bootstrap.sh installs the sudoers file + validates with visudo -cf
  - INSTALL.md documents the sudoers grant
  - scripts/smoke_install.sh checks the sudoers file presence + permissions
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
SUDOERS_FILE = REPO_ROOT / "systemd" / "sudoers.d" / "99-claude-soma-spawner"
BOOTSTRAP = REPO_ROOT / "scripts" / "bootstrap.sh"
SMOKE_INSTALL = REPO_ROOT / "scripts" / "smoke_install.sh"
INSTALL_MD = REPO_ROOT / "INSTALL.md"


# ---------------------------------------------------------------------------
# systemd/sudoers.d/99-claude-soma-spawner — file existence + content
# ---------------------------------------------------------------------------


def test_sudoers_file_exists():
    assert SUDOERS_FILE.exists(), (
        "systemd/sudoers.d/99-claude-soma-spawner must exist — "
        "it is installed to /etc/sudoers.d/ by bootstrap to grant ubuntu "
        "passwordless sudo for systemd-run lead spawning"
    )


@pytest.mark.skipif(shutil.which("visudo") is None, reason="visudo not installed")
def test_sudoers_file_visudo_clean():
    result = subprocess.run(
        ["visudo", "-cf", str(SUDOERS_FILE)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"visudo -cf systemd/sudoers.d/99-claude-soma-spawner failed:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_sudoers_grants_systemd_run():
    content = SUDOERS_FILE.read_text()
    assert "systemd-run" in content, (
        "systemd/sudoers.d/99-claude-soma-spawner must grant ubuntu "
        "passwordless sudo for /usr/bin/systemd-run (lead spawn path)"
    )


def test_sudoers_grants_systemctl_stop_lead():
    content = SUDOERS_FILE.read_text()
    assert "systemctl stop claude-soma-lead-" in content, (
        "systemd/sudoers.d/99-claude-soma-spawner must grant ubuntu "
        "passwordless sudo for 'systemctl stop claude-soma-lead-*' (lead kill path)"
    )


def test_sudoers_grants_systemctl_reset_failed_lead():
    content = SUDOERS_FILE.read_text()
    assert "systemctl reset-failed claude-soma-lead-" in content, (
        "systemd/sudoers.d/99-claude-soma-spawner must grant ubuntu "
        "passwordless sudo for 'systemctl reset-failed claude-soma-lead-*' "
        "(post-kill cleanup path)"
    )


# ---------------------------------------------------------------------------
# scripts/bootstrap.sh — installs the sudoers file with visudo validation
# ---------------------------------------------------------------------------


def test_bootstrap_installs_sudoers():
    content = BOOTSTRAP.read_text()
    assert "99-claude-soma-spawner" in content, (
        "scripts/bootstrap.sh must install 99-claude-soma-spawner to /etc/sudoers.d/"
    )


def test_bootstrap_validates_sudoers_with_visudo():
    content = BOOTSTRAP.read_text()
    assert "visudo -cf" in content, (
        "scripts/bootstrap.sh must validate the sudoers source file with "
        "'visudo -cf' before installing it — a broken sudoers file can lock out root"
    )


# ---------------------------------------------------------------------------
# INSTALL.md — documents the sudoers grant
# ---------------------------------------------------------------------------


def test_install_md_documents_sudoers():
    content = INSTALL_MD.read_text()
    assert "99-claude-soma-spawner" in content or "sudoers" in content.lower(), (
        "INSTALL.md must document the sudoers grant for lead orchestration "
        "(99-claude-soma-spawner or 'sudoers' mention)"
    )


# ---------------------------------------------------------------------------
# scripts/smoke_install.sh — checks sudoers file presence + permissions
# ---------------------------------------------------------------------------


def test_smoke_install_checks_sudoers():
    content = SMOKE_INSTALL.read_text()
    assert "99-claude-soma-spawner" in content, (
        "scripts/smoke_install.sh must check that /etc/sudoers.d/99-claude-soma-spawner "
        "is present"
    )
