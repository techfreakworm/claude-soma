from __future__ import annotations

from claude_soma.wizard.init import (
    render_systemd_unit, render_caddyfile, validate_domain
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
