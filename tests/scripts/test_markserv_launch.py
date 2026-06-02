"""Tests for scripts/markserv-launch.sh and systemd/claude-soma-markserv.service."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCH_SCRIPT = REPO_ROOT / "scripts" / "markserv-launch.sh"
SERVICE_FILE = REPO_ROOT / "systemd" / "claude-soma-markserv.service"


def test_bash_syntax_valid() -> None:
    result = subprocess.run(
        ["bash", "-n", str(LAUNCH_SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_defaults_relay_and_18081() -> None:
    content = LAUNCH_SCRIPT.read_text()
    assert "/var/lib/claude-soma/relay" in content
    assert "18081" in content
    assert "--silent" in content
    assert "/var/lib/claude-soma/staging" not in content
    assert "18080" not in content


def test_systemd_unit_workingdir_relay() -> None:
    content = SERVICE_FILE.read_text()
    assert "WorkingDirectory=/var/lib/claude-soma/relay" in content
    assert "Restart=always" in content


def test_systemd_analyze_verify() -> None:
    if shutil.which("systemd-analyze") is None:
        pytest.skip("systemd-analyze not available")
    result = subprocess.run(
        ["systemd-analyze", "verify", str(SERVICE_FILE)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr + result.stdout
