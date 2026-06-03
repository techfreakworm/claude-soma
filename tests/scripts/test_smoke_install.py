"""Tests for scripts/smoke_install.sh — post-install verifier."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "smoke_install.sh"


def test_bash_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_script_has_executable_bit() -> None:
    assert os.access(str(SCRIPT), os.X_OK)


def test_check_function_present() -> None:
    content = SCRIPT.read_text()
    assert "check() {" in content


def test_check_optional_function_present() -> None:
    content = SCRIPT.read_text()
    assert "check_optional() {" in content


def test_summary_exit_code_present() -> None:
    content = SCRIPT.read_text()
    assert "exit 1" in content
    assert "exit 0" in content


def test_all_long_running_services_checked() -> None:
    content = SCRIPT.read_text()
    for service in (
        "claude-soma-api.service",
        "claude-soma-frontend.service",
        "claude-soma-markserv.service",
        "claude-soma-channel.service",
        "caddy.service",
    ):
        assert service in content, f"missing service check: {service}"


def test_all_timers_checked() -> None:
    content = SCRIPT.read_text()
    for timer in (
        "healthcheck",
        "cache-refresh",
        "secrets-backup",
        "pw-refresh",
        "usage-snapshot",
        "rc-url-refresh",
        "idle-reaper",
        "daily-status",
        "listener-healthcheck",
        "engagement-drip",
        "channel-clear",
        "relay-cleanup",
    ):
        assert timer in content, f"missing timer check: {timer}"


def test_required_secrets_keys_checked() -> None:
    content = SCRIPT.read_text()
    for key in (
        "CLAUDE_CODE_OAUTH_TOKEN",
        "AUTH_GITHUB_ID",
        "AUTH_GITHUB_SECRET",
        "HERMES_ALLOWED_GITHUB_HANDLES",
        "NEXTAUTH_SECRET",
        "TELEGRAM_BOT_TOKEN",
        "HERMES_NOTIFY_CHAT_ID",
        "HERMES_FILES_PASSWORD",
    ):
        assert key in content, f"missing secret key check: {key}"


def test_no_secret_value_echo() -> None:
    content = SCRIPT.read_text()
    assert "grep -q" in content, "secrets must be checked with grep -q (silent, no value output)"
    assert "cat /etc/claude-soma/secrets.env" not in content, "must not cat secrets file"
