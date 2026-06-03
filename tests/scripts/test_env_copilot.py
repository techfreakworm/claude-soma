"""Tests for scripts/env-copilot-prompt.txt and the bootstrap FINAL STEP block."""

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent

COPILOT_PROMPT = REPO_ROOT / "scripts" / "env-copilot-prompt.txt"
BOOTSTRAP = REPO_ROOT / "scripts" / "bootstrap.sh"
INSTALL_MD = REPO_ROOT / "INSTALL.md"


def test_env_copilot_prompt_exists():
    assert COPILOT_PROMPT.exists(), "scripts/env-copilot-prompt.txt must exist"


def test_env_copilot_prompt_has_role_statement():
    text = COPILOT_PROMPT.read_text()
    assert "secrets-setup copilot" in text


def test_env_copilot_prompt_lists_required_keys():
    text = COPILOT_PROMPT.read_text()
    required_keys = [
        "CLAUDE_CODE_OAUTH_TOKEN",
        "AUTH_GITHUB_ID",
        "AUTH_GITHUB_SECRET",
        "HERMES_ALLOWED_GITHUB_HANDLES",
        "NEXTAUTH_SECRET",
        "NEXTAUTH_URL",
        "TELEGRAM_BOT_TOKEN",
        "HERMES_NOTIFY_CHAT_ID",
        "HERMES_FILES_PASSWORD",
    ]
    for key in required_keys:
        assert key in text, f"env-copilot-prompt.txt is missing required key: {key}"


def test_env_copilot_prompt_warns_no_echo():
    text = COPILOT_PROMPT.read_text()
    assert "NEVER echo" in text, "prompt must warn Claude to never echo secret values"


def test_env_copilot_prompt_mentions_chmod_600():
    text = COPILOT_PROMPT.read_text()
    assert "chmod 600" in text, "prompt must reference chmod 600 to protect secrets file"


def test_env_copilot_prompt_mentions_no_api_key():
    text = COPILOT_PROMPT.read_text()
    assert (
        "NOT an Anthropic API key" in text
        or "NO API key" in text
        or "Max OAuth" in text
    ), "prompt must clarify that CLAUDE_CODE_OAUTH_TOKEN is a Max OAuth token, not an API key"


def test_bootstrap_invokes_final_step():
    text = BOOTSTRAP.read_text()
    assert "FINAL STEP" in text, "bootstrap.sh must print a FINAL STEP block"
    assert "env-copilot-prompt.txt" in text, (
        "bootstrap.sh FINAL STEP must reference env-copilot-prompt.txt"
    )


def test_install_md_documents_both_options():
    text = INSTALL_MD.read_text()
    assert "Option A" in text, "INSTALL.md must document Option A (manual)"
    assert "Option B" in text, "INSTALL.md must document Option B (Claude copilot)"
    assert "env-copilot-prompt" in text, "INSTALL.md must reference env-copilot-prompt"


def test_install_md_references_bootstrap_final_step():
    text = INSTALL_MD.read_text()
    assert (
        "FINAL STEP" in text or "17/17" in text or "step 17" in text.lower()
    ), "INSTALL.md must reference the bootstrap's FINAL STEP / step 17/17 output"
