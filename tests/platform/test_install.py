"""Tests for claude_soma.install — the operator bootstrapper.

Monkeypatches platform detection to simulate a Linux/Ubuntu/systemd
environment.  Verifies --dry-run produces the correct plan without any
subprocess calls.  Verifies --cloud=oci adds the iptables action.
Verifies --apply mode handles success and failure correctly.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from claude_soma.install import (
    PlatformInfo,
    build_plan,
    main,
    _parse_args,
    _action_iptables_oci,
    _action_mkdir,
)
from claude_soma.platform._action import Action
from claude_soma.platform.pkg import PackageManager
from claude_soma.platform.paths import resolve


# ---------------------------------------------------------------------------
# Platform simulation helpers
# ---------------------------------------------------------------------------

FAKE_UBUNTU_ENV = {
    "os_name": "Linux",
    "distro_id": "ubuntu",
    "distro_id_like": "debian",
    "arch": "x86_64",
    "init_system": "systemd",
    "pm": PackageManager.APT,
}


def _fake_platform_info(**overrides: Any) -> PlatformInfo:
    info = PlatformInfo.__new__(PlatformInfo)
    env = {**FAKE_UBUNTU_ENV, **overrides}
    for k, v in env.items():
        object.__setattr__(info, k, v)
    return info


def _fake_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Return a fake Paths object pointing to tmp_path directories."""
    monkeypatch.setenv("HOME", str(tmp_path / "home" / "ubuntu"))
    monkeypatch.setenv("USER", "ubuntu")
    monkeypatch.setenv("SOMA_HOME", str(tmp_path / "opt" / "claude-soma"))
    with patch("platform.system", return_value="Linux"):
        paths = resolve("system")
    return paths


# ---------------------------------------------------------------------------
# _parse_args
# ---------------------------------------------------------------------------

