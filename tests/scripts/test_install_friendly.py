"""Tests for install-friendly UX helpers.

Verifies that scripts/lib-friendly.sh, scripts/bootstrap.sh,
scripts/finalize-caddy.sh, and INSTALL.md follow the friendly-error
UX principle: no raw systemd/pnpm/caddy error without a plain-language
explanation + exact next-step commands.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent


def test_lib_friendly_exists():
    assert (REPO_ROOT / "scripts" / "lib-friendly.sh").exists(), \
        "scripts/lib-friendly.sh must exist"


def test_lib_friendly_defines_helpers():
    content = (REPO_ROOT / "scripts" / "lib-friendly.sh").read_text()
    assert "friendly_warn()" in content, "lib-friendly.sh must define friendly_warn()"
    assert "friendly_halt()" in content, "lib-friendly.sh must define friendly_halt()"


def test_bootstrap_sources_lib_friendly():
    content = (REPO_ROOT / "scripts" / "bootstrap.sh").read_text()
    assert re.search(r'\bsource\b[^\n]*lib-friendly\.sh', content), \
        "bootstrap.sh must source lib-friendly.sh"


def test_bootstrap_creates_caddy_log_dir():
    content = (REPO_ROOT / "scripts" / "bootstrap.sh").read_text()
    assert "chown -R caddy:caddy /var/log/caddy" in content, \
        "bootstrap.sh must chown /var/log/caddy to caddy:caddy (BUG #5 fix)"


def test_bootstrap_step_13_uses_friendly_warn():
    content = (REPO_ROOT / "scripts" / "bootstrap.sh").read_text()
    idx_step13 = content.find("13/17")
    assert idx_step13 != -1, "bootstrap.sh must have a step 13/17 header"
    # Within the next ~3000 chars after step 13, friendly_warn must appear
    subsequent = content[idx_step13:idx_step13 + 3000]
    assert "friendly_warn" in subsequent, \
        "step 13/17 (Caddy install) must call friendly_warn for the not-yet-serving case"


def test_bootstrap_step_7_uses_friendly_halt_on_failure():
    content = (REPO_ROOT / "scripts" / "bootstrap.sh").read_text()
    idx_step7 = content.find("7/15")
    assert idx_step7 != -1, "bootstrap.sh must have a step 7/15 header"
    # Within the next ~1500 chars after step 7, friendly_halt must appear
    subsequent = content[idx_step7:idx_step7 + 1500]
    assert "friendly_halt" in subsequent, \
        "step 7/15 (frontend build) must wrap failure in friendly_halt"


def test_finalize_caddy_detects_not_active():
    content = (REPO_ROOT / "scripts" / "finalize-caddy.sh").read_text()
    assert "is-active" in content, \
        "finalize-caddy.sh must detect whether caddy.service is active (BUG #4 fix)"


def test_finalize_caddy_starts_if_not_active():
    content = (REPO_ROOT / "scripts" / "finalize-caddy.sh").read_text()
    assert "enable --now caddy" in content, \
        "finalize-caddy.sh must use 'enable --now caddy' when service is not active (BUG #4 fix)"


def test_finalize_caddy_creates_log_dir():
    content = (REPO_ROOT / "scripts" / "finalize-caddy.sh").read_text()
    assert "chown -R caddy:caddy /var/log/caddy" in content, \
        "finalize-caddy.sh must chown /var/log/caddy to caddy:caddy (BUG #5 fix)"


def test_install_md_documents_friendly_errors():
    content = (REPO_ROOT / "INSTALL.md").read_text()
    lower = content.lower()
    has_friendly_box = "friendly box" in lower
    has_never_crash = "never crash" in lower
    assert has_friendly_box or has_never_crash, \
        "INSTALL.md must document the friendly-error approach with 'friendly box' or 'never crash' wording"
