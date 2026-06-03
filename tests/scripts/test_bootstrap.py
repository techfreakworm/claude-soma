"""tests/scripts/test_bootstrap.py — syntax + content checks for bootstrap.sh."""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
BOOTSTRAP = REPO_ROOT / "scripts" / "bootstrap.sh"
DEPLOY = REPO_ROOT / "scripts" / "deploy.sh"
DAILY_STATUS_SERVICE = REPO_ROOT / "systemd" / "claude-soma-daily-status.service"

TIMER_NAMES = [
    "claude-soma-listener-healthcheck.timer",
    "claude-soma-engagement-drip.timer",
    "claude-soma-channel-clear.timer",
    "claude-soma-daily-status.timer",
    "claude-soma-pw-refresh.timer",
    "claude-soma-usage-snapshot.timer",
    "claude-soma-healthcheck.timer",
    "claude-soma-cache-refresh.timer",
    "claude-soma-secrets-backup.timer",
    "claude-soma-rc-url-refresh.timer",
    "claude-soma-idle-reaper.timer",
    "claude-soma-relay-cleanup.timer",
]


def test_bash_syntax_bootstrap():
    result = subprocess.run(
        ["bash", "-n", str(BOOTSTRAP)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"bash -n scripts/bootstrap.sh failed:\n{result.stderr}"
    )


def test_bash_syntax_deploy():
    result = subprocess.run(
        ["bash", "-n", str(DEPLOY)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"bash -n scripts/deploy.sh failed:\n{result.stderr}"
    )


def test_deploy_has_dev_machine_warning_header():
    content = DEPLOY.read_text()
    assert "DO NOT RUN ON THE VPS" in content, (
        "scripts/deploy.sh is missing the mandatory dev-machine warning header"
    )


def test_bootstrap_has_idempotency_comment():
    content = BOOTSTRAP.read_text()
    assert "IDEMPOTENT" in content, (
        "scripts/bootstrap.sh is missing the IDEMPOTENT comment in its header"
    )


def test_bootstrap_installs_markserv_pinned():
    content = BOOTSTRAP.read_text()
    assert "markserv@1.17.4" in content, (
        "scripts/bootstrap.sh must install markserv at the pinned version @1.17.4"
    )


def test_bootstrap_creates_engagement_dir():
    content = BOOTSTRAP.read_text()
    assert "/var/lib/claude-soma/engagement" in content, (
        "scripts/bootstrap.sh must create /var/lib/claude-soma/engagement"
    )


def test_bootstrap_enables_all_timer_units():
    content = BOOTSTRAP.read_text()
    missing = [t for t in TIMER_NAMES if t not in content]
    assert not missing, (
        f"scripts/bootstrap.sh is missing these timer names: {missing}"
    )


def test_systemd_daily_status_service_in_repo():
    assert DAILY_STATUS_SERVICE.exists(), (
        "systemd/claude-soma-daily-status.service is not committed to the repo"
    )
