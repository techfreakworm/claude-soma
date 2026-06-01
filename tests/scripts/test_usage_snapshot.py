import sys
import os
from pathlib import Path
import pytest

# Add the scripts directory to sys.path so we can import usage_snapshot
scripts_dir = Path(__file__).parent.parent.parent / "scripts"
sys.path.append(str(scripts_dir))

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
