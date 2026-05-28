"""claude_soma.platform.paths — resolved per-OS path dataclass.

Phase 1: Linux (system-mode FHS + user-mode XDG).
Phase 2+: macOS / Windows raise NotImplementedError with a phase label.

HERMES_* env-var names are interface contracts (CLAUDE.md) — this module fills
their *values* from the resolved paths but does NOT rename the variables.  Only
the new SOMA_HOME override (no existing contract) uses the SOMA_* prefix.

Usage::

    from claude_soma.platform.paths import resolve, Paths

    paths = resolve("system")          # /opt/claude-soma, /etc/…, /var/log/…
    paths = resolve("user")            # XDG dirs under ~
    mcp_json_str = render_mcp_json(paths)
"""
from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional


@dataclass(frozen=True)
class Paths:
    """All resolved filesystem paths for one Claude Soma installation.

    All fields are Path objects except ``user`` (a string username) and
    ``api_socket`` / ``lead_mcp_config`` which are str because they are
    passed directly to subprocess argv or JSON values.

    Read-only: never writes to the filesystem.
    """

    # --- installation root --------------------------------------------------
    code_root: Path
    venv_bin: Path

    # --- configuration & secrets -------------------------------------------
    config_dir: Path
    secrets_env: Path          # HERMES_LEAD_ENV_FILE value

    # --- log and state directories -----------------------------------------
    log_dir: Path
    state_dir: Path
    lead_log_dir: Path         # HERMES_LEAD_LOG_DIR value

    # --- per-user state (not root-owned in system mode) --------------------
    home: Path
    user: str
    pw_dir: Path               # playwright auth states (~/.claude-pw)
    activity_log: Path         # HERMES_ACTIVITY_LOG value
    api_socket: str            # HERMES_API_SOCKET value (str, not Path)
    lead_work_dir: Path        # HERMES_PROJECTS_ROOT value

    # --- databases (inside code_root / state_dir) --------------------------
    registry_db: Path          # HERMES_ORCH_DB value
    usage_db: Path

    # --- lead MCP config ---------------------------------------------------
    lead_mcp_config: str       # HERMES_LEAD_MCP_CONFIG value (str, not Path)

    # --- optional tooling binaries (may not exist yet at resolve time) -----
    whisper_bin: Path          # HERMES_WHISPER_BIN value
    whisper_model: Path        # HERMES_WHISPER_MODEL value
    piper_bin: Path            # HERMES_PIPER_BIN value
    piper_voice: Path          # HERMES_PIPER_DEFAULT_VOICE value

    # --- system binaries ---------------------------------------------------
    tmux_bin: Path             # HERMES_TMUX_BIN value
    sudo_bin: Path             # HERMES_SUDO_BIN value
    systemd_run_bin: Path      # HERMES_SYSTEMD_RUN_BIN value
    systemctl_bin: Path        # HERMES_SYSTEMCTL_BIN value
    claude_bin: Path           # HERMES_CLAUDE_BIN value

    # --- playwright browser ------------------------------------------------
    playwright_bin: Path
    playwright_chromium: Path


def _env(key: str, default: str) -> str:
    """Return an env var value, falling back to ``default``."""
    return os.environ.get(key, default)


def _xdg_config_home() -> Path:
    raw = os.environ.get("XDG_CONFIG_HOME", "")
    return Path(raw) if raw else Path.home() / ".config"


def _xdg_state_home() -> Path:
    raw = os.environ.get("XDG_STATE_HOME", "")
    return Path(raw) if raw else Path.home() / ".local" / "state"


def _xdg_data_home() -> Path:
    raw = os.environ.get("XDG_DATA_HOME", "")
    return Path(raw) if raw else Path.home() / ".local" / "share"


