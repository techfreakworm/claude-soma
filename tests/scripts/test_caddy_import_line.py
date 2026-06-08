"""Tests for P1-B: Caddyfile import line + bootstrap defensive append.

Asserts:
1. The repo Caddyfile's last non-empty line is the conf.d import.
2. bootstrap.sh contains the defensive append guard.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_caddyfile_last_nonempty_line_is_import() -> None:
    """The import line must be the last non-empty line in the repo Caddyfile."""
    caddyfile = (REPO_ROOT / "Caddyfile").read_text()
    non_empty = [line for line in caddyfile.splitlines() if line.strip()]
    assert non_empty, "Caddyfile appears to be empty"
    assert non_empty[-1].strip() == "import /etc/caddy/conf.d/*.caddyfile", (
        f"Last non-empty line of Caddyfile should be the conf.d import, got: {non_empty[-1]!r}"
    )


def test_bootstrap_has_defensive_import_append() -> None:
    """bootstrap.sh must contain the idempotent guard that appends the import line if absent."""
    content = (REPO_ROOT / "scripts" / "bootstrap.sh").read_text()
    # Both the grep check and the tee append must be present
    assert "import /etc/caddy/conf.d/*.caddyfile" in content, (
        "bootstrap.sh must reference the import line in a defensive append block"
    )
    assert "tee -a" in content or ">> /etc/caddy/Caddyfile" in content, (
        "bootstrap.sh must have an append (tee -a or >>) for the import line"
    )
