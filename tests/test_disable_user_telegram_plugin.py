"""Tests for scripts/disable-user-telegram-plugin.sh.

The root fix for the poller hijack (docs/KNOWN_BUGS.md #1) removes telegram from
user-scope enabledPlugins so non-bot sessions can't load it. This script must do
that surgically (only the telegram key), reversibly (timestamped backup), and
idempotently.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "disable-user-telegram-plugin.sh"


def _run(settings_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True, text=True, timeout=30,
        env={"PATH": "/usr/bin:/bin", "CLAUDE_USER_SETTINGS": str(settings_path)},
    )


def test_removes_only_telegram_and_preserves_other_keys(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text(json.dumps({
        "enabledPlugins": {"telegram@claude-plugins-official": True, "other@x": True},
        "skipDangerousModePermissionPrompt": True,
        "theme": "dark",
    }))
    r = _run(s)
    assert r.returncode == 0, r.stderr
    data = json.loads(s.read_text())
    assert data["enabledPlugins"] == {"other@x": True}
    assert data["skipDangerousModePermissionPrompt"] is True
    assert data["theme"] == "dark"
    backups = list(tmp_path.glob("settings.json.bak.*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text())["enabledPlugins"][
        "telegram@claude-plugins-official"
    ] is True


def test_idempotent_second_run_makes_no_change(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text(json.dumps({"enabledPlugins": {"telegram@claude-plugins-official": True}}))
    assert _run(s).returncode == 0
    first_backups = list(tmp_path.glob("settings.json.bak.*"))
    assert len(first_backups) == 1
    after_first = s.read_text()

    r2 = _run(s)
    assert r2.returncode == 0
    assert "already absent" in r2.stdout
    assert s.read_text() == after_first
    assert list(tmp_path.glob("settings.json.bak.*")) == first_backups


def test_missing_file_is_noop(tmp_path):
    r = _run(tmp_path / "nope.json")
    assert r.returncode == 0
    assert "nothing to do" in r.stdout
