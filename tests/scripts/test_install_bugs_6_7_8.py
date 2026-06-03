"""tests/scripts/test_install_bugs_6_7_8.py — verify bootstrap fixes for bugs 6, 7, and 8.

Bug 6: tmux server not running before channel service starts.
Bug 7: native claude binary not installed for ubuntu user (was using $HOME=/root).
Bug 8: ufw ports not opened + no cloud-provider firewall guidance.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
BOOTSTRAP = REPO_ROOT / "scripts" / "bootstrap.sh"
SHOW_DNS = REPO_ROOT / "scripts" / "show-dns-setup.sh"
SMOKE = REPO_ROOT / "scripts" / "smoke_install.sh"
INSTALL_MD = REPO_ROOT / "INSTALL.md"
CHANNEL_CLAUDE = REPO_ROOT / "scripts" / "channel-claude.sh"


# ---------------------------------------------------------------------------
# BUG #7 — native claude binary install
# ---------------------------------------------------------------------------


def test_bootstrap_installs_native_claude():
    """bootstrap.sh must run 'claude install latest' as ubuntu to put the native
    binary at /home/ubuntu/.local/bin/claude. The npm /usr/bin/claude does not
    support --channels; only the native binary does."""
    content = BOOTSTRAP.read_text()
    assert "claude install latest" in content, (
        "scripts/bootstrap.sh must invoke 'claude install latest' to install the "
        "native claude binary required for --channels (Telegram bot)"
    )


def test_bootstrap_native_claude_uses_as_ubuntu():
    """The native install must run as ubuntu, not root, so the binary lands in
    /home/ubuntu/.local/bin/claude rather than /root/.local/bin/claude."""
    content = BOOTSTRAP.read_text()
    assert "as_ubuntu" in content and "claude install latest" in content, (
        "scripts/bootstrap.sh must use 'as_ubuntu ... claude install latest' to "
        "ensure the native binary is installed for the ubuntu user, not root"
    )


# ---------------------------------------------------------------------------
# BUG #6 — tmux server pre-warm
# ---------------------------------------------------------------------------


def test_bootstrap_creates_tmux_session_for_channel():
    """bootstrap.sh must pre-warm the tmux server + create a placeholder hermes
    session before the channel service starts, to avoid 'no server running on
    /tmp/tmux-1001/default' when ExecStartPre tries kill-session on a fresh box."""
    content = BOOTSTRAP.read_text()
    channel_claude_content = CHANNEL_CLAUDE.read_text()
    bootstrap_has_hermes_tmux = (
        "tmux new-session" in content and "hermes" in content
    )
    # Acceptable alternative: channel-claude.sh patched to not require pre-existing server
    channel_patched = "start-server" in channel_claude_content
    assert bootstrap_has_hermes_tmux or channel_patched, (
        "scripts/bootstrap.sh must create a tmux hermes session before the channel "
        "service starts, OR channel-claude.sh must be patched to not require a "
        "pre-existing tmux server"
    )


# ---------------------------------------------------------------------------
# BUG #8 — ufw on-box firewall
# ---------------------------------------------------------------------------


def test_bootstrap_opens_ufw_ports():
    """bootstrap.sh must open ports 22/80/443 in ufw (if active) to prevent
    on-box firewall from blocking Caddy's ACME challenge and HTTPS traffic."""
    content = BOOTSTRAP.read_text()
    assert "ufw allow 22/tcp" in content, (
        "scripts/bootstrap.sh must run 'ufw allow 22/tcp' (SSH first, to avoid lockout)"
    )
    assert "ufw allow 80/tcp" in content, (
        "scripts/bootstrap.sh must run 'ufw allow 80/tcp' (Caddy ACME + HTTP)"
    )
    assert "ufw allow 443/tcp" in content, (
        "scripts/bootstrap.sh must run 'ufw allow 443/tcp' (Caddy HTTPS)"
    )


# ---------------------------------------------------------------------------
# BUG #8 — cloud-provider firewall guidance in show-dns-setup.sh
# ---------------------------------------------------------------------------


def test_show_dns_setup_has_cloud_firewall_block():
    """show-dns-setup.sh must include provider-specific cloud-firewall instructions
    for all four major providers. Called at bootstrap step 16/17, this ensures
    every fresh install sees the guidance regardless of provider."""
    content = SHOW_DNS.read_text()
    assert "Oracle Cloud" in content, (
        "scripts/show-dns-setup.sh must mention 'Oracle Cloud' in the cloud-firewall block"
    )
    assert "AWS" in content, (
        "scripts/show-dns-setup.sh must mention 'AWS' in the cloud-firewall block"
    )
    assert "GCP" in content, (
        "scripts/show-dns-setup.sh must mention 'GCP' in the cloud-firewall block"
    )
    assert "Security List" in content, (
        "scripts/show-dns-setup.sh must mention 'Security List' (OCI term) in the "
        "cloud-firewall block"
    )


def test_show_dns_setup_explains_acme_challenge():
    """show-dns-setup.sh must explain that the cloud-provider firewall blocks Caddy's
    ACME TLS challenge, so the operator understands WHY port 80 must be open."""
    content = SHOW_DNS.read_text()
    has_acme_or_tls = "ACME" in content or "TLS challenge" in content
    assert has_acme_or_tls, (
        "scripts/show-dns-setup.sh must mention 'ACME' or 'TLS challenge' to explain "
        "why port 80 must be open at the cloud-provider layer"
    )


# ---------------------------------------------------------------------------
# INSTALL.md documentation
# ---------------------------------------------------------------------------


def test_install_md_documents_two_prerequisites():
    """INSTALL.md must document both external prerequisites (DNS + cloud-provider
    firewall) in a clearly-labelled section."""
    content = INSTALL_MD.read_text()
    assert "Two external prerequisites" in content, (
        "INSTALL.md must contain a 'Two external prerequisites' section"
    )
    assert "cloud-provider firewall" in content, (
        "INSTALL.md must mention 'cloud-provider firewall' in the prerequisites section"
    )


# ---------------------------------------------------------------------------
# smoke_install.sh checks
# ---------------------------------------------------------------------------


def test_smoke_install_checks_native_claude():
    """smoke_install.sh must verify that the native claude binary is present at
    /home/ubuntu/.local/bin/claude (the path channel-claude.sh hardcodes)."""
    content = SMOKE.read_text()
    assert "/home/ubuntu/.local/bin/claude" in content, (
        "scripts/smoke_install.sh must check for the native claude binary at "
        "/home/ubuntu/.local/bin/claude"
    )


def test_smoke_install_checks_tmux_hermes():
    """smoke_install.sh must verify that a tmux hermes session is available after
    the channel service has started."""
    content = SMOKE.read_text()
    assert "tmux -L hermes ls" in content or "tmux ls" in content, (
        "scripts/smoke_install.sh must check that a tmux hermes session is running"
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


def test_bash_syntax_show_dns():
    result = subprocess.run(
        ["bash", "-n", str(SHOW_DNS)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"bash -n scripts/show-dns-setup.sh failed:\n{result.stderr}"
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
