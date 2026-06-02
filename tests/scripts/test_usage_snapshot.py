import json
import subprocess
import sys
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock
import pytest

# Add the scripts directory to sys.path so we can import usage_snapshot
scripts_dir = Path(__file__).parent.parent.parent / "scripts"
sys.path.append(str(scripts_dir))

import usage_snapshot
from usage_snapshot import _extract


def test_extract_valid_payload():
    payload = {
        "interactive_credits_used": 10.5,
        "interactive_credits_ceiling": 100.0,
        "agent_sdk_credits_used": 5.2,
        "agent_sdk_credits_ceiling": 50.0
    }
    iu, ic, su, sc = _extract(payload)
    assert iu == 10.5
    assert ic == 100.0
    assert su == 5.2
    assert sc == 50.0

def test_extract_missing_values():
    payload = {}
    iu, ic, su, sc = _extract(payload)
    assert iu == 0.0
    assert ic == 0.0
    assert su == 0.0
    assert sc == 0.0

def test_extract_null_values():
    payload = {
        "interactive_credits_used": None,
        "interactive_credits_ceiling": None,
        "agent_sdk_credits_used": None,
        "agent_sdk_credits_ceiling": None
    }
    iu, ic, su, sc = _extract(payload)
    assert iu == 0.0
    assert ic == 0.0
    assert su == 0.0
    assert sc == 0.0

def test_extract_string_values():
    payload = {
        "interactive_credits_used": "12.3",
        "interactive_credits_ceiling": "150",
        "agent_sdk_credits_used": "7.8",
        "agent_sdk_credits_ceiling": "80"
    }
    iu, ic, su, sc = _extract(payload)
    assert iu == 12.3
    assert ic == 150.0
    assert su == 7.8
    assert sc == 80.0

def test_extract_malformed_strings():
    # Current implementation might fail here if not fixed
    payload = {
        "interactive_credits_used": "abc",
        "interactive_credits_ceiling": "",
    }
    # We want it to be robust and return 0.0 for things it can't parse
    iu, ic, su, sc = _extract(payload)
    assert iu == 0.0
    assert ic == 0.0


def test_timer_is_daily():
    """Confirm the systemd timer fires once daily, not every 15 minutes.

    Rationale: CLAUDE_CODE_OAUTH_TOKEN authenticates against claude.ai, not
    api.anthropic.com. A direct HTTP call to api.anthropic.com/v1/usage would
    fail with 401 (wrong auth domain). The safe path is one daily subprocess
    call to `claude -p /usage` which uses the OAuth token through the claude
    CLI's own auth stack.
    """
    timer_file = Path(__file__).parent.parent.parent / "systemd" / "claude-soma-usage-snapshot.timer"
    content = timer_file.read_text()
    assert "OnCalendar=*-*-* 23:55:00" in content, (
        f"Timer should fire once daily at 23:55; got:\n{content}"
    )
    assert "OnCalendar=*:0/15" not in content, (
        "15-minute interval must not be present — it causes 96 claude spawns/day"
    )


def test_scan_excludes_other_days(tmp_path, monkeypatch):
    today = datetime.now(timezone.utc).date().isoformat()
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()

    proj_dir = tmp_path / ".claude" / "projects" / "proj"
    proj_dir.mkdir(parents=True)

    line_yesterday = json.dumps({
        "type": "assistant",
        "timestamp": f"{yesterday}T10:00:00.000Z",
        "message": {
            "role": "assistant",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "service_tier": "standard",
            },
        },
    })
    line_today = json.dumps({
        "type": "assistant",
        "timestamp": f"{today}T10:00:00.000Z",
        "message": {
            "role": "assistant",
            "usage": {
                "input_tokens": 200,
                "output_tokens": 75,
                "cache_creation_input_tokens": 25,
                "service_tier": "standard",
            },
        },
    })

    (proj_dir / "session.jsonl").write_text(line_yesterday + "\n" + line_today + "\n")

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    result = usage_snapshot._query_usage()

    # Only today's tokens: 200 + 75 + 25 = 300; yesterday's 150 excluded
    assert result["interactive_credits_used"] == 300.0
    assert result["agent_sdk_credits_used"] == 0.0


