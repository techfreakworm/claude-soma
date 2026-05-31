"""Interactive setup wizard: clean Ubuntu VPS to working claude-soma in ~30 min.

Designed to be re-run safely (idempotent) on the OCI VPS.  Reads/writes
the secrets file, /etc/systemd/system/claude-soma-*, /etc/caddy/Caddyfile,
and ~/.mcp.json — all paths resolved from the platform layer, not hardcoded.

Run as: sudo soma-init
   or:  python -m claude_soma.install --apply  (automates the full stack)
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from typing import Iterable

from claude_soma.platform.paths import Paths, resolve, render_mcp_json
from claude_soma.platform.services import (
    Service,
    SystemdBackend,
    _render_service,
    _render_timer,
)


# --- Lazy path resolution --------------------------------------------------
# Resolved once at first access so tests that mock platform.system() work.
_paths: Paths | None = None


def _get_paths() -> Paths:
    global _paths
    if _paths is None:
        _paths = resolve("system")
    return _paths


# Keep these module-level properties for backward compat with any external
# code that imports REPO_ROOT or SECRETS from this module directly.
# They are evaluated lazily via property-like functions, not at import time.
def _repo_root() -> Path:
    return _get_paths().code_root


def _secrets_path() -> Path:
    return _get_paths().secrets_env


# Legacy aliases (used in tests and older scripts; prefer _get_paths())
# Setting them as functions rather than module-level constants prevents
# the hardcoded "/opt/claude-soma" from leaking on non-Linux hosts.
REPO_ROOT: Path = Path("/opt/claude-soma")  # kept for backward compat
SECRETS: Path = Path("/etc/claude-soma/secrets.env")  # kept for backward compat


# ---- pure helpers (testable) ----------------------------------------------

DOMAIN_RX = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)


def validate_domain(d: str) -> bool:
    return bool(DOMAIN_RX.match(d))


def render_caddyfile(domain: str, email: str = "") -> str:
    email_block = f"    email {email}\n" if email else ""
    return dedent(f"""\
        {{
        {email_block}}}

        {domain} {{
            handle_path /api/* {{
                request_header -X-GitHub-Handle
                reverse_proxy localhost:9000
            }}
            handle {{
                reverse_proxy localhost:3000
            }}
            encode gzip zstd
            log {{
                output file /var/log/caddy/access.log
            }}
        }}
        """)


def render_systemd_unit(
    *,
    name: str,
    description: str,
    exec_start: str,
    type_: str = "simple",
    user: str = "ubuntu",
    env_file: str | None = None,
    wd: str | None = None,
    restart_sec: int = 5,
) -> str:
    """Render a systemd unit file using the platform paths layer.

    ``env_file`` defaults to the resolved secrets_env path.
    ``wd`` defaults to the resolved code_root.

    SECRETS NOTE: env_file is a file path only; actual secrets are read by
    systemd at runtime, never embedded in the unit content.
    """
    paths = _get_paths()
    _env_file = env_file if env_file is not None else str(paths.secrets_env)
    _wd = wd if wd is not None else str(paths.code_root)
    _log_dir = str(paths.log_dir)
    _venv_bin = str(paths.venv_bin)

    svc = Service(
        name=name,
        description=description,
        exec_argv=exec_start.split(),  # simple split; callers with spaces need _render_service directly
        env={"PATH": f"{_venv_bin}:/usr/local/bin:/usr/bin:/bin"},
        work_dir=_wd,
        restart_policy="always",
        restart_sec=restart_sec,
        log_paths={
            "stdout": f"{_log_dir}/{name}.log",
            "stderr": f"{_log_dir}/{name}.err.log",
        },
        user=user,
        group=user,
        type_=type_,  # type: ignore[arg-type]
        env_file=_env_file,
    )
    return _render_service(svc)


# ---- I/O wrappers ---------------------------------------------------------

def prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{label}{suffix}: ").strip()
    return val or default


def confirm(label: str, default: bool = True) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    val = input(f"{label}{suffix}: ").strip().lower()
    if not val:
        return default
    return val.startswith("y")


def write_secret(name: str, value: str) -> None:
    """Write a key=value line to the resolved secrets.env file.

    SECRETS NOTE: the value is written to disk (mode 600).  It is never
    logged, never echoed to stdout, and never passed as a subprocess arg.
    """
    secrets = _secrets_path()
    secrets.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if secrets.exists():
        for ln in secrets.read_text().splitlines():
            if not ln.strip() or ln.strip().startswith("#") or "=" not in ln:
                lines.append(ln)
                continue
            k, _, _ = ln.partition("=")
            if k.strip() != name:
                lines.append(ln)
    lines.append(f"{name}={value}")
    secrets.write_text("\n".join(lines) + "\n")
    os.chmod(secrets, 0o600)


def install_units(units: Iterable[tuple[str, str]]) -> None:
    """Install systemd unit files and reload daemon.

    Each entry is (unit_name_or_path, contents).  The content is rendered
    by the platform layer — not copied from the static systemd/ directory.

    SECRETS NOTE: unit file content contains only env-file PATHS (loaded
    at runtime by systemd), never actual secret values.
    """
    unit_dir = Path("/etc/systemd/system")
    for name_or_path, contents in units:
        target = unit_dir / Path(name_or_path).name
        target.write_text(contents)
        target.chmod(0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)


def enable_services(names: Iterable[str]) -> None:
    cmd = ["systemctl", "enable", "--now", *names]
    subprocess.run(cmd, check=True)


def _build_wizard_services(paths: Paths) -> list[tuple[str, str]]:
    """Build (unit_filename, unit_content) pairs for all Claude Soma services.

    Uses _render_service / _render_timer from the platform layer so unit
    file paths come from the resolved Paths object, not hardcoded strings.
    """
    env_file = str(paths.secrets_env)
    code_root = str(paths.code_root)
    venv_bin = str(paths.venv_bin)
    log_dir = str(paths.log_dir)
    user = paths.user

    path_env = f"{venv_bin}:/usr/local/bin:/usr/bin:/bin"

    services = [
        Service(
            name="claude-soma-api",
            description="Claude Soma FastAPI backend",
            exec_argv=[
                f"{venv_bin}/uvicorn",
                "claude_soma.api.main:app",
                "--host", "127.0.0.1",
                "--port", "9000",
                "--no-server-header",
            ],
            env={
                "PATH": path_env,
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
            user=user, group=user,
            env_file=env_file,
        ),
        Service(
            name="claude-soma-frontend",
            description="Claude Soma Next.js frontend",
            exec_argv=["bun", "run", "start"],
            env={"PATH": path_env},
            work_dir=f"{code_root}/frontend",
            restart_policy="always",
            restart_sec=5,
            log_paths={
                "stdout": f"{log_dir}/frontend.log",
                "stderr": f"{log_dir}/frontend.err.log",
            },
            user=user, group=user,
        ),
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
                "PATH": path_env,
                "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
            },
            work_dir=code_root,
            restart_policy="on-failure",
            restart_sec=10,
            type_="oneshot",
            remain_after_exit=True,
            user=user, group=user,
            env_file=env_file,
        ),
    ]

    timer_service_stubs = [
        Service(
            name="claude-soma-healthcheck",
            description="Claude Soma healthcheck (run by timer)",
            exec_argv=[f"{venv_bin}/python", "-m", "claude_soma.scripts.healthcheck"],
            env={}, work_dir=code_root, restart_policy="no", type_="oneshot",
            user=user, group=user, env_file=env_file,
        ),
        Service(
            name="claude-soma-cache-refresh",
            description="Claude Soma cache refresh (run by timer)",
            exec_argv=[f"{venv_bin}/python", "-m", "claude_soma.scripts.cache_refresh"],
            env={}, work_dir=code_root, restart_policy="no", type_="oneshot",
            user=user, group=user, env_file=env_file,
        ),
        Service(
            name="claude-soma-usage-snapshot",
            description="Claude Soma usage snapshot (run by timer)",
            exec_argv=[f"{venv_bin}/python", "-m", "claude_soma.scripts.usage_snapshot"],
            env={}, work_dir=code_root, restart_policy="no", type_="oneshot",
            user=user, group=user, env_file=env_file,
        ),
        Service(
            name="claude-soma-idle-reaper",
            description="Claude Soma idle lead reaper (run by timer)",
            exec_argv=[f"{venv_bin}/python", "-m", "claude_soma.scripts.idle_reaper"],
            env={}, work_dir=code_root, restart_policy="no", type_="oneshot",
            user=user, group=user, env_file=env_file,
        ),
        Service(
            name="claude-soma-rc-url-refresh",
            description="Claude Soma RC URL refresh (parse /remote-control menu, update registry)",
            exec_argv=[f"{venv_bin}/python", f"{code_root}/scripts/rc_url_refresh.py"],
            env={}, work_dir=code_root, restart_policy="no", type_="oneshot",
            user=user, group=user, env_file=env_file,
        ),
        Service(
            name="claude-soma-relay-cleanup",
            description="Claude Soma relay file cleanup (run by timer)",
            exec_argv=[f"{code_root}/scripts/relay_cleanup.sh"],
            env={}, work_dir=code_root, restart_policy="no", type_="oneshot",
            user=user, group=user, env_file=env_file,
        ),
    ]

    timers = [
        ("claude-soma-healthcheck",   "*:0/10"),
        ("claude-soma-cache-refresh", "*:0/5"),
        ("claude-soma-usage-snapshot", "*-*-* 23:55:00"),
        ("claude-soma-idle-reaper",   "0/6:00:00"),
        ("claude-soma-rc-url-refresh", "*-*-* 04:00:00 UTC"),
        ("claude-soma-relay-cleanup",  "*-*-* 04:15:00 UTC"),
    ]

    units: list[tuple[str, str]] = []
    for svc in services + timer_service_stubs:
        units.append((f"{svc.name}.service", _render_service(svc)))
    for timer_name, on_calendar in timers:
        units.append((
            f"{timer_name}.timer",
            _render_timer(timer_name, on_calendar, timer_name),
        ))
    return units


def _backfill_default_routines(paths: Paths | None = None) -> None:
    """Register the 4 system timers + the portfolio-oneliner bot timer."""
    from claude_soma.mcp_servers.project_orchestrator.registry import Registry  # noqa: PLC0415
    if paths is None:
        paths = _get_paths()
    db = os.environ.get("HERMES_ORCH_DB", str(paths.registry_db))
    reg = Registry(db)
    try:
        defaults = [
            ("healthcheck", "system",
             "every 10 min", "healthcheck",
             "Restart api/frontend/channel if any is down"),
            ("cache-refresh", "system",
             "every 5 min", "cache-refresh",
             "Prime hot dashboard API paths"),
            ("usage-snapshot", "system",
             "daily 23:55 UTC", "usage-snapshot",
             "Daily Max-credit usage snapshot"),
            ("idle-reaper", "system",
             "every 6h", "idle-reaper",
             "Hibernate idle project-leads >24h"),
            ("portfolio-oneliner", "bot",
             "Mon..Fri *-*-* 03:30:00", "portfolio-oneliner",
             "Weekday 09:00 IST portfolio brief"),
        ]
        for name, created_by, schedule, target_skill, description in defaults:
            reg.register_routine(
                name, kind="local", schedule=schedule,
                target_skill=target_skill, description=description,
                created_by=created_by,
            )
    finally:
        reg.close()


# ---- wizard flow ----------------------------------------------------------

def run() -> int:
    paths = _get_paths()

    print("Claude Soma setup wizard")
    print("========================\n")

    domain = prompt("Public dashboard domain", "claude.mayankgupta.in")
    if not validate_domain(domain):
        print(f"invalid domain: {domain!r}", file=sys.stderr)
        return 1

    email = prompt("Email for Let's Encrypt (optional)", "")

    print("\nMint a Claude Max OAuth token on a browser-capable machine:")
    print("    $ claude setup-token")
    print("Then paste the token here. (Starts with 'oat-')\n")
    token = prompt("CLAUDE_CODE_OAUTH_TOKEN", "")
    if token:
        # SECRETS NOTE: token written to disk (mode 600), never to stdout.
        write_secret("CLAUDE_CODE_OAUTH_TOKEN", token)

    print("\nGitHub OAuth app:")
    print("  1. https://github.com/settings/developers -> New OAuth App")
    print(f"  2. Callback: https://{domain}/api/auth/callback/github")
    gh_id = prompt("AUTH_GITHUB_ID", "")
    gh_secret = prompt("AUTH_GITHUB_SECRET", "")
    if gh_id:
        write_secret("AUTH_GITHUB_ID", gh_id)
    if gh_secret:
        # SECRETS NOTE: secret written to disk, never echoed.
        write_secret("AUTH_GITHUB_SECRET", gh_secret)
    write_secret("AUTH_SECRET",
                 subprocess.check_output(
                     ["openssl", "rand", "-hex", "32"], text=True,
                 ).strip())
    write_secret("AUTH_URL", f"https://{domain}")
    write_secret("AUTH_TRUST_HOST", "true")

    print("\nTelegram bot:")
    print("  1. In Telegram, message @BotFather: /newbot")
    print("  2. Save the bot token printed (starts with 123:ABC).")
    print("  3. Run:  claude   then  /plugin install telegram@claude-plugins-official")
    print("     then  /telegram:configure <bot-token>")
    print("     then DM the bot once, get pairing code, run /telegram:access pair <code>")
    print("     then  /telegram:access policy allowlist")
    input("\nPress Enter once Telegram is configured ... ")

    print("\nInstalling Caddyfile...")
    cf = render_caddyfile(domain, email)
    Path("/etc/caddy").mkdir(parents=True, exist_ok=True)
    Path("/etc/caddy/Caddyfile").write_text(cf)
    subprocess.run(["systemctl", "reload", "caddy"], check=True)

    print("Installing systemd units (rendered from platform layer)...")
    units = _build_wizard_services(paths)
    install_units(units)

    print("Enabling services and timers...")
    enable_services([
        "claude-soma-channel.service",
        "claude-soma-api.service",
        "claude-soma-frontend.service",
        "claude-soma-healthcheck.timer",
        "claude-soma-cache-refresh.timer",
        "claude-soma-usage-snapshot.timer",
        "claude-soma-idle-reaper.timer",
        "claude-soma-rc-url-refresh.timer",
    ])

    # Write rendered .mcp.json to ~/.mcp.json
    mcp_dest = paths.home / ".mcp.json"
    print(f"\nWriting rendered .mcp.json to {mcp_dest} …")
    mcp_content = render_mcp_json(paths)
    mcp_dest.parent.mkdir(parents=True, exist_ok=True)
    mcp_dest.write_text(mcp_content)
    os.chmod(mcp_dest, 0o600)

    # Backfill known routines into the registry so /api/routines has canonical
    # entries (instead of falling back to systemd-synthesised 'system' entries).
    _backfill_default_routines(paths)

    print("\nSetup complete.")
    print(f"  Public: https://{domain}/")
    print(f"  Admin:  https://{domain}/admin")
    return 0


def main() -> None:
    if os.geteuid() != 0:
        print("soma-init must run as root (use sudo)", file=sys.stderr)
        sys.exit(1)
    sys.exit(run())


if __name__ == "__main__":
    main()
