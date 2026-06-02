"""Tests for scripts/listener-healthcheck.sh and its systemd units."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "listener-healthcheck.sh"
SERVICE_FILE = REPO_ROOT / "systemd" / "claude-soma-listener-healthcheck.service"
TIMER_FILE = REPO_ROOT / "systemd" / "claude-soma-listener-healthcheck.timer"


def test_bash_syntax_valid() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_script_has_executable_bit() -> None:
    assert os.access(str(SCRIPT), os.X_OK)


def test_state_file_path_present() -> None:
    content = SCRIPT.read_text()
    assert "/var/lib/claude-soma/listener-healthcheck.state" in content


def test_health_endpoint_present() -> None:
    content = SCRIPT.read_text()
    assert "http://127.0.0.1:9100/health" in content


def test_telegram_curl_present() -> None:
    content = SCRIPT.read_text()
    assert "api.telegram.org/bot" in content
    assert "sendMessage" in content


def test_rate_limit_via_state_file(tmp_path: Path) -> None:
    state_file = tmp_path / "healthcheck.state"
    curl_log = tmp_path / "curl.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    mock_curl = bin_dir / "curl"
    mock_curl.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$@\" >> {curl_log}\n"
        "if printf '%s\\n' \"$@\" | grep -q '9100/health'; then\n"
        "    exit 1\n"
        "fi\n"
        "exit 0\n"
    )
    mock_curl.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + ":" + env["PATH"]
    env["LISTENER_HEALTHCHECK_STATE"] = str(state_file)

    result1 = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
    )
    result2 = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result1.returncode == 0, result1.stderr
    assert result2.returncode == 0, result2.stderr
    assert state_file.exists(), "state file should exist after first failed run"

    log_lines = curl_log.read_text().splitlines()
    health_calls = [ln for ln in log_lines if "9100/health" in ln]
    telegram_calls = [ln for ln in log_lines if "sendMessage" in ln]

    assert len(health_calls) == 2, f"Expected 2 health calls, got {len(health_calls)}: {health_calls}"
    assert len(telegram_calls) == 1, f"Expected 1 telegram call, got {len(telegram_calls)}: {telegram_calls}"


def test_success_path_with_whitespaced_json_response(tmp_path: Path) -> None:
    """Listener healthy: curl returns whitespace-padded JSON; script exits 0, no state file, no alert."""
    state_file = tmp_path / "healthcheck.state"
    curl_log = tmp_path / "curl.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    mock_curl = bin_dir / "curl"
    mock_curl.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$@\" >> {curl_log}\n"
        "if printf '%s\\n' \"$@\" | grep -q '9100/health'; then\n"
        "    echo '{\"status\": \"ok\", \"listener\": \"running\"}'\n"
        "    exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    mock_curl.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + ":" + env["PATH"]
    env["LISTENER_HEALTHCHECK_STATE"] = str(state_file)

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert not state_file.exists(), "state file must not be created when listener is healthy"
    log_content = curl_log.read_text() if curl_log.exists() else ""
    assert "sendMessage" not in log_content, f"sendMessage must not be called on success: {log_content!r}"


def test_success_path_with_no_whitespace_json_response(tmp_path: Path) -> None:
    """Listener healthy: curl returns compact JSON (no spaces); patched grep handles both shapes."""
    state_file = tmp_path / "healthcheck.state"
    curl_log = tmp_path / "curl.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    mock_curl = bin_dir / "curl"
    mock_curl.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$@\" >> {curl_log}\n"
        "if printf '%s\\n' \"$@\" | grep -q '9100/health'; then\n"
        "    echo '{\"status\":\"ok\",\"listener\":\"running\"}'\n"
        "    exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    mock_curl.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + ":" + env["PATH"]
    env["LISTENER_HEALTHCHECK_STATE"] = str(state_file)

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert not state_file.exists(), "state file must not be created when listener is healthy"
    log_content = curl_log.read_text() if curl_log.exists() else ""
    assert "sendMessage" not in log_content, f"sendMessage must not be called on success: {log_content!r}"


def test_systemd_unit_workingdir_or_exec_path() -> None:
    content = SERVICE_FILE.read_text()
    assert "ExecStart=/opt/claude-soma/scripts/listener-healthcheck.sh" in content
    assert "User=ubuntu" in content


def test_systemd_timer_schedule() -> None:
    content = TIMER_FILE.read_text()
    assert "OnUnitActiveSec=5min" in content


def test_systemd_analyze_verify() -> None:
    try:
        result = subprocess.run(
            ["systemd-analyze", "verify", str(SERVICE_FILE), str(TIMER_FILE)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        combined = result.stdout + result.stderr
        if result.returncode != 0:
            deployment_errors = (
                "No such file or directory" in combined
                or "not executable" in combined
                or "EnvironmentFile" in combined
            )
            if deployment_errors:
                pytest.skip(
                    "systemd-analyze verify skipped: deployment paths absent "
                    f"(ExecStart not installed under /opt); stderr: {result.stderr.strip()!r}"
                )
            assert False, "systemd-analyze verify failed (unit syntax error):\n" + combined
    except FileNotFoundError:
        pytest.skip("systemd-analyze not available in this environment")
