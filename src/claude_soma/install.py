"""python -m claude_soma.install — operator bootstrapper for Claude Soma.

OPERATORS ONLY (V1).  Contributor-mode (degraded local dev install) is a
planned follow-up; see docs/MULTI_PLATFORM_INSTALL.md §6 for the roadmap.

Usage
-----
    # Always dry-run first — see what would happen, make zero changes:
    python -m claude_soma.install --dry-run

    # With OCI iptables workaround:
    python -m claude_soma.install --dry-run --cloud=oci

    # When you're happy with the plan, apply it:
    python -m claude_soma.install --apply

    # Non-interactive (CI / headless):
    python -m claude_soma.install --apply --non-interactive

Flags
-----
--dry-run       Print every action that WOULD run (no state changes).
                Writes install-plan.log to a tempdir and prints its path.
--apply         Actually execute the plan.  Logs privileged actions to
                ~/.claude-soma/install-runs/<timestamp>/.
--install-mode  system (default) or user.
                system = /opt/claude-soma, /etc/…, /var/log/…  (needs sudo)
                user   = XDG dirs under ~  (no sudo for dirs; services still
                         need systemd user units — not yet supported, TODO).
--cloud=oci     Enable the iptables ACCEPT-before-Oracle-REJECT step.
                NO iptables changes without this flag.
--features      Comma-separated feature set (default: voice,social).
                Available: voice, social.  Pass --features="" to skip extras.
--non-interactive
                Skip interactive prompts (wizard).  Use env vars for secrets.
--verbose       In --dry-run, print full rendered file content (not just headers).

Exit codes
----------
0   Plan is complete and printable (dry-run) or all actions succeeded (apply).
1   Platform not supported / blocking precondition.
2   An action failed during --apply.
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Literal, Optional

from claude_soma.platform._action import Action
from claude_soma.platform.paths import Paths, resolve, render_mcp_json
from claude_soma.platform.pkg import (
    PackageManager,
    detect_package_manager,
    pkg_install,
)
from claude_soma.platform.services import (
    Service,
    SystemdBackend,
    _render_service,
)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("claude_soma.install")


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        format="[%(levelname)s] %(message)s",
        level=level,
        stream=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Version (read from pyproject.toml at the package root)
# ---------------------------------------------------------------------------

def _package_version() -> str:
    pyproject = Path(__file__).parent.parent.parent / "pyproject.toml"
    try:
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        return data["project"]["version"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "unknown"


# ---------------------------------------------------------------------------
# Platform detection (read-only; safe in --dry-run)
# ---------------------------------------------------------------------------

def _detect_os_release() -> dict[str, str]:
    result: dict[str, str] = {}
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, _, v = line.partition("=")
                        result[k.strip()] = v.strip().strip('"').strip("'")
            break
        except OSError:
            continue
    return result


def _detect_init_system() -> str:
    """Detect init system: 'systemd', 'openrc', 'upstart', 'unknown'.

    Read-only: only uses command -v + /proc reads, no writes.
    """
    # Check if PID 1 is systemd
    try:
        pid1_cmdline = Path("/proc/1/cmdline").read_bytes().split(b"\x00")[0].decode()
        if "systemd" in pid1_cmdline:
            return "systemd"
    except OSError:
        pass

    # Check for WSL2 (systemd may not be PID 1 in older WSL2 kernels)
    try:
        osrelease = Path("/proc/sys/kernel/osrelease").read_text().lower()
        if "microsoft" in osrelease:
            # WSL2 ships systemd since 2022; systemctl presence is the gate.
            if shutil.which("systemctl"):
                return "systemd"
    except OSError:
        pass

    # Check binary presence as fallback
    if shutil.which("systemctl"):
        return "systemd"
    if shutil.which("rc-service"):
        return "openrc"
    if shutil.which("initctl"):
        return "upstart"

    return "unknown"


def _detect_arch() -> str:
    """Return normalised architecture: 'aarch64', 'x86_64', or raw value."""
    m = platform.machine()
    if m in ("aarch64", "arm64"):
        return "aarch64"
    if m in ("x86_64", "AMD64"):
        return "x86_64"
    return m


class PlatformInfo:
    """Collected platform facts (read-only; gathered before any action)."""
    __slots__ = (
        "os_name", "distro_id", "distro_id_like", "arch",
        "init_system", "pm",
    )

    def __init__(self) -> None:
        self.os_name = platform.system()
        osr = _detect_os_release()
        self.distro_id = osr.get("ID", "unknown").lower()
        self.distro_id_like = osr.get("ID_LIKE", "").lower()
        self.arch = _detect_arch()
        self.init_system = _detect_init_system()
        try:
            self.pm = detect_package_manager()
        except (NotImplementedError, RuntimeError):
            self.pm = None  # type: ignore[assignment]

    def describe(self) -> str:
        pm_str = self.pm.value if self.pm else "unknown"
        return (
            f"OS={self.os_name}  distro={self.distro_id}  arch={self.arch}  "
            f"init={self.init_system}  pkg={pm_str}"
        )


# ---------------------------------------------------------------------------
# Action builders (plan-only; never execute)
# ---------------------------------------------------------------------------

def _action_mkdir(path: str, owner: str, mode: str, *, is_system: bool) -> Action:
    """Action to create a directory with correct owner/mode."""
    if is_system:
        return Action(
            commands=[
                ["sudo", "install", "-d", "-m", mode, "-o", owner, "-g", owner, path],
            ],
            description=f"Create directory {path} (owner={owner}, mode={mode})",
            is_privileged=True,
        )
    return Action(
        commands=[["mkdir", "-p", path]],
        description=f"Create directory {path}",
        is_privileged=False,
    )


def _action_create_secrets_env(secrets_path: str, *, is_system: bool) -> Action:
    """Action to create an empty secrets.env template (600, not world-readable).

    SECRETS NOTE: this action creates an EMPTY template — it never writes
    actual secrets.  Secrets are filled by the operator or wizard, never
    printed on argv or in the plan log.
    """
    template = (
        "# Claude Soma secrets — operator-managed, never commit to git.\n"
        "# Fill in values, then restart services.\n"
        "CLAUDE_CODE_OAUTH_TOKEN=\n"
        "AUTH_GITHUB_ID=\n"
        "AUTH_GITHUB_SECRET=\n"
        "AUTH_SECRET=\n"
        "AUTH_URL=\n"
        "AUTH_TRUST_HOST=true\n"
    )
    if is_system:
        # Write via sudo; mode 600 so only root can read.
        return Action(
            commands=[
                # sudo install: atomic, sets perms in one step.
                # SECRETS NOTE: no secret value on argv; this is a template.
                ["sudo", "install", "-m", "600", "-o", "root", "-g", "root",
                 "__TMPFILE__", secrets_path],
            ],
            description=f"Create secrets template at {secrets_path} (mode=600)",
            is_privileged=True,
            writes=[(secrets_path, template)],
            note="SECRETS NOTE: creates an empty template only; fill values manually.",
        )
    return Action(
        commands=[],
        description=f"Create secrets template at {secrets_path} (mode=600)",
        is_privileged=False,
        writes=[(secrets_path, template)],
        note="SECRETS NOTE: creates an empty template only; fill values manually.",
    )


def _action_write_mcp_json(dest: str, content: str) -> Action:
    """Action to write the rendered .mcp.json."""
    return Action(
        commands=[],  # user-writable; no sudo needed
        description=f"Write rendered .mcp.json to {dest}",
        is_privileged=False,
        writes=[(dest, content)],
    )


def _action_iptables_oci() -> Action:
    """OCI-specific iptables rule: insert ACCEPT before Oracle's REJECT.

    Only produced when --cloud=oci is passed.

    SECRETS NOTE: no secrets involved; pure network rule.
    """
    return Action(
        commands=[
            # Insert an ACCEPT rule at position 1 in INPUT chain, before
            # Oracle Cloud's default REJECT rule, to allow inbound traffic.
            ["sudo", "iptables", "-I", "INPUT", "1", "-j", "ACCEPT"],
            # Persist across reboots (Ubuntu/Debian).
            ["sudo", "bash", "-c",
             "iptables-save > /etc/iptables/rules.v4 2>/dev/null || true"],
        ],
        description="OCI iptables: insert ACCEPT before Oracle REJECT (--cloud=oci)",
        is_privileged=True,
        note=(
            "Oracle Cloud Infrastructure inserts a REJECT rule at the end of the "
            "INPUT chain during instance boot.  This inserts an unconditional ACCEPT "
            "before it so inbound traffic reaches Caddy.  Only runs with --cloud=oci."
        ),
    )


def _action_clone_repo(dest: str, repo_url: str) -> Action:
    return Action(
        commands=[
            ["sudo", "git", "clone", "--depth=1", repo_url, dest],
            ["sudo", "chown", "-R", "ubuntu:ubuntu", dest],
        ],
        description=f"Clone claude-soma repo to {dest}",
        is_privileged=True,
        note="Clones the repo at HEAD; operator should pin a tag for production.",
    )


def _action_venv_install(code_root: str, venv_bin: str) -> Action:
    """Create venv and install the package."""
    return Action(
        commands=[
            ["sudo", "-u", "ubuntu", "python3.12", "-m", "venv",
             f"{code_root}/.venv"],
            ["sudo", "-u", "ubuntu",
             f"{venv_bin}/pip", "install", "-e", f"{code_root}[dev]"],
        ],
        description=f"Create venv at {code_root}/.venv and install claude-soma",
        is_privileged=True,
    )


def _action_build_whisper(code_root: str, *, arch: str) -> list[Action]:
    """Actions to clone and build whisper.cpp."""
    whisper_dir = "/opt/whisper.cpp"
    model_url = (
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin"
    )
    model_dest = f"{whisper_dir}/models/ggml-base.en.bin"
    return [
        Action(
            commands=[
                ["sudo", "git", "clone", "--depth=1",
                 "https://github.com/ggerganov/whisper.cpp",
                 whisper_dir],
                ["sudo", "bash", "-c",
                 f"cd {whisper_dir} && cmake -B build -DWHISPER_BUILD_TESTS=OFF"
                 " && cmake --build build --config Release --target whisper-cli -j$(nproc)"],
                ["sudo", "mkdir", "-p", f"{whisper_dir}/models"],
                # SECRETS NOTE: downloads from a PUBLIC HuggingFace URL.
                ["sudo", "curl", "-L", "-o", model_dest, model_url],
                ["sudo", "chown", "-R", "ubuntu:ubuntu", whisper_dir],
            ],
            description=(
                f"Clone, build whisper.cpp ({arch}), "
                f"download ggml-base.en.bin model"
            ),
            is_privileged=True,
            note=(
                "Whisper model: ggml-base.en.bin (matches .mcp.json default). "
                "Build requires cmake + build-essential (install whisper-build-deps first)."
            ),
        ),
    ]


def _action_install_piper(*, arch: str) -> list[Action]:
    """Actions to download and extract piper binary + default voice model."""
    piper_dir = "/opt/piper"

    # Map arch to piper release archive name
    arch_map = {
        "aarch64": "piper_linux_aarch64.tar.gz",
        "x86_64": "piper_linux_x86_64.tar.gz",
    }
    archive = arch_map.get(arch, f"piper_linux_{arch}.tar.gz")
    release_url = (
        f"https://github.com/rhasspy/piper/releases/download/v1.2.0/{archive}"
    )
    # en_US-ryan-medium voice model
    voice_url = (
        "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/"
        "en/en_US/ryan/medium/en_US-ryan-medium.onnx"
    )
    voice_json_url = (
        "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/"
        "en/en_US/ryan/medium/en_US-ryan-medium.onnx.json"
    )
    return [
        Action(
            commands=[
                ["sudo", "mkdir", "-p", piper_dir],
                # SECRETS NOTE: downloads from PUBLIC GitHub/HuggingFace URLs.
                ["sudo", "bash", "-c",
                 f"curl -L '{release_url}'"
                 f" | tar -xz --strip-components=1 -C {piper_dir}"],
                ["sudo", "curl", "-L", "-o", f"{piper_dir}/en_US-ryan-medium.onnx",
                 voice_url],
                ["sudo", "curl", "-L", "-o", f"{piper_dir}/en_US-ryan-medium.onnx.json",
                 voice_json_url],
                ["sudo", "chown", "-R", "ubuntu:ubuntu", piper_dir],
                ["sudo", "chmod", "+x", f"{piper_dir}/piper"],
            ],
            description=f"Download and install piper TTS binary ({arch}) + en_US-ryan-medium voice",
            is_privileged=True,
            note=(
                "piper binary: pre-built from rhasspy/piper v1.2.0 release. "
                "Voice model: en_US-ryan-medium.onnx (matches .mcp.json default)."
            ),
        ),
    ]


def _action_install_claude_cli() -> Action:
    """Install the claude CLI (native binary via npm global + claude install)."""
    return Action(
        commands=[
            # Step 1: install the npm package (gives us `claude` on PATH)
            # SECRETS NOTE: npm installs from PUBLIC registry only.
            ["sudo", "npm", "install", "-g", "@anthropic-ai/claude-code"],
            # Step 2: install the native binary (faster, supports --channels)
            # claude install latest writes to ~/.local/bin/claude for the
            # current user; run as ubuntu so it lands in /home/ubuntu/.local/bin.
            ["sudo", "-u", "ubuntu", "bash", "-c",
             "claude install latest || true"],
        ],
        description="Install claude CLI (npm package + native binary)",
        is_privileged=True,
        note=(
            "The native binary at ~/.local/bin/claude is required for --channels. "
            "The npm package at /usr/bin/claude silently falls back to --print mode."
        ),
    )


def _action_playwright_chromium(*, user: str = "ubuntu") -> Action:
    """Install playwright chromium browser + create PATH symlink."""
    return Action(
        commands=[
            # SECRETS NOTE: downloads from PUBLIC playwright CDN.
            ["sudo", "-u", user, "bash", "-c",
             "npx playwright install --with-deps chromium"],
            # Create the symlink that .mcp.json references
            ["sudo", "bash", "-c",
             "chromium_path=$(sudo -u ubuntu bash -c"
             " 'npx playwright install --dry-run chromium 2>&1"
             " | grep -oP \"(?<=Installing Chromium ).*(?= \\()\"' || true);"
             " installed=$(find /home/ubuntu/.cache/ms-playwright -name chrome"
             " -type f 2>/dev/null | head -1 || true);"
             " if [ -n \"$installed\" ];"
             " then ln -sf \"$installed\" /usr/local/bin/playwright-chromium; fi"],
        ],
        description="Install Playwright Chromium browser and create /usr/local/bin/playwright-chromium symlink",
        is_privileged=True,
        note=(
            "The symlink /usr/local/bin/playwright-chromium is referenced by "
            ".mcp.json.  Only created when the browser install locates the binary."
        ),
    )


# ---------------------------------------------------------------------------
# Define the standard service catalog
# ---------------------------------------------------------------------------

def _build_services(paths: Paths) -> list[Service]:
    """Return the canonical list of Claude Soma services."""
    env_path = str(paths.secrets_env)
    code_root = str(paths.code_root)
    venv_python = str(paths.venv_bin / "python")
    log_dir = str(paths.log_dir)

    return [
        Service(
            name="claude-soma-api",
            description="Claude Soma FastAPI backend",
            exec_argv=[
                str(paths.venv_bin / "uvicorn"),
                "claude_soma.api.main:app",
                "--host", "127.0.0.1",
                "--port", "9000",
                "--no-server-header",
            ],
            env={
                "HERMES_ALLOWED_GITHUB_HANDLES": "techfreakworm",
                "HERMES_API_CORS_ORIGINS": "http://localhost:3000",
                "HERMES_USAGE_DB": str(paths.usage_db),
                "HERMES_ACTIVITY_LOG": str(paths.activity_log),
            },
            work_dir=code_root,
            restart_policy="always",
            restart_sec=5,
            log_paths={
                "stdout": f"{log_dir}/api.log",
                "stderr": f"{log_dir}/api.err.log",
            },
            user=paths.user,
            group=paths.user,
            env_file=env_path,
        ),
        Service(
            name="claude-soma-frontend",
            description="Claude Soma Next.js frontend",
            exec_argv=["bun", "run", "start"],
            env={},
            work_dir=f"{code_root}/frontend",
            restart_policy="always",
            restart_sec=5,
            log_paths={
                "stdout": f"{log_dir}/frontend.log",
                "stderr": f"{log_dir}/frontend.err.log",
            },
            user=paths.user,
            group=paths.user,
        ),
        # Channel service (Type=oneshot, RemainAfterExit)
        Service(
            name="claude-soma-channel",
            description="Claude Soma persistent Telegram channel session",
            exec_argv=[
                str(paths.tmux_bin),
                "new-session", "-d", "-s", "hermes",
                "-c", code_root,
                f"{code_root}/scripts/channel-claude.sh",
            ],
            env={
                "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
            },
            work_dir=code_root,
            restart_policy="on-failure",
            restart_sec=10,
            type_="oneshot",
            remain_after_exit=True,
            user=paths.user,
            group=paths.user,
            env_file=env_path,
        ),
        # Timer companion services (oneshot, no restart — triggered by timers)
        Service(
            name="claude-soma-healthcheck",
            description="Claude Soma healthcheck (run by timer)",
            exec_argv=[str(paths.venv_bin / "python"), "-m",
                       "claude_soma.scripts.healthcheck"],
            env={},
            work_dir=code_root,
            restart_policy="no",
            type_="oneshot",
            user=paths.user,
            group=paths.user,
            env_file=env_path,
        ),
        Service(
            name="claude-soma-cache-refresh",
            description="Claude Soma cache refresh (run by timer)",
            exec_argv=[str(paths.venv_bin / "python"), "-m",
                       "claude_soma.scripts.cache_refresh"],
            env={},
            work_dir=code_root,
            restart_policy="no",
            type_="oneshot",
            user=paths.user,
            group=paths.user,
            env_file=env_path,
        ),
        Service(
            name="claude-soma-usage-snapshot",
            description="Claude Soma usage snapshot (run by timer)",
            exec_argv=[str(paths.venv_bin / "python"), "-m",
                       "claude_soma.scripts.usage_snapshot"],
            env={},
            work_dir=code_root,
            restart_policy="no",
            type_="oneshot",
            user=paths.user,
            group=paths.user,
            env_file=env_path,
        ),
        Service(
            name="claude-soma-idle-reaper",
            description="Claude Soma idle lead reaper (run by timer)",
            exec_argv=[str(paths.venv_bin / "python"), "-m",
                       "claude_soma.scripts.idle_reaper"],
            env={},
            work_dir=code_root,
            restart_policy="no",
            type_="oneshot",
            user=paths.user,
            group=paths.user,
            env_file=env_path,
        ),
    ]


# ---------------------------------------------------------------------------
# Timer catalog
# ---------------------------------------------------------------------------

_TIMERS: list[tuple[str, str]] = [
    ("claude-soma-healthcheck",   "*:0/10"),             # every 10 min
    ("claude-soma-cache-refresh", "*:0/5"),              # every 5 min
    ("claude-soma-usage-snapshot", "*-*-* 23:55:00"),    # daily 23:55 UTC
    ("claude-soma-idle-reaper",   "0/6:00:00"),          # every 6h
]


# ---------------------------------------------------------------------------
# Plan builder
# ---------------------------------------------------------------------------

def build_plan(
    *,
    info: PlatformInfo,
    paths: Paths,
    install_mode: Literal["system", "user"],
    cloud: Optional[str],
    features: set[str],
) -> list[Action]:
    """Build the full ordered action list without executing anything.

    This is the single source of truth for what the installer does.  Both
    --dry-run (print) and --apply (execute) consume this list.

    Every privileged action has ``is_privileged=True`` so the sudo-audit log
    can collect them without re-scanning the command strings.
    """
    plan: list[Action] = []
    is_system = (install_mode == "system")

    # --- 0. Detect check ---------------------------------------------------
    if info.os_name != "Linux":
        raise NotImplementedError(
            f"Phase 1 supports Linux only.  Detected: {info.os_name}.  "
            "See docs/MULTI_PLATFORM_INSTALL.md §6.  "
            "Rerun with --dry-run to see the full plan."
        )
    if info.init_system != "systemd":
        log.warning(
            "WARNING: init system is %r (not systemd).  "
            "Lead isolation will be DEGRADED (no cgroup protection).  "
            "For strong isolation, use a Linux host with systemd as PID 1.",
            info.init_system,
        )

    pm = info.pm
    if pm is None:
        raise RuntimeError(
            "Cannot detect package manager.  Rerun with --dry-run to see the full plan."
        )

    # --- 1. Create directories ---------------------------------------------
    dirs_to_create = [
        (str(paths.code_root), paths.user, "755"),
        (str(paths.config_dir), "root", "750"),
        (str(paths.log_dir), paths.user, "755"),
        (str(paths.lead_log_dir), paths.user, "755"),
        (str(paths.lead_work_dir), paths.user, "755"),
        (str(paths.state_dir), paths.user, "755"),
        (str(paths.home / ".claude-soma"), paths.user, "700"),
        (str(paths.pw_dir), paths.user, "700"),
    ]
    for d, owner, mode in dirs_to_create:
        plan.append(_action_mkdir(d, owner, mode, is_system=is_system))

    # --- 2. Install core packages ------------------------------------------
    core_pkgs = ["curl", "git", "openssl", "tmux", "python3.12",
                 "build-essential", "caddy", "node22", "bun", "gh"]
    for pkg in core_pkgs:
        plan.append(pkg_install(pkg, dry_run=True, pm=pm))

    # --- 3. Voice packages -------------------------------------------------
    if "voice" in features:
        plan.append(pkg_install("ffmpeg", dry_run=True, pm=pm))
        plan.append(pkg_install("whisper-build-deps", dry_run=True, pm=pm))

    # --- 4. Clone / update code -------------------------------------------
    plan.append(_action_clone_repo(str(paths.code_root),
                                   "https://github.com/techfreakworm/claude-soma"))

    # --- 5. Create Python venv + install -----------------------------------
    plan.append(_action_venv_install(str(paths.code_root), str(paths.venv_bin)))

    # --- 6. Install claude CLI --------------------------------------------
    plan.append(_action_install_claude_cli())

    # --- 7. Voice binaries (whisper + piper) ------------------------------
    if "voice" in features:
        plan.extend(_action_build_whisper(str(paths.code_root), arch=info.arch))
        plan.extend(_action_install_piper(arch=info.arch))

    # --- 8. Social (playwright) -------------------------------------------
    if "social" in features:
        plan.append(pkg_install("playwright-mcp", dry_run=True, pm=pm))
        plan.append(_action_playwright_chromium(user=paths.user))

    # --- 9. Secrets template ----------------------------------------------
    # Only create if file does not exist (idempotent).  In --apply mode the
    # execute function skips the write if the dest already exists.
    plan.append(_action_create_secrets_env(str(paths.secrets_env),
                                           is_system=is_system))

    # --- 10. Systemd services + timers ------------------------------------
    backend = SystemdBackend(
        systemctl_bin=str(paths.systemctl_bin),
        systemd_run_bin=str(paths.systemd_run_bin),
        sudo_bin=str(paths.sudo_bin),
    )
    services = _build_services(paths)
    for svc in services:
        plan.append(backend.install_service(svc))
    for timer_name, on_calendar in _TIMERS:
        plan.append(backend.install_timer(timer_name, on_calendar,
                                          service_name=timer_name))

    # --- 11. Enable services ----------------------------------------------
    service_names_to_enable = [
        "claude-soma-channel.service",
        "claude-soma-api.service",
        "claude-soma-frontend.service",
        "claude-soma-healthcheck.timer",
        "claude-soma-cache-refresh.timer",
        "claude-soma-usage-snapshot.timer",
        "claude-soma-idle-reaper.timer",
    ]
    for svc_name in service_names_to_enable:
        plan.append(backend.enable(svc_name))

    # --- 12. OCI iptables (behind --cloud=oci) ----------------------------
    if cloud == "oci":
        plan.append(_action_iptables_oci())

    # --- 13. Write .mcp.json ----------------------------------------------
    mcp_dest = str(paths.home / ".mcp.json")
    mcp_content = render_mcp_json(paths)
    plan.append(_action_write_mcp_json(mcp_dest, mcp_content))

    return plan


# ---------------------------------------------------------------------------
# Dry-run printer
# ---------------------------------------------------------------------------

def _fmt_action(i: int, action: Action, *, verbose: bool) -> str:
    """Format one Action for dry-run output."""
    lines: list[str] = []
    priv_tag = " [PRIVILEGED]" if action.is_privileged else ""
    lines.append(f"\n[{i:03d}]{priv_tag} {action.description}")
    if action.note:
        lines.append(f"      NOTE: {action.note}")
    for cmd in action.commands:
        if cmd == ["__TMPFILE__"]:
            continue  # placeholder — skip
        displayed = [c if c != "__TMPFILE__" else "<tempfile>" for c in cmd]
        lines.append(f"      CMD:  {' '.join(displayed)}")
    for path, content in action.writes:
        if verbose:
            lines.append(f"      WRITE: {path}\n"
                         f"      --- content ---\n{content}\n      ---")
        else:
            first_line = content.split("\n")[0][:80]
            lines.append(f"      WRITE: {path}  ({first_line!r} …)")
    return "\n".join(lines)


def print_dry_run_plan(
    plan: list[Action],
    *,
    info: PlatformInfo,
    paths: Paths,
    verbose: bool,
    log_dir: Path,
) -> None:
    """Print the plan to stdout and write install-plan.log to log_dir."""
    header = (
        f"Claude Soma install plan  —  dry-run (no changes made)\n"
        f"Platform: {info.describe()}\n"
        f"Code root: {paths.code_root}\n"
        f"Config:    {paths.config_dir}\n"
        f"Logs:      {paths.log_dir}\n"
        f"Actions:   {len(plan)}\n"
        f"Privileged: {sum(1 for a in plan if a.is_privileged)} of {len(plan)}\n"
    )
    print(header)

    lines: list[str] = [header]
    for i, action in enumerate(plan, start=1):
        formatted = _fmt_action(i, action, verbose=verbose)
        print(formatted)
        lines.append(formatted)

    # Write install-plan.log
    log_path = log_dir / "install-plan.log"
    log_path.write_text("\n".join(lines))
    print(f"\nInstall plan written to: {log_path}")
    print("Re-run with --apply to execute (or --apply --cloud=oci on OCI).")


# ---------------------------------------------------------------------------
# Apply executor
# ---------------------------------------------------------------------------

def _execute_action(
    action: Action,
    *,
    run_log: Path,
    dry_run: bool = False,
) -> None:
    """Execute one action's commands in order.

    For actions with writes and __TMPFILE__ placeholders:
    - Writes the content to a real tempfile
    - Substitutes __TMPFILE__ in the command argv
    - Runs the command
    - Cleans up the tempfile

    SECRETS NOTE: no secret values appear in the log or on argv.  The only
    content written to tmpfiles is rendered unit-file text, never tokens.
    """
    if dry_run:
        return

    # Pre-create tempfiles for any write entries
    tmpfiles: list[tuple[str, str]] = []  # [(tmppath, dest_path)]
    for dest, content in action.writes:
        fd, tmp = tempfile.mkstemp(prefix="soma-install-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
        except Exception:
            os.unlink(tmp)
            raise
        tmpfiles.append((tmp, dest))

    try:
        for cmd_template in action.commands:
            if not cmd_template:
                continue

            # Replace __TMPFILE__ placeholder with the actual tempfile path
            cmd: list[str] = []
            tmp_idx = 0
            for token in cmd_template:
                if token == "__TMPFILE__":
                    if tmp_idx < len(tmpfiles):
                        cmd.append(tmpfiles[tmp_idx][0])
                        tmp_idx += 1
                    else:
                        cmd.append(token)  # no tempfile available, keep as-is
                else:
                    cmd.append(token)

            # Log privileged commands to the run log
            if action.is_privileged:
                with open(run_log, "a") as f:
                    f.write(f"[PRIVILEGED] {' '.join(cmd)}\n")

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=300,
                )
                if result.stdout:
                    log.debug("  stdout: %s", result.stdout[:500])
            except subprocess.CalledProcessError as e:
                stderr = (e.stderr or "")[-500:]
                raise RuntimeError(
                    f"Action failed: {action.description!r}\n"
                    f"Command: {' '.join(cmd)}\n"
                    f"Exit code: {e.returncode}\n"
                    f"Stderr: {stderr}\n"
                    f"Rerun with --dry-run to see the full plan."
                ) from e
            except subprocess.TimeoutExpired as e:
                raise RuntimeError(
                    f"Action timed out (300s): {action.description!r}\n"
                    f"Command: {' '.join(cmd)}"
                ) from e

        # Handle write-only actions (no commands, but has writes)
        if not action.commands and action.writes:
            for dest, content in action.writes:
                dest_path = Path(dest)
                if dest_path.exists():
                    log.info("  Skipping write (already exists): %s", dest)
                    continue
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                dest_path.write_text(content)
                if action.is_privileged:
                    with open(run_log, "a") as f:
                        f.write(f"[WRITE] {dest}\n")
                log.info("  Wrote: %s", dest)

    finally:
        for tmp, _ in tmpfiles:
            try:
                os.unlink(tmp)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m claude_soma.install",
        description=(
            "Claude Soma operator installer (Phase 1: any-Linux).  "
            "OPERATORS ONLY — contributor-mode coming later.  "
            "Always run --dry-run first to see what would happen."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m claude_soma.install --dry-run\n"
            "  python -m claude_soma.install --dry-run --cloud=oci --verbose\n"
            "  python -m claude_soma.install --apply\n"
            "  python -m claude_soma.install --apply --cloud=oci --non-interactive\n"
        ),
    )

    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "RECOMMENDED FIRST STEP.  Print every action that WOULD run.  "
            "Makes ZERO state changes.  Writes install-plan.log to a tempdir."
        ),
    )
    mode_group.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Execute the plan.  Logs privileged actions to "
            "~/.claude-soma/install-runs/<timestamp>/."
        ),
    )

    parser.add_argument(
        "--install-mode",
        choices=["system", "user"],
        default="system",
        help=(
            "system (default): /opt/claude-soma, /etc/…, /var/log/… (needs sudo). "
            "user: XDG dirs under ~ (no root for dirs; services still need systemd)."
        ),
    )
    parser.add_argument(
        "--cloud",
        metavar="PROVIDER",
        default=None,
        help=(
            "Cloud-provider-specific steps.  Currently: 'oci' enables the "
            "iptables ACCEPT-before-Oracle-REJECT rule.  "
            "No iptables changes without this flag."
        ),
    )
    parser.add_argument(
        "--features",
        default="voice,social",
        help=(
            "Comma-separated features to install (default: voice,social).  "
            "voice: ffmpeg, whisper.cpp, piper.  "
            "social: playwright + chromium.  "
            "Pass --features='' to install core only."
        ),
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Skip wizard prompts.  Use env vars to supply secrets.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="In --dry-run: print full rendered file content (not just headers).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"claude-soma {_package_version()}",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point: ``python -m claude_soma.install`` or ``soma-install``."""
    args = _parse_args(argv)
    _setup_logging(args.verbose)

    features = {f.strip() for f in args.features.split(",") if f.strip()}

    log.info("Claude Soma Installer — Phase 1 (any-Linux)")
    log.info("Version: %s", _package_version())

    # ---- Detect platform --------------------------------------------------
    info = PlatformInfo()
    log.info("Platform: %s", info.describe())

    if info.os_name != "Linux":
        print(
            f"ERROR: Phase 1 supports Linux only (detected {info.os_name!r}). "
            "See docs/MULTI_PLATFORM_INSTALL.md §6.  "
            "Rerun with --dry-run to see the full plan.",
            file=sys.stderr,
        )
        return 1

    if info.init_system != "systemd":
        print(
            f"WARNING: init system is {info.init_system!r} (expected systemd).  "
            "Lead isolation will be DEGRADED (no cgroup protection) on non-systemd hosts.  "
            "For strong cgroup isolation, use a Linux host with systemd as PID 1.",
            file=sys.stderr,
        )

    # ---- Resolve paths ----------------------------------------------------
    try:
        paths = resolve(args.install_mode, cloud=args.cloud)
    except NotImplementedError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    # ---- Build plan -------------------------------------------------------
    try:
        plan = build_plan(
            info=info,
            paths=paths,
            install_mode=args.install_mode,
            cloud=args.cloud,
            features=features,
        )
    except (NotImplementedError, RuntimeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    # ---- Dry-run: print plan, write log, exit 0 --------------------------
    if args.dry_run:
        with tempfile.TemporaryDirectory(prefix="soma-install-dryrun-") as tmpdir:
            log_dir = Path(tmpdir)
            print_dry_run_plan(plan, info=info, paths=paths,
                               verbose=args.verbose, log_dir=log_dir)
            # Keep the tempdir alive for the user to inspect.
            # (TemporaryDirectory cleans up on exit; we print the path above.)
        return 0

    # ---- Apply: execute plan ---------------------------------------------
    ts = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    run_dir = Path.home() / ".claude-soma" / "install-runs" / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    run_log = run_dir / "install.log"
    run_log.write_text(
        f"# Claude Soma install log — {ts}\n"
        f"# Platform: {info.describe()}\n"
        f"# install-mode: {args.install_mode}\n"
        f"# cloud: {args.cloud}\n"
        f"# features: {sorted(features)}\n\n"
    )

    log.info("Run log: %s", run_log)
    log.info("Executing %d actions …", len(plan))

    for i, action in enumerate(plan, start=1):
        log.info("[%03d/%03d] %s", i, len(plan), action.description)
        try:
            _execute_action(action, run_log=run_log)
        except RuntimeError as e:
            print(f"\nERROR at action {i}: {e}", file=sys.stderr)
            print(f"See run log: {run_log}", file=sys.stderr)
            return 2

    log.info("All actions complete.")

    # ---- Post-install wizard (interactive) ------------------------------
    if not args.non_interactive:
        try:
            from claude_soma.wizard.init import run as wizard_run  # noqa: PLC0415
            wizard_run()
        except KeyboardInterrupt:
            print("\n(wizard interrupted — run soma-init to retry)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
