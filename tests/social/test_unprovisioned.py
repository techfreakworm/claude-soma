"""Tests for unprovisioned-boot robustness.

The service must boot cleanly (no exception, no crash-loop) when
~/.config/social-manager/meta-tokens.env is absent and no credentials are in
the environment.  Routes that need config return 503; /health reports
provisioned:false; the refresh entrypoint exits 0.

Env isolation: HERMES_SOCIAL_CONFIG points at a nonexistent path and every
credential key is removed from os.environ via monkeypatch, so
SocialConfig.from_env() raises ValueError.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Every required key SocialConfig.from_env() asserts on.
_REQUIRED_KEYS = (
    "META_APP_ID",
    "META_APP_SECRET",
    "IG_APP_ID",
    "IG_APP_SECRET",
    "THREADS_APP_ID",
    "THREADS_APP_SECRET",
    "WEBHOOK_VERIFY_TOKEN",
)


@pytest.fixture()
def unprovisioned_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Force an unprovisioned environment: no config file, no creds in env."""
    # Point the config loader at a path that does not exist.
    monkeypatch.setenv("HERMES_SOCIAL_CONFIG", str(tmp_path / "does-not-exist.env"))
    # Use a tmp vault path so nothing real is touched.
    monkeypatch.setenv("HERMES_SOCIAL_DB", str(tmp_path / "social.sqlite"))
    # Remove every credential key that may be lingering in os.environ.
    for key in _REQUIRED_KEYS:
        monkeypatch.delenv(key, raising=False)


# ---------------------------------------------------------------------------
# App boot + routes
# ---------------------------------------------------------------------------

def test_app_boots_unprovisioned(unprovisioned_env: None) -> None:
    """Lifespan must run without raising when config is absent."""
    from claude_soma.social.main import app

    # The `with` block runs the lifespan startup/shutdown.
    with TestClient(app) as client:
        # cfg is None and adapters is empty after an unprovisioned boot.
        assert app.state.cfg is None
        assert app.state.adapters == {}
        # The state store is still created so OAuth state minting works later.
        assert app.state.state_store is not None
        # A trivial request succeeds.
        resp = client.get("/health")
        assert resp.status_code == 200


def test_health_reports_unprovisioned(unprovisioned_env: None) -> None:
    from claude_soma.social.main import app

    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["provisioned"] is False
        # No platform/token detail is leaked in the unprovisioned shape.
        assert "platforms" not in data


def test_oauth_start_returns_503(unprovisioned_env: None) -> None:
    from claude_soma.social.main import app

    with TestClient(app) as client:
        resp = client.get("/oauth/start?platform=instagram")
        assert resp.status_code == 503
        assert "not provisioned" in resp.json()["detail"]
        assert "docs/social-provisioning.md" in resp.json()["detail"]


def test_oauth_callback_returns_503(unprovisioned_env: None) -> None:
    from claude_soma.social.main import app

    with TestClient(app) as client:
        resp = client.get("/oauth/callback?code=X&state=Y")
        assert resp.status_code == 503
        assert "not provisioned" in resp.json()["detail"]


def test_tokens_status_returns_503(unprovisioned_env: None) -> None:
    from claude_soma.social.main import app

    with TestClient(app) as client:
        resp = client.get("/api/tokens/status")
        assert resp.status_code == 503
        assert "not provisioned" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Refresh entrypoint
# ---------------------------------------------------------------------------

def test_refresh_main_exits_zero_unprovisioned(unprovisioned_env: None) -> None:
    from claude_soma.social.refresh import main

    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
