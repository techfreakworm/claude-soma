"""Tests for claude_soma.platform.services — service-manager adapter.

Tests:
- SystemdBackend renders correct .service file content
- SystemdBackend.spawn_isolated builds the expected systemd-run argv
- Stub backends (Launchd, OpenRc, Windows) raise NotImplementedError with phase labels
- isolation_strength property returns correct values for each backend
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from claude_soma.platform._action import Action
from claude_soma.platform.services import (
    LaunchdBackend,
    OpenRcBackend,
    Service,
    SystemdBackend,
    WindowsServiceBackend,
    _render_service,
    _render_timer,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def simple_service() -> Service:
    return Service(
        name="test-svc",
        description="Test service description",
        exec_argv=["/usr/bin/python3", "-m", "test_module"],
        env={"FOO": "bar", "BAZ": "qux"},
        work_dir="/opt/test",
        restart_policy="always",
        restart_sec=5,
        log_paths={
            "stdout": "/var/log/test/out.log",
            "stderr": "/var/log/test/err.log",
        },
        user="testuser",
        group="testuser",
        env_file="/etc/test/secrets.env",
    )


@pytest.fixture
def oneshot_service() -> Service:
    return Service(
        name="test-oneshot",
        description="Test oneshot service",
        exec_argv=["/usr/bin/bash", "-c", "echo hello"],
        work_dir="/opt/test",
        restart_policy="no",
        type_="oneshot",
        remain_after_exit=True,
        user="ubuntu",
        group="ubuntu",
    )


@pytest.fixture
def systemd_backend() -> SystemdBackend:
    return SystemdBackend(
        systemctl_bin="/usr/bin/systemctl",
        systemd_run_bin="/usr/bin/systemd-run",
        sudo_bin="/usr/bin/sudo",
        unit_dir="/etc/systemd/system",
    )


# ---------------------------------------------------------------------------
# _render_service — unit file content
# ---------------------------------------------------------------------------

class TestRenderService:
    def test_description_in_content(self, simple_service: Service) -> None:
        content = _render_service(simple_service)
        assert "Test service description" in content

    def test_exec_start_in_content(self, simple_service: Service) -> None:
        content = _render_service(simple_service)
        assert "ExecStart=/usr/bin/python3 -m test_module" in content

    def test_user_and_group(self, simple_service: Service) -> None:
        content = _render_service(simple_service)
        assert "User=testuser" in content
        assert "Group=testuser" in content

    def test_env_vars_in_content(self, simple_service: Service) -> None:
        content = _render_service(simple_service)
        assert "Environment=FOO=bar" in content
        assert "Environment=BAZ=qux" in content

    def test_env_file_in_content(self, simple_service: Service) -> None:
        content = _render_service(simple_service)
        # Leading `-` makes it optional
        assert "EnvironmentFile=-/etc/test/secrets.env" in content

    def test_log_paths_in_content(self, simple_service: Service) -> None:
        content = _render_service(simple_service)
        assert "StandardOutput=append:/var/log/test/out.log" in content
        assert "StandardError=append:/var/log/test/err.log" in content

    def test_restart_policy_in_content(self, simple_service: Service) -> None:
        content = _render_service(simple_service)
        assert "Restart=always" in content

    def test_working_directory_in_content(self, simple_service: Service) -> None:
        content = _render_service(simple_service)
        assert "WorkingDirectory=/opt/test" in content

    def test_type_simple(self, simple_service: Service) -> None:
        content = _render_service(simple_service)
        assert "Type=simple" in content

    def test_type_oneshot(self, oneshot_service: Service) -> None:
        content = _render_service(oneshot_service)
        assert "Type=oneshot" in content

    def test_remain_after_exit(self, oneshot_service: Service) -> None:
        content = _render_service(oneshot_service)
        assert "RemainAfterExit=yes" in content

    def test_no_remain_after_exit_by_default(self, simple_service: Service) -> None:
        content = _render_service(simple_service)
        assert "RemainAfterExit" not in content

    def test_after_network_target(self, simple_service: Service) -> None:
        content = _render_service(simple_service)
        assert "network-online.target" in content

    def test_install_section(self, simple_service: Service) -> None:
        content = _render_service(simple_service)
        assert "[Install]" in content
        assert "WantedBy=multi-user.target" in content

    def test_no_secret_in_content(self, simple_service: Service) -> None:
        """The env_file path is present but no actual secret value."""
        content = _render_service(simple_service)
        # env_file is a PATH, not a secret value
        assert "EnvironmentFile=-/etc/test/secrets.env" in content
        # No '=' after a potential token prefix
        assert "oat-" not in content
        assert "CLAUDE_CODE_OAUTH_TOKEN=" not in content


# ---------------------------------------------------------------------------
# SystemdBackend.install_service
# ---------------------------------------------------------------------------

class TestSystemdBackendInstallService:
    def test_returns_action(self, systemd_backend: SystemdBackend, simple_service: Service) -> None:
        action = systemd_backend.install_service(simple_service)
        assert isinstance(action, Action)

    def test_action_is_privileged(self, systemd_backend: SystemdBackend, simple_service: Service) -> None:
        action = systemd_backend.install_service(simple_service)
        assert action.is_privileged

    def test_writes_contains_unit_path(self, systemd_backend: SystemdBackend, simple_service: Service) -> None:
        action = systemd_backend.install_service(simple_service)
        paths_written = [w[0] for w in action.writes]
        assert any("test-svc.service" in p for p in paths_written)

    def test_writes_content_is_valid_unit(self, systemd_backend: SystemdBackend, simple_service: Service) -> None:
        action = systemd_backend.install_service(simple_service)
        content = action.writes[0][1]
        assert "[Unit]" in content
        assert "[Service]" in content
        assert "[Install]" in content

    def test_commands_include_daemon_reload(self, systemd_backend: SystemdBackend, simple_service: Service) -> None:
        action = systemd_backend.install_service(simple_service)
        cmds_flat = " ".join(" ".join(c) for c in action.commands)
        assert "daemon-reload" in cmds_flat

    def test_commands_include_sudo_install(self, systemd_backend: SystemdBackend, simple_service: Service) -> None:
        action = systemd_backend.install_service(simple_service)
        first_cmd = action.commands[0]
        # sudo_bin may be full path like /usr/bin/sudo; check any token ends with "sudo"
        assert any("sudo" in token for token in first_cmd)
        assert "install" in first_cmd

    def test_unit_dir_in_command(self, systemd_backend: SystemdBackend, simple_service: Service) -> None:
        action = systemd_backend.install_service(simple_service)
        cmds_flat = " ".join(" ".join(c) for c in action.commands)
        assert "/etc/systemd/system" in cmds_flat

    def test_note_mentions_secrets(self, systemd_backend: SystemdBackend, simple_service: Service) -> None:
        action = systemd_backend.install_service(simple_service)
        assert "SECRETS" in action.note.upper() or "secret" in action.note.lower()


# ---------------------------------------------------------------------------
# SystemdBackend.install_timer
# ---------------------------------------------------------------------------

class TestSystemdBackendInstallTimer:
    def test_returns_action(self, systemd_backend: SystemdBackend) -> None:
        action = systemd_backend.install_timer(
            "test-timer", "*:0/10", service_name="test-svc",
        )
        assert isinstance(action, Action)

    def test_writes_timer_file(self, systemd_backend: SystemdBackend) -> None:
        action = systemd_backend.install_timer(
            "test-timer", "*:0/10", service_name="test-svc",
        )
        paths_written = [w[0] for w in action.writes]
        assert any("test-timer.timer" in p for p in paths_written)

    def test_timer_content_has_on_calendar(self, systemd_backend: SystemdBackend) -> None:
        action = systemd_backend.install_timer(
            "test-timer", "*:0/10", service_name="test-svc",
        )
        content = action.writes[0][1]
        assert "OnCalendar=*:0/10" in content

    def test_timer_content_references_service(self, systemd_backend: SystemdBackend) -> None:
        action = systemd_backend.install_timer(
            "test-timer", "*:0/10", service_name="test-svc",
        )
        content = action.writes[0][1]
        assert "test-svc.service" in content


# ---------------------------------------------------------------------------
# SystemdBackend.enable / restart
# ---------------------------------------------------------------------------

class TestSystemdBackendEnableRestart:
    def test_enable_produces_privileged_action(self, systemd_backend: SystemdBackend) -> None:
        action = systemd_backend.enable("test-svc.service")
        assert action.is_privileged
        cmds_flat = " ".join(" ".join(c) for c in action.commands)
        assert "enable" in cmds_flat
        assert "--now" in cmds_flat

    def test_restart_produces_privileged_action(self, systemd_backend: SystemdBackend) -> None:
        action = systemd_backend.restart("test-svc.service")
        assert action.is_privileged
        cmds_flat = " ".join(" ".join(c) for c in action.commands)
        assert "restart" in cmds_flat


# ---------------------------------------------------------------------------
# SystemdBackend.spawn_isolated — mirrors spawner._wrap_in_transient_unit
# ---------------------------------------------------------------------------

class TestSystemdBackendSpawnIsolated:
    def test_strong_returns_action(self, systemd_backend: SystemdBackend) -> None:
        action = systemd_backend.spawn_isolated(
            name="my-project",
            argv=["/usr/bin/tmux", "new-session", "-d"],
            env={"HOME": "/home/ubuntu"},
            isolation_strength="strong",
        )
        assert isinstance(action, Action)

    def test_strong_uses_sudo_n(self, systemd_backend: SystemdBackend) -> None:
        action = systemd_backend.spawn_isolated(
            name="my-project",
            argv=["/usr/bin/tmux", "new-session", "-d"],
            env={},
            isolation_strength="strong",
        )
        cmd = action.commands[0]
        assert cmd[0].endswith("sudo")
        assert "-n" in cmd

    def test_strong_uses_systemd_run(self, systemd_backend: SystemdBackend) -> None:
        action = systemd_backend.spawn_isolated(
            name="my-project",
            argv=["/usr/bin/tmux"],
            env={},
            isolation_strength="strong",
        )
        cmd = action.commands[0]
        assert any("systemd-run" in arg for arg in cmd)

    def test_strong_sets_unit_name(self, systemd_backend: SystemdBackend) -> None:
        action = systemd_backend.spawn_isolated(
            name="my-project",
            argv=["/usr/bin/tmux"],
            env={},
            isolation_strength="strong",
        )
        cmd = action.commands[0]
        assert "--unit=claude-soma-lead-my-project.service" in cmd

    def test_strong_sets_type_oneshot(self, systemd_backend: SystemdBackend) -> None:
        action = systemd_backend.spawn_isolated(
            name="my-project",
            argv=["/usr/bin/tmux"],
            env={},
            isolation_strength="strong",
        )
        cmd = action.commands[0]
        assert "--property=Type=oneshot" in cmd

    def test_strong_sets_remain_after_exit(self, systemd_backend: SystemdBackend) -> None:
        action = systemd_backend.spawn_isolated(
            name="my-project",
            argv=["/usr/bin/tmux"],
            env={},
            isolation_strength="strong",
        )
        cmd = action.commands[0]
        assert "--property=RemainAfterExit=yes" in cmd

    def test_strong_separator_before_inner_argv(self, systemd_backend: SystemdBackend) -> None:
        action = systemd_backend.spawn_isolated(
            name="my-project",
            argv=["/usr/bin/tmux", "new-session"],
            env={},
            isolation_strength="strong",
        )
        cmd = action.commands[0]
        assert "--" in cmd
        # tmux should appear after --
        dash_idx = cmd.index("--")
        assert "/usr/bin/tmux" in cmd[dash_idx:]

    def test_strong_env_as_setenv(self, systemd_backend: SystemdBackend) -> None:
        action = systemd_backend.spawn_isolated(
            name="my-project",
            argv=["/usr/bin/tmux"],
            env={"HOME": "/home/ubuntu", "PATH": "/usr/bin"},
            isolation_strength="strong",
        )
        cmd = action.commands[0]
        assert "--setenv=HOME=/home/ubuntu" in cmd
        assert "--setenv=PATH=/usr/bin" in cmd

    def test_medium_raises_not_implemented(self, systemd_backend: SystemdBackend) -> None:
        with pytest.raises(NotImplementedError) as exc_info:
            systemd_backend.spawn_isolated(
                name="my-project",
                argv=["/usr/bin/tmux"],
                env={},
                isolation_strength="medium",
            )
        assert "Phase" in str(exc_info.value)

    def test_action_note_mentions_secrets(self, systemd_backend: SystemdBackend) -> None:
        action = systemd_backend.spawn_isolated(
            name="my-project",
            argv=["/usr/bin/tmux"],
            env={},
            isolation_strength="strong",
        )
        assert "SECRETS" in action.note.upper() or "secret" in action.note.lower()
        assert "argv" in action.note.lower()

    def test_isolation_strength_property(self, systemd_backend: SystemdBackend) -> None:
        assert systemd_backend.isolation_strength == "strong"


# ---------------------------------------------------------------------------
# Stub backends — NotImplementedError with phase labels
# ---------------------------------------------------------------------------

class TestLaunchdBackendStubs:
    def setup_method(self) -> None:
        self.backend = LaunchdBackend()

    def test_install_service_raises_phase2(self) -> None:
        svc = Service(
            name="test", description="test", exec_argv=["/bin/true"],
            user="user", group="user",
        )
        with pytest.raises(NotImplementedError) as exc_info:
            self.backend.install_service(svc)
        assert "Phase 2" in str(exc_info.value)

    def test_install_timer_raises_phase2(self) -> None:
        with pytest.raises(NotImplementedError) as exc_info:
            self.backend.install_timer("t", "daily", service_name="svc")
        assert "Phase 2" in str(exc_info.value)

    def test_enable_raises_phase2(self) -> None:
        with pytest.raises(NotImplementedError) as exc_info:
            self.backend.enable("test.service")
        assert "Phase 2" in str(exc_info.value)

    def test_restart_raises_phase2(self) -> None:
        with pytest.raises(NotImplementedError) as exc_info:
            self.backend.restart("test.service")
        assert "Phase 2" in str(exc_info.value)

    def test_spawn_isolated_raises_phase2(self) -> None:
        with pytest.raises(NotImplementedError) as exc_info:
            self.backend.spawn_isolated("name", ["/bin/true"], {}, isolation_strength="medium")
        assert "Phase 2" in str(exc_info.value)

    def test_isolation_strength_is_medium(self) -> None:
        assert self.backend.isolation_strength == "medium"


class TestOpenRcBackendStubs:
    def setup_method(self) -> None:
        self.backend = OpenRcBackend()

    def test_install_service_raises_not_implemented(self) -> None:
        svc = Service(
            name="test", description="test", exec_argv=["/bin/true"],
            user="user", group="user",
        )
        with pytest.raises(NotImplementedError) as exc_info:
            self.backend.install_service(svc)
        assert "Phase 1 degraded" in str(exc_info.value) or \
               "OpenRC" in str(exc_info.value)

    def test_isolation_strength_is_medium(self) -> None:
        assert self.backend.isolation_strength == "medium"


class TestWindowsServiceBackendStubs:
    def setup_method(self) -> None:
        self.backend = WindowsServiceBackend()

    def test_install_service_raises_phase4(self) -> None:
        svc = Service(
            name="test", description="test", exec_argv=["/bin/true"],
            user="user", group="user",
        )
        with pytest.raises(NotImplementedError) as exc_info:
            self.backend.install_service(svc)
        assert "Phase 4" in str(exc_info.value)

    def test_isolation_strength_is_none(self) -> None:
        assert self.backend.isolation_strength == "none"

    def test_mentions_wsl2(self) -> None:
        svc = Service(
            name="test", description="test", exec_argv=["/bin/true"],
            user="user", group="user",
        )
        with pytest.raises(NotImplementedError) as exc_info:
            self.backend.install_service(svc)
        assert "WSL2" in str(exc_info.value)
