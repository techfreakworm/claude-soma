"""Tests for INSTALL-BUGS-10+11+13: correct auth env var names, de-personalization,
and Telegram pairing guide.

BUG #10: AUTH_GITHUB_CLIENT_ID / AUTH_GITHUB_CLIENT_SECRET were wrong (ignored by NextAuth v5).
         Canonical names are AUTH_GITHUB_ID + AUTH_GITHUB_SECRET.
BUG #11: Hardcoded personal identifiers (techfreakworm, 935376085, mayankgupta.in) in shipped
         artifacts must be replaced by configurable env vars.
BUG #13: Telegram bot pairing flow was missing/undocumented. Added setup-telegram.sh.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent

SECRETS_EXAMPLE = REPO_ROOT / "secrets.env.example"
INSTALL_MD = REPO_ROOT / "INSTALL.md"
ENV_COPILOT = REPO_ROOT / "scripts" / "env-copilot-prompt.txt"
SETUP_TELEGRAM = REPO_ROOT / "scripts" / "setup-telegram.sh"
SYSTEMD_DIR = REPO_ROOT / "systemd"
SCRIPTS_DIR = REPO_ROOT / "scripts"


# ---------------------------------------------------------------------------
# BUG #10 — correct NextAuth v5 env var names
# ---------------------------------------------------------------------------


def test_secrets_env_uses_auth_github_id():
    """secrets.env.example must declare AUTH_GITHUB_ID= (not AUTH_GITHUB_CLIENT_ID=)."""
    text = SECRETS_EXAMPLE.read_text()
    assert "AUTH_GITHUB_ID=" in text, (
        "secrets.env.example must contain AUTH_GITHUB_ID= (NextAuth v5 canonical name)"
    )


def test_secrets_env_uses_auth_github_secret():
    """secrets.env.example must declare AUTH_GITHUB_SECRET= (not AUTH_GITHUB_CLIENT_SECRET=)."""
    text = SECRETS_EXAMPLE.read_text()
    assert "AUTH_GITHUB_SECRET=" in text, (
        "secrets.env.example must contain AUTH_GITHUB_SECRET= (NextAuth v5 canonical name)"
    )


def test_secrets_env_no_client_id_key():
    """secrets.env.example must NOT declare AUTH_GITHUB_CLIENT_ID= (NextAuth v5 ignores it)."""
    text = SECRETS_EXAMPLE.read_text()
    assert "AUTH_GITHUB_CLIENT_ID=" not in text, (
        "secrets.env.example must not contain AUTH_GITHUB_CLIENT_ID= — NextAuth v5 ignores it"
    )


def test_secrets_env_no_client_secret_key():
    """secrets.env.example must NOT declare AUTH_GITHUB_CLIENT_SECRET=."""
    text = SECRETS_EXAMPLE.read_text()
    assert "AUTH_GITHUB_CLIENT_SECRET=" not in text, (
        "secrets.env.example must not contain AUTH_GITHUB_CLIENT_SECRET= — NextAuth v5 ignores it"
    )


def test_env_copilot_uses_correct_auth_names():
    """env-copilot-prompt.txt must reference AUTH_GITHUB_ID + AUTH_GITHUB_SECRET as the
    primary names to set. It may mention AUTH_GITHUB_CLIENT_ID only as a negative warning
    ("Do NOT use ..."), but must not instruct the user to set it."""
    text = ENV_COPILOT.read_text()
    assert "AUTH_GITHUB_ID" in text, (
        "env-copilot-prompt.txt must reference AUTH_GITHUB_ID"
    )
    assert "AUTH_GITHUB_SECRET" in text, (
        "env-copilot-prompt.txt must reference AUTH_GITHUB_SECRET"
    )
    # The copilot prompt may mention AUTH_GITHUB_CLIENT_ID in a "Do NOT use" warning,
    # but it must not have a sed command that writes AUTH_GITHUB_CLIENT_ID= to secrets.env.
    assert "AUTH_GITHUB_CLIENT_ID=<value>" not in text, (
        "env-copilot-prompt.txt must not instruct writing AUTH_GITHUB_CLIENT_ID into secrets.env"
    )
    assert "AUTH_GITHUB_CLIENT_SECRET=<value>" not in text, (
        "env-copilot-prompt.txt must not instruct writing AUTH_GITHUB_CLIENT_SECRET into secrets.env"
    )


def test_install_md_uses_correct_auth_names():
    """INSTALL.md must reference AUTH_GITHUB_ID + AUTH_GITHUB_SECRET in the Step 5 table."""
    text = INSTALL_MD.read_text()
    assert "AUTH_GITHUB_ID" in text, "INSTALL.md must reference AUTH_GITHUB_ID"
    assert "AUTH_GITHUB_SECRET" in text, "INSTALL.md must reference AUTH_GITHUB_SECRET"


# ---------------------------------------------------------------------------
# BUG #11 — no hardcoded personal identifiers in shipped artifacts
# ---------------------------------------------------------------------------


def test_secrets_env_has_allowed_github_handles():
    """secrets.env.example must declare HERMES_ALLOWED_GITHUB_HANDLES= (required for login)."""
    text = SECRETS_EXAMPLE.read_text()
    assert "HERMES_ALLOWED_GITHUB_HANDLES=" in text, (
        "secrets.env.example must contain HERMES_ALLOWED_GITHUB_HANDLES="
    )


def test_no_hardcoded_techfreakworm_in_systemd():
    """No systemd *.service file may contain the literal string 'techfreakworm'."""
    service_files = list(SYSTEMD_DIR.glob("*.service"))
    assert service_files, "No .service files found in systemd/"
    offenders = []
    for f in service_files:
        if "techfreakworm" in f.read_text():
            offenders.append(f.name)
    assert not offenders, (
        f"These systemd units hardcode 'techfreakworm' — move to EnvironmentFile: {offenders}"
    )


def test_no_hardcoded_techfreakworm_in_scripts():
    """No scripts/*.sh file may contain 'techfreakworm' outside of comment lines."""
    sh_files = list(SCRIPTS_DIR.glob("*.sh"))
    assert sh_files, "No .sh files found in scripts/"
    offenders = []
    for f in sh_files:
        for lineno, line in enumerate(f.read_text().splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if "techfreakworm" in line:
                offenders.append(f"{f.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "These script lines hardcode 'techfreakworm' (outside comments):\n"
        + "\n".join(offenders)
    )


def test_no_hardcoded_chat_id_in_systemd():
    """No systemd *.service file may hardcode the numeric chat ID 935376085."""
    service_files = list(SYSTEMD_DIR.glob("*.service"))
    offenders = []
    for f in service_files:
        if "935376085" in f.read_text():
            offenders.append(f.name)
    assert not offenders, (
        f"These systemd units hardcode chat ID 935376085: {offenders}"
    )


def test_no_hardcoded_chat_id_in_scripts():
    """No scripts/*.sh may hardcode 935376085 outside comment lines."""
    sh_files = list(SCRIPTS_DIR.glob("*.sh"))
    offenders = []
    for f in sh_files:
        for lineno, line in enumerate(f.read_text().splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if "935376085" in line:
                offenders.append(f"{f.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "These script lines hardcode chat ID 935376085 (outside comments):\n"
        + "\n".join(offenders)
    )


def test_secrets_env_no_hardcoded_owner():
    """secrets.env.example must not have a hardcoded username as a default value."""
    text = SECRETS_EXAMPLE.read_text()
    assert "AUTH_GITHUB_OWNER=techfreakworm" not in text, (
        "secrets.env.example must not hardcode AUTH_GITHUB_OWNER=techfreakworm"
    )
    assert "HERMES_ALLOWED_GITHUB_HANDLES=techfreakworm" not in text, (
        "secrets.env.example must not hardcode HERMES_ALLOWED_GITHUB_HANDLES=techfreakworm"
    )


# ---------------------------------------------------------------------------
# BUG #13 — Telegram pairing guide exists
# ---------------------------------------------------------------------------


def test_setup_telegram_script_exists():
    """scripts/setup-telegram.sh must exist as the guided Telegram pairing script."""
    assert SETUP_TELEGRAM.exists(), (
        "scripts/setup-telegram.sh must exist — it guides the operator through Telegram pairing"
    )


def test_setup_telegram_script_is_executable():
    """scripts/setup-telegram.sh must be executable."""
    import os
    assert os.access(SETUP_TELEGRAM, os.X_OK), (
        "scripts/setup-telegram.sh must be executable (chmod +x)"
    )


def test_setup_telegram_script_reads_token():
    """setup-telegram.sh must read TELEGRAM_BOT_TOKEN from secrets.env."""
    text = SETUP_TELEGRAM.read_text()
    assert "TELEGRAM_BOT_TOKEN" in text, (
        "setup-telegram.sh must reference TELEGRAM_BOT_TOKEN"
    )
    assert "secrets.env" in text, (
        "setup-telegram.sh must read from secrets.env"
    )


def test_setup_telegram_script_writes_access_json():
    """setup-telegram.sh must write to access.json (the plugin allowlist)."""
    text = SETUP_TELEGRAM.read_text()
    assert "access.json" in text, (
        "setup-telegram.sh must update ~/.claude/channels/telegram/access.json"
    )


def test_install_md_documents_telegram_pairing():
    """INSTALL.md must document the Telegram pairing flow with setup-telegram.sh."""
    text = INSTALL_MD.read_text()
    assert "setup-telegram.sh" in text, (
        "INSTALL.md must reference scripts/setup-telegram.sh for the pairing flow"
    )
    assert "access.json" in text or "pairing" in text.lower(), (
        "INSTALL.md must explain the Telegram pairing flow"
    )


def test_env_copilot_mentions_telegram_pairing():
    """env-copilot-prompt.txt must reference the Telegram pairing flow."""
    text = ENV_COPILOT.read_text()
    assert "setup-telegram.sh" in text or "access.json" in text or "pairing" in text.lower(), (
        "env-copilot-prompt.txt must mention the Telegram pairing step"
    )