def resolve(
    install_mode: Literal["system", "user"],
    cloud: Optional[str] = None,  # noqa: ARG001  (reserved for Phase 1 oci gating)
) -> "Paths":
    """Resolve all paths for the given install mode on the current OS.

    Parameters
    ----------
    install_mode:
        ``"system"`` — FHS layout under ``/opt``, ``/etc``, ``/var/log``.
                        Intended for root/operator installs.
        ``"user"``   — XDG layout under ``~/.config``, ``~/.local/state``,
                        ``~/.local/share``.  No root required.
    cloud:
        Reserved.  Pass ``"oci"`` when running on Oracle Cloud Infrastructure
        so the install module can enable the iptables ordering step.
        Currently unused by the paths layer.

    Raises
    ------
    NotImplementedError
        On macOS (Phase 2 pending) or Windows (Phase 3/4 pending).
    """
    sys_name = platform.system()

    if sys_name == "Darwin":
        raise NotImplementedError(
            "macOS support is Phase 2 (not yet implemented). "
            "See docs/MULTI_PLATFORM_INSTALL.md §6 for the roadmap. "
            "Rerun with --dry-run to see the full plan for Linux."
        )
    if sys_name == "Windows":
        raise NotImplementedError(
            "Native Windows support is Phase 3/4 (not yet implemented). "
            "For Windows parity, use WSL2 (Phase 3) which ships systemd. "
            "See docs/MULTI_PLATFORM_INSTALL.md §6 for the roadmap."
        )
    if sys_name != "Linux":
        raise NotImplementedError(
            f"Unsupported platform: {sys_name!r}. Phase 1 covers Linux only. "
            "See docs/MULTI_PLATFORM_INSTALL.md §6 for the roadmap."
        )

    home = Path(_env("HOME", str(Path.home())))
    user = _env("USER", os.environ.get("LOGNAME", "ubuntu"))

    # SOMA_HOME overrides the code_root for both modes (new env var, no
    # existing HERMES_* contract for this).
    soma_home_override = os.environ.get("SOMA_HOME", "")

    if install_mode == "system":
        code_root = Path(soma_home_override) if soma_home_override else Path("/opt/claude-soma")
        config_dir = Path("/etc/claude-soma")
        secrets_env = config_dir / "secrets.env"
        log_dir = Path("/var/log/claude-soma")
        state_dir = code_root
        lead_log_dir = log_dir
        venv_bin = code_root / ".venv" / "bin"

        # Per-user paths (ubuntu home, not /opt) — unchanged from CLAUDE.md canonical
        pw_dir = home / ".claude-pw"
        activity_log = home / ".claude-soma" / "activity.jsonl"

        # Binary/tool paths for system mode
        whisper_root = Path("/opt/whisper.cpp")
        piper_root = Path("/opt/piper")
        claude_bin = home / ".local" / "bin" / "claude"
        lead_work_dir = home / "projects"
        playwright_bin = Path("/usr/bin/playwright-mcp")
        playwright_chromium = Path("/usr/local/bin/playwright-chromium")

    elif install_mode == "user":
        xdg_data = _xdg_data_home()
        xdg_config = _xdg_config_home()
        xdg_state = _xdg_state_home()

        code_root = (
            Path(soma_home_override) if soma_home_override
            else xdg_data / "claude-soma"
        )
        config_dir = xdg_config / "claude-soma"
        secrets_env = config_dir / "secrets.env"
        log_dir = xdg_state / "claude-soma" / "logs"
        state_dir = xdg_state / "claude-soma"
        lead_log_dir = xdg_state / "claude-soma" / "leads"
        venv_bin = code_root / ".venv" / "bin"

        pw_dir = home / ".claude-pw"
        activity_log = xdg_state / "claude-soma" / "activity.jsonl"

        whisper_root = xdg_data / "claude-soma" / "whisper.cpp"
        piper_root = xdg_data / "claude-soma" / "piper"
        claude_bin = home / ".local" / "bin" / "claude"
        lead_work_dir = home / "projects"
        playwright_bin = Path("/usr/bin/playwright-mcp")
        playwright_chromium = Path("/usr/local/bin/playwright-chromium")
    else:
        raise ValueError(f"install_mode must be 'system' or 'user', got {install_mode!r}")

    # Apply HERMES_* env overrides (interface contract — names must not change).
    # Values come from the resolved paths above unless the operator has set an
    # override explicitly.  Any HERMES_* override takes precedence.
    whisper_bin = Path(_env("HERMES_WHISPER_BIN", str(whisper_root / "build" / "bin" / "whisper-cli")))
    whisper_model = Path(_env("HERMES_WHISPER_MODEL", str(whisper_root / "models" / "ggml-base.en.bin")))
    piper_bin = Path(_env("HERMES_PIPER_BIN", str(piper_root / "piper")))
    piper_voice = Path(_env("HERMES_PIPER_DEFAULT_VOICE", str(piper_root / "en_US-ryan-medium.onnx")))
    registry_db = Path(_env("HERMES_ORCH_DB", str(state_dir / "registry.sqlite")))
    usage_db = Path(_env("HERMES_USAGE_DB", str(state_dir / "usage.sqlite")))
    activity_log = Path(_env("HERMES_ACTIVITY_LOG", str(activity_log)))
    api_socket = _env("HERMES_API_SOCKET", "/tmp/claude-soma-api.sock")
    tmux_bin = Path(_env("HERMES_TMUX_BIN", "/usr/bin/tmux"))
    sudo_bin = Path(_env("HERMES_SUDO_BIN", "/usr/bin/sudo"))
    systemd_run_bin = Path(_env("HERMES_SYSTEMD_RUN_BIN", "/usr/bin/systemd-run"))
    systemctl_bin = Path(_env("HERMES_SYSTEMCTL_BIN", "/usr/bin/systemctl"))
    claude_bin = Path(_env("HERMES_CLAUDE_BIN", str(claude_bin)))
    lead_log_dir = Path(_env("HERMES_LEAD_LOG_DIR", str(lead_log_dir)))
    lead_work_dir = Path(_env("HERMES_PROJECTS_ROOT", str(lead_work_dir)))
    lead_mcp_config = _env(
        "HERMES_LEAD_MCP_CONFIG",
        str(code_root / "config" / "claude" / "lead-mcp.json"),
    )

    return Paths(
        code_root=code_root,
        venv_bin=venv_bin,
        config_dir=config_dir,
        secrets_env=secrets_env,
        log_dir=log_dir,
        state_dir=state_dir,
        lead_log_dir=lead_log_dir,
        home=home,
        user=user,
        pw_dir=pw_dir,
        activity_log=activity_log,
        api_socket=api_socket,
        lead_work_dir=lead_work_dir,
        registry_db=registry_db,
        usage_db=usage_db,
        lead_mcp_config=lead_mcp_config,
        whisper_bin=whisper_bin,
        whisper_model=whisper_model,
        piper_bin=piper_bin,
        piper_voice=piper_voice,
        tmux_bin=tmux_bin,
        sudo_bin=sudo_bin,
        systemd_run_bin=systemd_run_bin,
        systemctl_bin=systemctl_bin,
        claude_bin=claude_bin,
        playwright_bin=playwright_bin,
        playwright_chromium=playwright_chromium,
    )


