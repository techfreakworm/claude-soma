"""Tests for Caddy install artefacts: Caddyfile import, files.caddyfile presence, secrets template."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_caddyfile_has_import_line():
    caddyfile = (REPO_ROOT / "Caddyfile").read_text()
    assert "import /etc/caddy/conf.d/*.caddyfile" in caddyfile


def test_caddyfile_has_soma_site_block():
    caddyfile = (REPO_ROOT / "Caddyfile").read_text()
    assert "soma.mayankgupta.in" in caddyfile


def test_caddyfile_has_upload_handle_block():
    caddyfile = (REPO_ROOT / "Caddyfile").read_text()
    assert "handle /api/admin/upload/*" in caddyfile


def test_files_caddyfile_committed():
    assert (REPO_ROOT / "caddy" / "files.caddyfile").exists()


def test_files_caddyfile_has_binary_matcher():
    content = (REPO_ROOT / "caddy" / "files.caddyfile").read_text()
    assert "@binary" in content or "file_server" in content


def test_files_caddyfile_has_markserv_route():
    content = (REPO_ROOT / "caddy" / "files.caddyfile").read_text()
    assert "18081" in content


def test_files_caddyfile_in_deprecated():
    path = REPO_ROOT / "caddy" / "files.caddyfile.in"
    if not path.exists():
        return
    assert "DEPRECATED" in path.read_text()


def test_secrets_env_example_exists():
    assert (REPO_ROOT / "secrets.env.example").exists()


def test_secrets_env_example_has_required_keys():
    content = (REPO_ROOT / "secrets.env.example").read_text()
    required = [
        "CLAUDE_CODE_OAUTH_TOKEN",
        "AUTH_GITHUB_CLIENT_ID",
        "AUTH_GITHUB_CLIENT_SECRET",
        "TELEGRAM_BOT_TOKEN",
        "HERMES_NOTIFY_CHAT_ID",
        "HERMES_FILES_PASSWORD",
    ]
    for key in required:
        assert key in content, f"Missing required key: {key}"


def test_frontend_service_uses_node():
    content = (REPO_ROOT / "systemd" / "claude-soma-frontend.service").read_text()
    for line in content.splitlines():
        if line.strip().startswith("ExecStart="):
            assert "node" in line, f"ExecStart does not use node: {line}"
            return
    raise AssertionError("ExecStart line not found in frontend service")


def test_build_frontend_has_standalone_static_cp():
    content = (REPO_ROOT / "scripts" / "build_frontend.sh").read_text()
    assert ".next/standalone/.next/static" in content