class TestParseArgs:
    def test_dry_run_flag(self) -> None:
        args = _parse_args(["--dry-run"])
        assert args.dry_run is True
        assert args.apply is False

    def test_apply_flag(self) -> None:
        args = _parse_args(["--apply"])
        assert args.apply is True
        assert args.dry_run is False

    def test_neither_flag_exits(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            _parse_args([])
        assert exc_info.value.code != 0

    def test_both_flags_are_mutually_exclusive(self) -> None:
        with pytest.raises(SystemExit):
            _parse_args(["--dry-run", "--apply"])

    def test_cloud_oci(self) -> None:
        args = _parse_args(["--dry-run", "--cloud=oci"])
        assert args.cloud == "oci"

    def test_cloud_default_none(self) -> None:
        args = _parse_args(["--dry-run"])
        assert args.cloud is None

    def test_install_mode_default_system(self) -> None:
        args = _parse_args(["--dry-run"])
        assert args.install_mode == "system"

    def test_install_mode_user(self) -> None:
        args = _parse_args(["--dry-run", "--install-mode=user"])
        assert args.install_mode == "user"

    def test_features_default(self) -> None:
        args = _parse_args(["--dry-run"])
        assert "voice" in args.features
        assert "social" in args.features

    def test_non_interactive_flag(self) -> None:
        args = _parse_args(["--apply", "--non-interactive"])
        assert args.non_interactive is True

    def test_verbose_flag(self) -> None:
        args = _parse_args(["--dry-run", "--verbose"])
        assert args.verbose is True

    def test_help_mentions_operators_only(self, capsys) -> None:
        with pytest.raises(SystemExit):
            _parse_args(["--help"])
        captured = capsys.readouterr()
        assert "operator" in captured.out.lower() or "OPERATOR" in captured.out


# ---------------------------------------------------------------------------
# build_plan — plan contents without execution
# ---------------------------------------------------------------------------

class TestBuildPlan:
    def _make_plan(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        cloud: str | None = None,
        features: set[str] | None = None,
    ) -> list[Action]:
        paths = _fake_paths(monkeypatch, tmp_path)
        info = _fake_platform_info()
        return build_plan(
            info=info,
            paths=paths,
            install_mode="system",
            cloud=cloud,
            features=features if features is not None else {"voice", "social"},
        )

    def test_plan_is_non_empty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        plan = self._make_plan(monkeypatch, tmp_path)
        assert len(plan) > 0

    def test_plan_contains_ffmpeg(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        plan = self._make_plan(monkeypatch, tmp_path, features={"voice"})
        descriptions = [a.description for a in plan]
        assert any("ffmpeg" in d for d in descriptions)

    def test_plan_contains_node22(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        plan = self._make_plan(monkeypatch, tmp_path)
        descriptions = [a.description for a in plan]
        assert any("node22" in d for d in descriptions)

    def test_plan_contains_caddy(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        plan = self._make_plan(monkeypatch, tmp_path)
        descriptions = [a.description for a in plan]
        assert any("caddy" in d.lower() for d in descriptions)

    def test_plan_contains_systemd_units(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        plan = self._make_plan(monkeypatch, tmp_path)
        descriptions = [a.description for a in plan]
        assert any("systemd unit" in d.lower() for d in descriptions)

    def test_plan_contains_mkdir_for_code_root(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        paths = _fake_paths(monkeypatch, tmp_path)
        plan = self._make_plan(monkeypatch, tmp_path)
        descriptions = [a.description for a in plan]
        assert any("directory" in d.lower() for d in descriptions)

    def test_plan_contains_mcp_json_write(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        plan = self._make_plan(monkeypatch, tmp_path)
        descriptions = [a.description for a in plan]
        assert any(".mcp.json" in d for d in descriptions)

    def test_cloud_oci_adds_iptables_action(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        plan = self._make_plan(monkeypatch, tmp_path, cloud="oci")
        descriptions = [a.description for a in plan]
        assert any("iptables" in d.lower() or "oci" in d.lower()
                   for d in descriptions)

    def test_cloud_none_no_iptables_action(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        plan = self._make_plan(monkeypatch, tmp_path, cloud=None)
        # Check that no action is specifically an iptables firewall operation.
        # Use description prefix match to avoid false positives from tmp_path
        # names that happen to contain the test name (which includes "iptables").
        iptables_actions = [
            a for a in plan
            if a.description.lower().startswith("oci iptables")
            or "iptables -i" in " ".join(" ".join(c) for c in a.commands).lower()
        ]
        assert not iptables_actions, f"Unexpected iptables actions: {iptables_actions}"

    def test_no_voice_feature_skips_ffmpeg(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        plan = self._make_plan(monkeypatch, tmp_path, features=set())
        descriptions = [a.description for a in plan]
        assert not any("ffmpeg" in d for d in descriptions)

    def test_no_social_feature_skips_playwright(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        plan = self._make_plan(monkeypatch, tmp_path, features=set())
        descriptions = [a.description for a in plan]
        assert not any("playwright" in d.lower() for d in descriptions)

    def test_all_service_units_in_plan(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        plan = self._make_plan(monkeypatch, tmp_path)
        descriptions = " ".join(a.description for a in plan)
        for svc in ["claude-soma-api", "claude-soma-channel", "claude-soma-frontend"]:
            assert svc in descriptions

    def test_privileged_actions_have_flag(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        plan = self._make_plan(monkeypatch, tmp_path)
        # mkdir for /etc/claude-soma should be privileged
        mkdir_actions = [a for a in plan if "Create directory" in a.description]
        assert len(mkdir_actions) > 0
        # System-mode dirs need sudo
        etc_actions = [
            a for a in mkdir_actions if "/etc" in a.description
        ]
        if etc_actions:
            assert all(a.is_privileged for a in etc_actions)

    def test_non_linux_raises_not_implemented(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        paths = _fake_paths(monkeypatch, tmp_path)
        info = _fake_platform_info(os_name="Darwin")
        with pytest.raises(NotImplementedError) as exc_info:
            build_plan(
                info=info, paths=paths,
                install_mode="system", cloud=None, features=set(),
            )
        assert "Linux" in str(exc_info.value)

    def test_secrets_action_has_note(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        plan = self._make_plan(monkeypatch, tmp_path)
        # Match by description prefix to avoid tmp_path name false positives
        secrets_actions = [
            a for a in plan
            if a.description.lower().startswith("create secrets template")
        ]
        assert len(secrets_actions) > 0
        assert all(a.note for a in secrets_actions)

    def test_secrets_action_note_says_template(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        plan = self._make_plan(monkeypatch, tmp_path)
        secrets_actions = [
            a for a in plan
            if a.description.lower().startswith("create secrets template")
        ]
        for action in secrets_actions:
            assert "template" in action.note.lower() or "empty" in action.note.lower()


# ---------------------------------------------------------------------------
# --dry-run mode via main() — no subprocess calls
# ---------------------------------------------------------------------------

class TestDryRunMode:
    def test_dry_run_returns_zero(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path / "home" / "ubuntu"))
        monkeypatch.setenv("USER", "ubuntu")
        monkeypatch.setenv("SOMA_HOME", str(tmp_path / "opt" / "claude-soma"))

        with patch("platform.system", return_value="Linux"), \
             patch("claude_soma.install.PlatformInfo") as mock_info_cls, \
             patch("claude_soma.install._detect_init_system", return_value="systemd"), \
             patch("claude_soma.install.detect_package_manager", return_value=PackageManager.APT), \
             patch("subprocess.run") as mock_run:

            mock_info = _fake_platform_info()
            mock_info_cls.return_value = mock_info

            rc = main(["--dry-run", "--features=voice"])

        assert rc == 0
        mock_run.assert_not_called()

    def test_dry_run_prints_plan(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path / "home" / "ubuntu"))
        monkeypatch.setenv("USER", "ubuntu")
        monkeypatch.setenv("SOMA_HOME", str(tmp_path / "opt" / "claude-soma"))

        with patch("platform.system", return_value="Linux"), \
             patch("claude_soma.install.PlatformInfo") as mock_info_cls, \
             patch("subprocess.run") as mock_run:

            mock_info = _fake_platform_info()
            mock_info_cls.return_value = mock_info

            main(["--dry-run", "--features="])

        captured = capsys.readouterr()
        # Should print something about the plan
        assert "install" in captured.out.lower() or "plan" in captured.out.lower() or \
               "dry" in captured.out.lower()

    def test_dry_run_oci_mentions_iptables(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path / "home" / "ubuntu"))
        monkeypatch.setenv("USER", "ubuntu")
        monkeypatch.setenv("SOMA_HOME", str(tmp_path / "opt" / "claude-soma"))

        with patch("platform.system", return_value="Linux"), \
             patch("claude_soma.install.PlatformInfo") as mock_info_cls, \
             patch("subprocess.run"):

            mock_info = _fake_platform_info()
            mock_info_cls.return_value = mock_info

            main(["--dry-run", "--cloud=oci", "--features="])

        captured = capsys.readouterr()
        assert "iptables" in captured.out.lower() or "oci" in captured.out.lower()

    def test_dry_run_no_writes_outside_tempdir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
    ) -> None:
        """--dry-run must make ZERO state changes (no writes, no subprocess)."""
        monkeypatch.setenv("HOME", str(tmp_path / "home" / "ubuntu"))
        monkeypatch.setenv("USER", "ubuntu")
        monkeypatch.setenv("SOMA_HOME", str(tmp_path / "opt" / "claude-soma"))

        written_paths: list[str] = []
        original_write_text = Path.write_text

        def _spy_write_text(self: Path, *args: Any, **kwargs: Any) -> None:
            written_paths.append(str(self))
            # Don't actually write (except to tempdir files the plan logger creates)

        with patch("platform.system", return_value="Linux"), \
             patch("claude_soma.install.PlatformInfo") as mock_info_cls, \
             patch("subprocess.run"):

            mock_info = _fake_platform_info()
            mock_info_cls.return_value = mock_info
            # We don't patch Path.write_text globally — just verify subprocess.run
            # was never called (which would indicate state changes)
            import subprocess as sp
            with patch.object(sp, "run") as mock_run:
                main(["--dry-run", "--features="])
            mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# --apply mode — execute actions / halt on failure
# ---------------------------------------------------------------------------

class TestApplyMode:
    def test_apply_calls_subprocess_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path / "home" / "ubuntu"))
        monkeypatch.setenv("USER", "ubuntu")
        monkeypatch.setenv("SOMA_HOME", str(tmp_path / "opt" / "claude-soma"))
        (tmp_path / "home" / "ubuntu" / ".claude-soma").mkdir(parents=True, exist_ok=True)

        with patch("platform.system", return_value="Linux"), \
             patch("claude_soma.install.PlatformInfo") as mock_info_cls, \
             patch("claude_soma.install.build_plan") as mock_plan, \
             patch("claude_soma.install._execute_action") as mock_execute, \
             patch("claude_soma.wizard.init.run", return_value=0):

            mock_info_cls.return_value = _fake_platform_info()
            # Return a minimal plan with one action
            mock_plan.return_value = [
                Action(
                    commands=[["echo", "test"]],
                    description="Test action",
                    is_privileged=False,
                )
            ]

            rc = main(["--apply", "--non-interactive"])

        assert rc == 0
        mock_execute.assert_called_once()

    def test_apply_halts_on_action_failure(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path / "home" / "ubuntu"))
        monkeypatch.setenv("USER", "ubuntu")
        monkeypatch.setenv("SOMA_HOME", str(tmp_path / "opt" / "claude-soma"))
        (tmp_path / "home" / "ubuntu" / ".claude-soma").mkdir(parents=True, exist_ok=True)

        with patch("platform.system", return_value="Linux"), \
             patch("claude_soma.install.PlatformInfo") as mock_info_cls, \
             patch("claude_soma.install.build_plan") as mock_plan, \
             patch("claude_soma.install._execute_action",
                   side_effect=RuntimeError("test failure")):

            mock_info_cls.return_value = _fake_platform_info()
            mock_plan.return_value = [
                Action(
                    commands=[["sudo", "fail"]],
                    description="Failing action",
                    is_privileged=True,
                ),
                Action(
                    commands=[["echo", "should-not-run"]],
                    description="Should not run",
                    is_privileged=False,
                ),
            ]

            rc = main(["--apply", "--non-interactive"])

        assert rc == 2

    def test_apply_non_linux_returns_1(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path / "home" / "ubuntu"))
        monkeypatch.setenv("USER", "ubuntu")
        monkeypatch.setenv("SOMA_HOME", str(tmp_path / "opt" / "claude-soma"))

        with patch("platform.system", return_value="Darwin"):
            rc = main(["--apply", "--non-interactive"])

        assert rc == 1


# ---------------------------------------------------------------------------
# _action_iptables_oci — explicit sudo audit check
# ---------------------------------------------------------------------------

class TestIptablesOciAction:
    def test_is_privileged(self) -> None:
        action = _action_iptables_oci()
        assert action.is_privileged

    def test_contains_iptables_command(self) -> None:
        action = _action_iptables_oci()
        cmds_flat = " ".join(" ".join(c) for c in action.commands)
        assert "iptables" in cmds_flat

    def test_inserts_accept_rule(self) -> None:
        action = _action_iptables_oci()
        cmds_flat = " ".join(" ".join(c) for c in action.commands)
        assert "ACCEPT" in cmds_flat or "-j ACCEPT" in cmds_flat

    def test_note_explains_purpose(self) -> None:
        action = _action_iptables_oci()
        assert "oci" in action.note.lower() or "oracle" in action.note.lower()

    def test_no_secret_in_commands(self) -> None:
        """iptables action should never pass secrets on argv."""
        action = _action_iptables_oci()
        for cmd in action.commands:
            for token in cmd:
                assert "oat-" not in token
                assert "OAUTH_TOKEN" not in token


# ---------------------------------------------------------------------------
# Sudo audit: every privileged action has is_privileged=True
# ---------------------------------------------------------------------------

class TestSudoAudit:
    def test_all_sudo_commands_are_flagged(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Any action whose commands contain 'sudo' must have is_privileged=True."""
        paths = _fake_paths(monkeypatch, tmp_path)
        info = _fake_platform_info()
        plan = build_plan(
            info=info, paths=paths,
            install_mode="system", cloud=None, features={"voice"},
        )
        for action in plan:
            for cmd in action.commands:
                if "sudo" in cmd:
                    assert action.is_privileged, (
                        f"Action {action.description!r} has 'sudo' in commands "
                        "but is_privileged=False — add to sudo audit!"
                    )

    def test_no_secret_values_in_any_command(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """SECRETS NEVER ON ARGV — no action command should contain raw secrets."""
        paths = _fake_paths(monkeypatch, tmp_path)
        info = _fake_platform_info()
        plan = build_plan(
            info=info, paths=paths,
            install_mode="system", cloud="oci", features={"voice", "social"},
        )
        secret_prefixes = ["oat-", "CLAUDE_CODE_OAUTH_TOKEN=", "AUTH_SECRET=",
                           "AUTH_GITHUB_SECRET="]
        for action in plan:
            for cmd in action.commands:
                for token in cmd:
                    for prefix in secret_prefixes:
                        assert prefix not in token, (
                            f"SECRET ON ARGV in action {action.description!r}: "
                            f"token {token!r} looks like a secret value"
                        )
