"""tests/scripts/test_show_dns_setup.py — syntax + content checks for show-dns-setup.sh."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
SHOW_DNS = REPO_ROOT / "scripts" / "show-dns-setup.sh"
BOOTSTRAP = REPO_ROOT / "scripts" / "bootstrap.sh"
INSTALL_MD = REPO_ROOT / "INSTALL.md"
README_MD = REPO_ROOT / "README.md"


def test_bash_syntax_show_dns():
    result = subprocess.run(
        ["bash", "-n", str(SHOW_DNS)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"


def test_script_executable():
    assert os.access(str(SHOW_DNS), os.X_OK), f"{SHOW_DNS} is not executable"


def test_fetches_ipv4_via_multiple_services():
    content = SHOW_DNS.read_text()
    assert "api.ipify.org" in content
    assert "ifconfig.me" in content
    assert "icanhazip.com" in content


def test_handles_check_flag():
    content = SHOW_DNS.read_text()
    assert "--check" in content


def test_handles_missing_domain_gracefully():
    content = SHOW_DNS.read_text()
    assert "<your-domain>" in content


def test_handles_missing_ip_gracefully():
    content = SHOW_DNS.read_text()
    assert "<YOUR_VPS_PUBLIC_IPV4>" in content


def test_bootstrap_invokes_show_dns():
    content = BOOTSTRAP.read_text()
    assert "show-dns-setup.sh" in content


def test_install_md_references_dns_step():
    content = INSTALL_MD.read_text()
    assert "Add DNS records" in content


def test_readme_quickstart_notes_dns():
    content = README_MD.read_text()
    quickstart_pos = content.find("## Quickstart")
    assert quickstart_pos != -1, "## Quickstart section not found"
    quickstart_section = content[quickstart_pos:]
    assert "DNS" in quickstart_section, "DNS not mentioned in Quickstart section"