def render_mcp_json(paths: "Paths") -> str:
    """Render a .mcp.json string from the resolved Paths.

    Produces a JSON document with the same structure as the shipped
    ``/.mcp.json`` but with all absolute paths filled from ``paths``.

    The HERMES_* env-var names are preserved (interface contract per
    CLAUDE.md) — only the values change to match the resolved paths.

    The repo's ``.mcp.json`` file stays as-is (Linux-default values for
    the OCI VPS).  This function generates a FRESH copy at install time.
    """
    doc = {
        "mcpServers": {
            "voice-stt": {
                "type": "stdio",
                "command": str(paths.venv_bin / "python"),
                "args": ["-m", "claude_soma.mcp_servers.voice_stt.server"],
                "env": {
                    "HERMES_WHISPER_BIN": str(paths.whisper_bin),
                    "HERMES_WHISPER_MODEL": str(paths.whisper_model),
                },
                "alwaysLoad": True,
            },
            "voice-tts": {
                "type": "stdio",
                "command": str(paths.venv_bin / "python"),
                "args": ["-m", "claude_soma.mcp_servers.voice_tts.server"],
                "env": {
                    "HERMES_PIPER_BIN": str(paths.piper_bin),
                    "HERMES_PIPER_DEFAULT_VOICE": str(paths.piper_voice),
                },
                "alwaysLoad": True,
            },
            "project-orchestrator": {
                "type": "stdio",
                "command": str(paths.venv_bin / "python"),
                "args": ["-m", "claude_soma.mcp_servers.project_orchestrator.server"],
                "env": {
                    "HERMES_ORCH_DB": str(paths.registry_db),
                    "HERMES_PROJECTS_ROOT": str(paths.lead_work_dir),
                    "HERMES_MAX_CONCURRENT_PROJECTS": "6",
                },
                "alwaysLoad": True,
            },
            "hermes-api": {
                "type": "stdio",
                "command": str(paths.venv_bin / "python"),
                "args": ["-m", "claude_soma.mcp_servers.hermes_api.server"],
                "env": {
                    "HERMES_API_SOCKET": paths.api_socket,
                    "HERMES_ACTIVITY_LOG": str(paths.activity_log),
                },
                "alwaysLoad": True,
            },
            "playwright": {
                "type": "stdio",
                "command": str(paths.playwright_bin),
                "args": [
                    "--headless",
                    "--isolated",
                    "--executable-path", str(paths.playwright_chromium),
                ],
                "alwaysLoad": True,
            },
            "playwright-linkedin": {
                "type": "stdio",
                "command": str(paths.playwright_bin),
                "args": [
                    "--headless",
                    "--isolated",
                    "--storage-state", str(paths.pw_dir / "state-linkedin.json"),
                    "--executable-path", str(paths.playwright_chromium),
                ],
                "alwaysLoad": True,
            },
            "playwright-x": {
                "type": "stdio",
                "command": str(paths.playwright_bin),
                "args": [
                    "--headless",
                    "--isolated",
                    "--storage-state", str(paths.pw_dir / "state-x.json"),
                    "--executable-path", str(paths.playwright_chromium),
                ],
                "alwaysLoad": True,
            },
            "playwright-x-article": {
                "type": "stdio",
                "command": str(paths.playwright_bin),
                "args": [
                    "--headless",
                    "--isolated",
                    "--storage-state", str(paths.pw_dir / "state-x.json"),
                    "--executable-path", str(paths.playwright_chromium),
                ],
                "alwaysLoad": True,
            },
            "playwright-medium": {
                "type": "stdio",
                "command": str(paths.playwright_bin),
                "args": [
                    "--headless",
                    "--isolated",
                    "--storage-state", str(paths.pw_dir / "state-medium.json"),
                    "--executable-path", str(paths.playwright_chromium),
                ],
                "alwaysLoad": True,
            },
        }
    }
    return json.dumps(doc, indent=2)
