"""Interactive setup wizard: clean Ubuntu VPS to working claude-soma in ~30 min.

Designed to be re-run safely (idempotent) on the OCI VPS. Reads/writes
/etc/claude-soma/secrets.env, /etc/systemd/system/claude-soma-*, /etc/caddy/Caddyfile.

Run as: sudo soma-init
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from typing import Iterable


REPO_ROOT = Path("/opt/claude-soma")
SECRETS = Path("/etc/claude-soma/secrets.env")


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


def render_systemd_unit(*, name: str, description: str, exec_start: str,
                        type_: str = "simple", user: str = "ubuntu",
                        env_file: str = "/etc/claude-soma/secrets.env",
                        wd: str = "/opt/claude-soma",
                        restart_sec: int = 5) -> str:
    return dedent(f"""\
        [Unit]
        Description={description}
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type={type_}
        User={user}
        Group={user}
        WorkingDirectory={wd}
        EnvironmentFile={env_file}
        Environment=PATH=/opt/claude-soma/.venv/bin:/usr/local/bin:/usr/bin:/bin
        ExecStart={exec_start}
        Restart=always
        RestartSec={restart_sec}
        StandardOutput=append:/var/log/claude-soma/{name}.log
        StandardError=append:/var/log/claude-soma/{name}.err.log

        [Install]
        WantedBy=multi-user.target
        """)


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
    SECRETS.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if SECRETS.exists():
        for ln in SECRETS.read_text().splitlines():
            if not ln.strip() or ln.strip().startswith("#") or "=" not in ln:
                lines.append(ln)
                continue
            k, _, _ = ln.partition("=")
            if k.strip() != name:
                lines.append(ln)
    lines.append(f"{name}={value}")
    SECRETS.write_text("\n".join(lines) + "\n")
    os.chmod(SECRETS, 0o600)


def install_units(units: Iterable[tuple[str, str]]) -> None:
    """Install systemd unit files. Each entry is (path, contents)."""
    for path, contents in units:
        target = Path("/etc/systemd/system") / Path(path).name
        target.write_text(contents)
        target.chmod(0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)


def enable_services(names: Iterable[str]) -> None:
    cmd = ["systemctl", "enable", "--now", *names]
    subprocess.run(cmd, check=True)


def _backfill_default_routines() -> None:
    """Register the 4 system timers + the portfolio-oneliner bot timer."""
    from claude_soma.mcp_servers.project_orchestrator.registry import Registry
    db = os.environ.get("HERMES_ORCH_DB", "/opt/claude-soma/registry.sqlite")
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
        write_secret("CLAUDE_CODE_OAUTH_TOKEN", token)

    print("\nGitHub OAuth app:")
    print("  1. https://github.com/settings/developers -> New OAuth App")
    print(f"  2. Callback: https://{domain}/api/auth/callback/github")
    gh_id = prompt("AUTH_GITHUB_ID", "")
    gh_secret = prompt("AUTH_GITHUB_SECRET", "")
    if gh_id:
        write_secret("AUTH_GITHUB_ID", gh_id)
    if gh_secret:
        write_secret("AUTH_GITHUB_SECRET", gh_secret)
    write_secret("AUTH_SECRET",
                 subprocess.check_output(["openssl", "rand", "-hex", "32"],
                                         text=True).strip())
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

    print("Installing systemd units...")
    subprocess.run(["cp", "-f",
                    *list((REPO_ROOT / "systemd").glob("*.service")),
                    *list((REPO_ROOT / "systemd").glob("*.timer")),
                    "/etc/systemd/system/"], check=True)
    subprocess.run(["systemctl", "daemon-reload"], check=True)

    print("Enabling services and timers...")
    enable_services([
        "claude-soma-channel.service",
        "claude-soma-api.service",
        "claude-soma-frontend.service",
        "claude-soma-healthcheck.timer",
        "claude-soma-cache-refresh.timer",
        "claude-soma-usage-snapshot.timer",
        "claude-soma-idle-reaper.timer",
    ])

    # Backfill known routines into the registry so /api/routines has canonical
    # entries (instead of falling back to systemd-synthesized 'system' entries).
    _backfill_default_routines()

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
