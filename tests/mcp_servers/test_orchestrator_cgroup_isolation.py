# tests/mcp_servers/test_orchestrator_cgroup_isolation.py
"""Proves a spawned project lead survives a channel restart.

The bug: every lead used to share one tmux server inside
claude-soma-channel.service's cgroup, so restarting the channel
(KillMode=control-group) SIGKILLed every lead with it.

The fix spawns each lead inside its own transient systemd service, so its tmux
server lands in a SIBLING cgroup (/system.slice/claude-soma-lead-<name>.service).
systemd's KillMode=control-group only reaches a unit's own cgroup subtree, so a
channel restart provably cannot touch a lead in a sibling cgroup -- that
cgroup-membership fact IS the survival proof.

This test deliberately does NOT restart the real channel (that would kill the
live bot and any running leads). It stands up a real throwaway lead through the
actual spawn path and asserts the isolating property directly. It self-skips
where systemd + passwordless sudo are unavailable (CI/dev).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from claude_soma.mcp_servers.project_orchestrator import spawner
from claude_soma.mcp_servers.project_orchestrator.spawner import (
    spawn_background_lead,
    kill_session,
    _lead_unit,
    _lead_socket,
    _session_name,
)


def _passwordless_sudo() -> bool:
    try:
        return subprocess.run(
            ["sudo", "-n", "true"], capture_output=True, timeout=10
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


_SYSTEMD = (
    os.path.isdir("/run/systemd/system")
    and (shutil.which("systemd-run") is not None or os.path.exists("/usr/bin/systemd-run"))
    and _passwordless_sudo()
)

pytestmark = pytest.mark.skipif(
    not _SYSTEMD,
    reason="needs a systemd system manager + passwordless sudo (runs on the VPS)",
)


def _unit_active(unit: str) -> bool:
    return subprocess.run(
        ["systemctl", "is-active", "--quiet", unit], timeout=10
    ).returncode == 0


def _stop_unit(unit: str) -> None:
    for argv in (
        ["sudo", "-n", "systemctl", "stop", unit],
        ["sudo", "-n", "systemctl", "reset-failed", unit],
    ):
        try:
            subprocess.run(argv, capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass


def _pane_pid(socket: str, session: str) -> int | None:
    try:
        out = subprocess.run(
            ["tmux", "-L", socket, "list-panes", "-t", session, "-F", "#{pane_pid}"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return int(out.stdout.strip().splitlines()[0])


def _cgroup_of(pid: int) -> str:
    # cgroup v2 unified line looks like: 0::/system.slice/<unit>
    return Path(f"/proc/{pid}/cgroup").read_text().strip()


def test_spawned_lead_lives_in_its_own_cgroup_not_the_channel(tmp_path, monkeypatch):
    name = f"cgtest-{os.getpid()}"
    unit = _lead_unit(name)
    socket = _lead_socket(name)
    session = _session_name(name)

    # Stub claude: ignore every arg and just sleep, so we exercise the real
    # systemd-run + tmux spawn path without launching real claude (no network,
    # no auth, no PTY UI).
    stub = tmp_path / "claude-stub"
    stub.write_text("#!/bin/bash\nexec sleep 600\n")
    stub.chmod(0o755)

    # Isolate every external side effect onto tmp paths.
    monkeypatch.setattr(spawner, "CLAUDE_BIN", str(stub))
    monkeypatch.setattr(spawner, "RC_URL_POLL_SECONDS", 0)  # don't poll for a URL
    monkeypatch.setenv("HERMES_CLAUDE_GLOBAL_JSON", str(tmp_path / "claude.json"))
    # Tee pane logging into tmp_path, not the real /var/log/claude-soma.
    monkeypatch.setenv("HERMES_LEAD_LOG_DIR", str(tmp_path / "logs"))
    env_file = tmp_path / "lead.env"
    env_file.write_text("CLAUDE_CODE_OAUTH_TOKEN=fake-token-for-test\n")
    monkeypatch.setattr(spawner, "LEAD_ENV_FILE", str(env_file))

    cwd = tmp_path / "proj"

    # Clear any stale unit from a previous aborted run with the same name.
    _stop_unit(unit)

    try:
        result = spawn_background_lead(
            name=name, brief="sleep and stay alive", cwd=cwd,
            permission_mode="acceptEdits",
        )
        assert result["agent_id"] == session

        # The lead's own transient unit is active...
        assert _unit_active(unit), f"{unit} should be active right after spawn"

        # ...and its session is alive on its own dedicated socket.
        assert subprocess.run(
            ["tmux", "-L", socket, "has-session", "-t", session], timeout=10
        ).returncode == 0

        # THE PROOF: the lead's actual process is in the lead's own cgroup, a
        # sibling of (not inside) claude-soma-channel.service. A channel
        # restart (KillMode=control-group) therefore cannot reach it.
        pid = None
        cgroup = ""
        for _ in range(50):
            pid = _pane_pid(socket, session)
            if pid is not None:
                cgroup = _cgroup_of(pid)
                if cgroup:
                    break
            time.sleep(0.1)
        assert pid is not None, "could not find the lead's pane process"
        assert cgroup.endswith(f"claude-soma-lead-{name}.service"), (
            f"lead pid {pid} is in cgroup {cgroup!r}, expected its own "
            f"claude-soma-lead-{name}.service scope"
        )
        assert "claude-soma-channel.service" not in cgroup, (
            f"lead pid {pid} is still inside the channel cgroup ({cgroup!r}) -- "
            "a channel restart would kill it"
        )
    finally:
        # Tear down through the real kill path, then belt-and-suspenders.
        kill_session(name)
        _stop_unit(unit)
        try:
            subprocess.run(
                ["tmux", "-L", socket, "kill-server"],
                capture_output=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    # After teardown the unit is gone (proves kill_session cleans up the cgroup).
    assert not _unit_active(unit)
