"""Tests for scripts/caddy-files-render.sh.

Uses fake `caddy`, `sudo`, and `systemctl` binaries to test the hash
substitution and import-directive logic without touching live system files.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "caddy-files-render.sh"
TEMPLATE = Path(__file__).resolve().parents[2] / "caddy" / "files.caddyfile.in"

# Use a bcrypt-like hash with no special shell chars in the echo output.
# Single-quote the variable in the echo so bash does NOT expand $2a etc.
FAKE_HASH = r"$2a$14$fakehashfortesting000000000000000000000000000000000"

# Fake caddy: prints hash with single-quotes to prevent shell expansion of $
_FAKE_CADDY_SCRIPT = r"""echo '$2a$14$fakehashfortesting000000000000000000000000000000000'"""

# Fake sudo: pass through all commands EXCEPT chown (which would fail without root)
_FAKE_SUDO_SCRIPT = """\
case "$1" in
    chown) exit 0 ;;
    *) exec "$@" ;;
esac
"""


def _make_fake_bin(bin_dir: Path, name: str, script: str) -> Path:
    """Write a fake binary to bin_dir/name and make it executable."""
    p = bin_dir / name
    p.write_text(f"#!/usr/bin/env bash\n{script}\n")
    p.chmod(0o755)
    return p


def _setup_env(tmp_path: Path, with_password: bool = True) -> tuple[Path, dict]:
    """Set up a fresh fake environment under tmp_path, return (bin_dir, env)."""
    bin_dir = tmp_path / "fake_bin"
    bin_dir.mkdir(exist_ok=True)

    _make_fake_bin(bin_dir, "caddy", _FAKE_CADDY_SCRIPT)
    _make_fake_bin(bin_dir, "sudo", _FAKE_SUDO_SCRIPT)
    _make_fake_bin(bin_dir, "systemctl", "exit 0")

    conf_d = tmp_path / "conf.d"
    conf_d.mkdir(exist_ok=True)

    caddyfile = tmp_path / "Caddyfile"
    if not caddyfile.exists():
        caddyfile.write_text(
            "# Caddyfile\n{\n    email test@example.com\n}\n\n"
            "soma.example.com {\n    handle {\n        reverse_proxy localhost:3000\n    }\n}\n"
        )

    secrets_file = tmp_path / "secrets.env"
    if with_password:
        secrets_file.write_text("HERMES_FILES_PASSWORD=testpassword123\n")
    else:
        secrets_file.write_text("SOME_OTHER_VAR=value\n")
    secrets_file.chmod(0o600)

    env = {k: v for k, v in os.environ.items() if k not in ("HERMES_FILES_PASSWORD", "FILES_DOMAIN")}
    env.update({
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "HERMES_SECRETS_FILE": str(secrets_file),
        "HERMES_CADDY_CONF_D": str(conf_d),
        "HERMES_CADDYFILE": str(caddyfile),
    })
    return bin_dir, env


def _run_render(tmp_path: Path, extra_env: dict | None = None, with_password: bool = True) -> subprocess.CompletedProcess:
    """Run caddy-files-render.sh in a fully mocked environment."""
    _, env = _setup_env(tmp_path, with_password=with_password)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
    )


def test_render_substitutes_hash(tmp_path: Path) -> None:
    result = _run_render(tmp_path)
    assert result.returncode == 0, result.stderr

    dest = tmp_path / "conf.d" / "files.caddyfile"
    assert dest.exists(), "rendered file should be written to conf.d"
    content = dest.read_text()
    assert FAKE_HASH in content, "bcrypt hash should appear in rendered config"
    assert "__BCRYPT_HASH__" not in content, "placeholder should be substituted"
    assert "files.mayankgupta.in" in content, "site block should be present"
    assert "basicauth" in content, "basicauth directive should be present"


def test_render_adds_import_directive(tmp_path: Path) -> None:
    result = _run_render(tmp_path)
    assert result.returncode == 0, result.stderr

    caddyfile = tmp_path / "Caddyfile"
    content = caddyfile.read_text()
    assert "import /etc/caddy/conf.d/*.caddyfile" in content


def test_render_import_is_idempotent(tmp_path: Path) -> None:
    # Run twice: import directive should appear exactly once in the Caddyfile
    _run_render(tmp_path)
    result = _run_render(tmp_path)
    assert result.returncode == 0, result.stderr

    caddyfile = tmp_path / "Caddyfile"
    content = caddyfile.read_text()
    count = content.count("import /etc/caddy/conf.d/*.caddyfile")
    assert count == 1, f"Import directive should appear exactly once, found {count}"


def test_render_fails_when_password_missing(tmp_path: Path) -> None:
    result = _run_render(tmp_path, with_password=False)
    assert result.returncode != 0
    assert "HERMES_FILES_PASSWORD" in result.stderr


def test_template_has_placeholder() -> None:
    content = TEMPLATE.read_text()
    assert "__BCRYPT_HASH__" in content, "Template must contain __BCRYPT_HASH__ placeholder"
    assert "__FILES_DOMAIN__" in content, "Template must contain __FILES_DOMAIN__ placeholder"
    assert "basicauth" in content
    assert "soma" in content


def test_files_domain_default(tmp_path: Path) -> None:
    result = _run_render(tmp_path)
    assert result.returncode == 0, result.stderr

    dest = tmp_path / "conf.d" / "files.caddyfile"
    content = dest.read_text()
    assert "files.mayankgupta.in" in content, "default domain should appear in rendered config"
    assert "__FILES_DOMAIN__" not in content, "__FILES_DOMAIN__ placeholder should be substituted"


def test_files_domain_override(tmp_path: Path) -> None:
    result = _run_render(tmp_path, extra_env={"FILES_DOMAIN": "custom.example.com"})
    assert result.returncode == 0, result.stderr

    dest = tmp_path / "conf.d" / "files.caddyfile"
    content = dest.read_text()
    assert "custom.example.com" in content, "overridden domain should appear in rendered config"
    assert "files.mayankgupta.in" not in content, "default domain should not appear when overridden"
    assert "__FILES_DOMAIN__" not in content, "__FILES_DOMAIN__ placeholder should be substituted"
