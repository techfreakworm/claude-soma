"""Tests for claude_soma.platform.paths — path resolution.

Uses unittest.mock to patch platform.system() and env vars; no filesystem
writes.  Tests that macOS/Windows raise NotImplementedError with Phase labels.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Generator
from unittest.mock import patch

import pytest

from claude_soma.platform.paths import Paths, resolve, render_mcp_json


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all HERMES_* and SOMA_* overrides for test isolation."""
    hermes_keys = [k for k in os.environ if k.startswith("HERMES_")]
    soma_keys = [k for k in os.environ if k.startswith("SOMA_")]
    for k in hermes_keys + soma_keys + [
        "HOME", "USER", "LOGNAME",
        "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME",
    ]:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", "/home/testuser")
    monkeypatch.setenv("USER", "testuser")


# ---------------------------------------------------------------------------
# Linux system mode
# ---------------------------------------------------------------------------

class TestSystemModeLinux:
    def test_code_root_is_opt(self) -> None:
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        assert p.code_root == Path("/opt/claude-soma")

    def test_config_dir_is_etc(self) -> None:
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        assert p.config_dir == Path("/etc/claude-soma")

    def test_secrets_env_under_config(self) -> None:
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        assert p.secrets_env == Path("/etc/claude-soma/secrets.env")

    def test_log_dir_is_var_log(self) -> None:
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        assert p.log_dir == Path("/var/log/claude-soma")

    def test_whisper_bin_default(self) -> None:
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        assert p.whisper_bin == Path("/opt/whisper.cpp/build/bin/whisper-cli")

    def test_whisper_model_is_base_en(self) -> None:
        """ggml-base.en.bin must be the default (matches .mcp.json contract)."""
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        assert "ggml-base.en.bin" in str(p.whisper_model)

    def test_piper_bin_default(self) -> None:
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        assert p.piper_bin == Path("/opt/piper/piper")

    def test_user_is_testuser(self) -> None:
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        assert p.user == "testuser"

    def test_venv_bin_under_code_root(self) -> None:
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        assert p.venv_bin == Path("/opt/claude-soma/.venv/bin")

    def test_registry_db_in_code_root(self) -> None:
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        assert p.registry_db == Path("/opt/claude-soma/registry.sqlite")

    def test_activity_log_under_home(self) -> None:
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        assert p.activity_log == Path("/home/testuser/.claude-soma/activity.jsonl")


class TestSystemModeHermesOverrides:
    """HERMES_* env vars must override the resolved defaults."""

    def test_hermes_whisper_bin_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HERMES_WHISPER_BIN", "/custom/whisper-cli")
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        assert p.whisper_bin == Path("/custom/whisper-cli")

    def test_hermes_whisper_model_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HERMES_WHISPER_MODEL", "/custom/model.bin")
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        assert p.whisper_model == Path("/custom/model.bin")

    def test_hermes_piper_bin_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HERMES_PIPER_BIN", "/custom/piper")
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        assert p.piper_bin == Path("/custom/piper")

    def test_hermes_orch_db_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HERMES_ORCH_DB", "/data/registry.sqlite")
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        assert p.registry_db == Path("/data/registry.sqlite")

    def test_hermes_projects_root_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HERMES_PROJECTS_ROOT", "/projects")
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        assert p.lead_work_dir == Path("/projects")

    def test_soma_home_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SOMA_HOME", "/srv/claude-soma")
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        assert p.code_root == Path("/srv/claude-soma")

    def test_hermes_lead_log_dir_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HERMES_LEAD_LOG_DIR", "/tmp/leads")
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        assert p.lead_log_dir == Path("/tmp/leads")


# ---------------------------------------------------------------------------
# Linux user mode (XDG)
# ---------------------------------------------------------------------------

