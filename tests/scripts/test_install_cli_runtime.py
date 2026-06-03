"""tests/scripts/test_install_cli_runtime.py — verify bootstrap fixes for bugs 9 and 12.

Bug 9: somux + soma* CLI helpers not installed by bootstrap.
Bug 12: bun runtime not installed by bootstrap (Telegram plugin MCP server requires it).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
BOOTSTRAP = REPO_ROOT / "scripts" / "bootstrap.sh"
SMOKE = REPO_ROOT / "scripts" / "smoke_install.sh"
INSTALL_MD = REPO_ROOT / "INSTALL.md"

SOMA_CLI_HELPERS = ["somux", "soma-relay", "soma-publish"]


# ---------------------------------------------------------------------------
# BUG #12 — bun runtime
# ---------------------------------------------------------------------------


def test_bootstrap_installs_bun():
    """bootstrap.sh must install bun via the official curl installer.
    The Telegram plugin spawns 'bun server.ts' as an MCP child of claude --channels;
    without bun the bot cannot start on a fresh VPS."""
    content = BOOTSTRAP.read_text()
    assert "bun.sh/install" in content, (
        "scripts/bootstrap.sh must install bun via 'curl -fsSL https://bun.sh/install | bash'"
    )


def test_bootstrap_bun_uses_as_ubuntu():
    """bun must be installed as the ubuntu user so it lands in /home/ubuntu/.bun/bin/bun,
    not /root/.bun/bin/bun (bootstrap can run as root)."""
    content = BOOTSTRAP.read_text()
    assert "as_ubuntu" in content and "bun.sh/install" in content, (
        "scripts/bootstrap.sh must use the as_ubuntu wrapper when installing bun "
        "so the binary lands in /home/ubuntu/.bun/bin/bun"
    )


def test_bootstrap_symlinks_bun_to_usr_local_bin():
    """bootstrap.sh must symlink bun to /usr/local/bin/bun so systemd units and
    tmux subshells that do not source ~/.bashrc can find it."""
    content = BOOTSTRAP.read_text()
    assert "/usr/local/bin/bun" in content, (
        "scripts/bootstrap.sh must symlink bun to /usr/local/bin/bun for "
        "system-wide visibility (systemd units, tmux subshells)"
    )


# ---------------------------------------------------------------------------
# BUG #9 — soma* CLI helpers
# ---------------------------------------------------------------------------


def test_bootstrap_installs_soma_cli_helpers():
    """bootstrap.sh must symlink soma* helper scripts onto /usr/local/bin/.
    Operators need somux / soma-relay / soma-publish on PATH after a fresh install."""
    content = BOOTSTRAP.read_text()
    assert "somux" in content and "/usr/local/bin/" in content, (
        "scripts/bootstrap.sh must install soma* CLI helpers to /usr/local/bin/"
    )
    assert "soma-relay" in content, (
        "scripts/bootstrap.sh must install soma-relay to /usr/local/bin/"
    )


def test_somux_script_exists_in_repo():
    """scripts/somux must be committed to the repo so bootstrap can symlink it."""
    somux = REPO_ROOT / "scripts" / "somux"
    assert somux.exists() and somux.is_file(), (
        "scripts/somux is not present in the repo; bootstrap cannot install it"
    )


def test_soma_relay_script_exists_in_repo():
    """scripts/soma-relay must be committed to the repo."""
    relay = REPO_ROOT / "scripts" / "soma-relay"
    assert relay.exists() and relay.is_file(), (
        "scripts/soma-relay is not present in the repo; bootstrap cannot install it"
    )


def test_soma_publish_script_exists_in_repo():
    """scripts/soma-publish must be committed to the repo."""
    publish = REPO_ROOT / "scripts" / "soma-publish"
    assert publish.exists() and publish.is_file(), (
        "scripts/soma-publish is not present in the repo; bootstrap cannot install it"
    )


# ---------------------------------------------------------------------------
# INSTALL.md documentation
# ---------------------------------------------------------------------------


def test_install_md_lists_bun_dependency():
    """INSTALL.md must mention bun so operators know the Telegram plugin requires it."""
    content = INSTALL_MD.read_text()
    assert "bun" in content, (
        "INSTALL.md must mention bun (the Telegram plugin's MCP server runtime)"
    )


def test_install_md_lists_soma_cli_helpers():
    """INSTALL.md must document the soma* CLI helpers installed by bootstrap."""
    content = INSTALL_MD.read_text()
    assert "somux" in content, (
        "INSTALL.md must document somux (the tmux session manager)"
    )
    assert "soma-relay" in content, (
        "INSTALL.md must document soma-relay (the file relay helper)"
    )


# ---------------------------------------------------------------------------
# smoke_install.sh checks
# ---------------------------------------------------------------------------


def test_smoke_install_checks_bun():
    """smoke_install.sh must verify bun is installed after bootstrap."""
    content = SMOKE.read_text()
    assert "bun" in content, (
        "scripts/smoke_install.sh must check that the bun runtime is installed"
    )


def test_smoke_install_checks_somux():
    """smoke_install.sh must verify somux is on PATH after bootstrap."""
    content = SMOKE.read_text()
    assert "somux" in content, (
        "scripts/smoke_install.sh must check that somux is installed"
    )


# ---------------------------------------------------------------------------
# Bash syntax
# ---------------------------------------------------------------------------


def test_bash_syntax_bootstrap():
    result = subprocess.run(
        ["bash", "-n", str(BOOTSTRAP)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"bash -n scripts/bootstrap.sh failed:\n{result.stderr}"
    )


def test_bash_syntax_smoke_install():
    result = subprocess.run(
        ["bash", "-n", str(SMOKE)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"bash -n scripts/smoke_install.sh failed:\n{result.stderr}"
    )