def test_scan_tier_split(tmp_path, monkeypatch):
    today = datetime.now(timezone.utc).date().isoformat()

    proj_dir = tmp_path / ".claude" / "projects" / "proj"
    proj_dir.mkdir(parents=True)

    line_batch = json.dumps({
        "type": "assistant",
        "timestamp": f"{today}T11:00:00.000Z",
        "message": {
            "role": "assistant",
            "usage": {
                "input_tokens": 300,
                "output_tokens": 100,
                "cache_creation_input_tokens": 50,
                "service_tier": "batch",
            },
        },
    })
    line_standard = json.dumps({
        "type": "assistant",
        "timestamp": f"{today}T12:00:00.000Z",
        "message": {
            "role": "assistant",
            "usage": {
                "input_tokens": 400,
                "output_tokens": 150,
                "cache_creation_input_tokens": 0,
                "service_tier": "standard",
            },
        },
    })

    (proj_dir / "session.jsonl").write_text(line_batch + "\n" + line_standard + "\n")

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    result = usage_snapshot._query_usage()

    # batch: 300 + 100 + 50 = 450
    assert result["agent_sdk_credits_used"] == 450.0
    # standard: 400 + 150 + 0 = 550
    assert result["interactive_credits_used"] == 550.0


def test_no_subprocess_called(tmp_path, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called in usage_snapshot")

    monkeypatch.setattr(subprocess, "run", fail_if_called)
    monkeypatch.setattr(usage_snapshot, "DB", str(tmp_path / "usage.sqlite"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    result = usage_snapshot.main()
    assert result == 0


def test_today_filter_uses_utc_not_local(tmp_path, monkeypatch):
    """Parser counts only entries whose timestamp prefix matches the UTC date.

    Simulates a moment where IST local date is "2026-06-02" (03:30 IST) but UTC
    date is still "2026-06-01" (22:00 UTC). An IST-based filter would count the
    wrong day; a UTC-based filter counts the correct day.
    """
    # UTC 2026-06-01T22:00:00Z = IST 2026-06-02T03:30:00+05:30
    utc_time = datetime(2026, 6, 1, 22, 0, 0, tzinfo=timezone.utc)
    mock_dt = MagicMock()
    mock_dt.now.return_value = utc_time
    monkeypatch.setattr(usage_snapshot, "datetime", mock_dt)

    proj_dir = tmp_path / ".claude" / "projects" / "proj"
    proj_dir.mkdir(parents=True)

    # UTC June 1 entry — should be counted (matches UTC today "2026-06-01")
    line_utc_today = json.dumps({
        "type": "assistant",
        "timestamp": "2026-06-01T21:00:00Z",
        "message": {
            "usage": {
                "input_tokens": 200,
                "output_tokens": 75,
                "cache_creation_input_tokens": 25,
                "service_tier": "standard",
            }
        },
    })
    # UTC June 2 entry — should NOT be counted (different UTC day)
    line_utc_next = json.dumps({
        "type": "assistant",
        "timestamp": "2026-06-02T00:30:00Z",
        "message": {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "service_tier": "standard",
            }
        },
    })

    (proj_dir / "session.jsonl").write_text(line_utc_today + "\n" + line_utc_next + "\n")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    result = usage_snapshot._query_usage()

    # Only UTC-June-1 entry: 200 + 75 + 25 = 300 tokens
    assert result["interactive_credits_used"] == 300.0
    assert result["agent_sdk_credits_used"] == 0.0


def test_ceiling_env_unset_returns_zero_ceiling(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_INTERACTIVE_CEILING", raising=False)
    monkeypatch.delenv("HERMES_AGENT_SDK_CEILING", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    result = usage_snapshot._query_usage()

    assert result["interactive_credits_ceiling"] == 0.0
    assert result["agent_sdk_credits_ceiling"] == 0.0


def test_ceiling_env_set_returns_correct_value(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_INTERACTIVE_CEILING", "10000")
    monkeypatch.setenv("HERMES_AGENT_SDK_CEILING", "20000")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    result = usage_snapshot._query_usage()

    assert result["interactive_credits_ceiling"] == 10000.0
    assert result["agent_sdk_credits_ceiling"] == 20000.0
