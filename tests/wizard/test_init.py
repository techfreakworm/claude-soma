from __future__ import annotations

from pathlib import Path

from claude_soma.mcp_servers.project_orchestrator.registry import Registry
from claude_soma.wizard.init import (
    _backfill_default_routines,
    render_caddyfile,
    render_systemd_unit,
    validate_domain,
)


def test_validate_domain_accepts_subdomain() -> None:
    assert validate_domain("claude.mayankgupta.in") is True


def test_validate_domain_rejects_invalid() -> None:
    assert validate_domain("not a domain") is False
    assert validate_domain("") is False


def test_render_caddyfile_includes_domain() -> None:
    out = render_caddyfile(domain="claude.mayankgupta.in", email="x@y.com")
    assert "claude.mayankgupta.in" in out
    assert "reverse_proxy localhost:9000" in out
    assert "reverse_proxy localhost:3000" in out


def test_render_systemd_unit_substitutes_paths() -> None:
    out = render_systemd_unit(
        name="claude-soma-api", description="API",
        exec_start="/usr/bin/echo ok",
    )
    assert "ExecStart=/usr/bin/echo ok" in out
    assert "Description=API" in out


def test_backfill_default_routines_registers_all_five(
    tmp_path: Path, monkeypatch
) -> None:
    db = tmp_path / "reg.sqlite"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))

    _backfill_default_routines()

    reg = Registry(db)
    try:
        routines = {r["name"]: r for r in reg.list_routines()}
    finally:
        reg.close()

    assert set(routines) == {
        "healthcheck", "cache-refresh", "usage-snapshot",
        "idle-reaper", "portfolio-oneliner",
    }
    for name in ("healthcheck", "cache-refresh", "usage-snapshot", "idle-reaper"):
        assert routines[name]["created_by"] == "system"
        assert routines[name]["kind"] == "local"
        assert routines[name]["metadata"] == {"unit": f"claude-soma-{name}.timer"}
    assert routines["portfolio-oneliner"]["created_by"] == "bot"
    assert routines["portfolio-oneliner"]["kind"] == "local"
    assert routines["portfolio-oneliner"]["target_skill"] == "portfolio-oneliner"
    assert routines["portfolio-oneliner"]["metadata"] == {
        "unit": "claude-soma-portfolio-oneliner.timer"
    }


def test_backfill_default_routines_is_idempotent(
    tmp_path: Path, monkeypatch
) -> None:
    db = tmp_path / "reg.sqlite"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))

    _backfill_default_routines()
    _backfill_default_routines()

    reg = Registry(db)
    try:
        routines = reg.list_routines()
    finally:
        reg.close()
    assert len(routines) == 5