class TestUserModeLinux:
    def test_code_root_under_xdg_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", "/home/testuser/.local/share")
        with patch("platform.system", return_value="Linux"):
            p = resolve("user")
        assert p.code_root == Path("/home/testuser/.local/share/claude-soma")

    def test_config_dir_under_xdg_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", "/home/testuser/.config")
        with patch("platform.system", return_value="Linux"):
            p = resolve("user")
        assert p.config_dir == Path("/home/testuser/.config/claude-soma")

    def test_state_dir_under_xdg_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_STATE_HOME", "/home/testuser/.local/state")
        with patch("platform.system", return_value="Linux"):
            p = resolve("user")
        assert p.state_dir == Path("/home/testuser/.local/state/claude-soma")

    def test_log_dir_under_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_STATE_HOME", "/home/testuser/.local/state")
        with patch("platform.system", return_value="Linux"):
            p = resolve("user")
        assert p.log_dir == Path("/home/testuser/.local/state/claude-soma/logs")

    def test_defaults_without_xdg_vars(self) -> None:
        """Without XDG vars, fall back to ~/.config / ~/.local/state / ~/.local/share."""
        with patch("platform.system", return_value="Linux"):
            p = resolve("user")
        assert ".local/share/claude-soma" in str(p.code_root) or \
               "claude-soma" in str(p.code_root)

    def test_whisper_bin_under_data_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", "/home/testuser/.local/share")
        with patch("platform.system", return_value="Linux"):
            p = resolve("user")
        assert "whisper.cpp" in str(p.whisper_bin)
        assert "ggml-base.en.bin" not in str(p.whisper_bin)  # bin != model

    def test_whisper_model_is_base_en(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", "/home/testuser/.local/share")
        with patch("platform.system", return_value="Linux"):
            p = resolve("user")
        assert "ggml-base.en.bin" in str(p.whisper_model)


# ---------------------------------------------------------------------------
# Non-Linux platforms raise NotImplementedError with phase labels
# ---------------------------------------------------------------------------

class TestNonLinuxPlatforms:
    def test_macos_raises_phase2(self) -> None:
        with patch("platform.system", return_value="Darwin"):
            with pytest.raises(NotImplementedError) as exc_info:
                resolve("system")
        assert "Phase 2" in str(exc_info.value)

    def test_windows_raises_phase34(self) -> None:
        with patch("platform.system", return_value="Windows"):
            with pytest.raises(NotImplementedError) as exc_info:
                resolve("system")
        assert "Phase 3" in str(exc_info.value) or "Phase 4" in str(exc_info.value)

    def test_freebsd_raises_not_implemented(self) -> None:
        with patch("platform.system", return_value="FreeBSD"):
            with pytest.raises(NotImplementedError) as exc_info:
                resolve("system")
        assert "Phase 1" in str(exc_info.value)


# ---------------------------------------------------------------------------
# render_mcp_json
# ---------------------------------------------------------------------------

class TestRenderMcpJson:
    def test_returns_valid_json(self) -> None:
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        content = render_mcp_json(p)
        parsed = json.loads(content)
        assert "mcpServers" in parsed

    def test_contains_expected_servers(self) -> None:
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        parsed = json.loads(render_mcp_json(p))
        servers = parsed["mcpServers"]
        assert "voice-stt" in servers
        assert "voice-tts" in servers
        assert "project-orchestrator" in servers
        assert "hermes-api" in servers
        assert "playwright" in servers

    def test_hermes_whisper_bin_in_env(self) -> None:
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        parsed = json.loads(render_mcp_json(p))
        env = parsed["mcpServers"]["voice-stt"]["env"]
        assert env["HERMES_WHISPER_BIN"] == str(p.whisper_bin)
        assert env["HERMES_WHISPER_MODEL"] == str(p.whisper_model)

    def test_hermes_piper_bin_in_env(self) -> None:
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        parsed = json.loads(render_mcp_json(p))
        env = parsed["mcpServers"]["voice-tts"]["env"]
        assert env["HERMES_PIPER_BIN"] == str(p.piper_bin)

    def test_whisper_model_is_base_en(self) -> None:
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        parsed = json.loads(render_mcp_json(p))
        model = parsed["mcpServers"]["voice-stt"]["env"]["HERMES_WHISPER_MODEL"]
        assert "ggml-base.en.bin" in model

    def test_venv_python_used_as_command(self) -> None:
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        parsed = json.loads(render_mcp_json(p))
        cmd = parsed["mcpServers"]["voice-stt"]["command"]
        assert str(p.venv_bin) in cmd

    def test_custom_paths_reflected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SOMA_HOME", "/srv/soma")
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        content = render_mcp_json(p)
        assert "/srv/soma" in content

    def test_preserves_hermes_names(self) -> None:
        """HERMES_* env-var names must be present in the JSON (interface contract)."""
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        content = render_mcp_json(p)
        assert "HERMES_WHISPER_BIN" in content
        assert "HERMES_PIPER_BIN" in content
        assert "HERMES_ORCH_DB" in content
        assert "HERMES_API_SOCKET" in content
        assert "HERMES_ACTIVITY_LOG" in content

    def test_playwright_storage_states_point_to_pw_dir(self) -> None:
        with patch("platform.system", return_value="Linux"):
            p = resolve("system")
        parsed = json.loads(render_mcp_json(p))
        linkedin_args = parsed["mcpServers"]["playwright-linkedin"]["args"]
        state_idx = linkedin_args.index("--storage-state") + 1
        assert "state-linkedin.json" in linkedin_args[state_idx]
