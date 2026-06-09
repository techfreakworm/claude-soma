"""Tests for oauth.py — CSRF state store, and FastAPI OAuth routes.

Route tests use FastAPI TestClient with mock platform adapters injected
via app.state — no real HTTP calls.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from claude_soma.social.oauth import StateStore
from claude_soma.social.platforms import Token


# ---------------------------------------------------------------------------
# StateStore unit tests
# ---------------------------------------------------------------------------

class TestStateStore:
    def test_generate_returns_unique_states(self) -> None:
        store = StateStore()
        s1 = store.generate("instagram")
        s2 = store.generate("threads")
        assert s1 != s2
        assert len(s1) > 10

    def test_validate_and_consume_returns_platform(self) -> None:
        store = StateStore()
        state = store.generate("instagram")
        platform = store.validate_and_consume(state)
        assert platform == "instagram"

    def test_validate_single_use(self) -> None:
        store = StateStore()
        state = store.generate("threads")
        store.validate_and_consume(state)
        with pytest.raises(ValueError, match="Invalid or already-used"):
            store.validate_and_consume(state)

    def test_validate_unknown_state_raises(self) -> None:
        store = StateStore()
        with pytest.raises(ValueError, match="Invalid or already-used"):
            store.validate_and_consume("totally_unknown_state")

    def test_validate_expired_state_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = StateStore()
        state = store.generate("facebook_page")
        # Freeze time 20 minutes in the future so the token appears expired.
        future = int(time.time()) + 1200
        monkeypatch.setattr(
            "claude_soma.social.oauth.time.time",
            lambda: future,
        )
        with pytest.raises(ValueError, match="expired"):
            store.validate_and_consume(state)

    def test_prune_expired_removes_old(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = StateStore()
        store.generate("instagram")
        store.generate("threads")
        # Freeze time past the TTL.
        future = int(time.time()) + 700
        monkeypatch.setattr(
            "claude_soma.social.oauth.time.time",
            lambda: future,
        )
        removed = store.prune_expired()
        assert removed == 2


# ---------------------------------------------------------------------------
# FastAPI route tests (TestClient)
# ---------------------------------------------------------------------------

def _make_mock_token(platform: str, account_id: str = "test_user") -> Token:
    now = int(time.time())
    return Token(
        platform=platform,
        account_id=account_id,
        token="test_long_lived_token",
        token_type="bearer",
        issued_at=now,
        expires_at=now + 5_184_000,
        last_refreshed=None,
        scopes="test_scope",
    )


@pytest.fixture()
def social_client(tmp_path: Path) -> TestClient:
    """TestClient with mocked adapters and a tmp vault."""
    import os
    os.environ["HERMES_SOCIAL_DB"] = str(tmp_path / "test.sqlite")
    os.environ["META_APP_ID"] = "META_ID"
    os.environ["META_APP_SECRET"] = "META_SEC"
    os.environ["IG_APP_ID"] = "IG_ID"
    os.environ["IG_APP_SECRET"] = "IG_SEC"
    os.environ["THREADS_APP_ID"] = "TH_ID"
    os.environ["THREADS_APP_SECRET"] = "TH_SEC"
    os.environ["WEBHOOK_VERIFY_TOKEN"] = "webhook_tok"

    from claude_soma.social.main import app
    from claude_soma.social.oauth import StateStore
    from claude_soma.social.platforms import Platform

    # Build mock adapters.
    def _make_mock_adapter(name: str) -> MagicMock:
        m = MagicMock(spec=Platform)
        m.name = name
        m.authorize_url.return_value = f"https://example.com/auth?platform={name}"
        m.exchange_code.return_value = _make_mock_token(name)
        return m

    mock_adapters: dict[str, Any] = {
        "instagram": _make_mock_adapter("instagram"),
        "threads": _make_mock_adapter("threads"),
        "facebook_page": _make_mock_adapter("facebook_page"),
    }

    client = TestClient(app, raise_server_exceptions=True)
    # Override app.state after startup.
    with client:
        app.state.adapters = mock_adapters
        app.state.state_store = StateStore()
        yield client  # type: ignore[misc]


class TestOAuthStartRoute:
    def test_start_redirects_to_authorize_url(self, social_client: TestClient) -> None:
        resp = social_client.get("/oauth/start?platform=instagram", follow_redirects=False)
        assert resp.status_code == 302
        assert "example.com/auth" in resp.headers["location"]

    def test_start_unknown_platform_returns_400(self, social_client: TestClient) -> None:
        resp = social_client.get("/oauth/start?platform=twitter")
        assert resp.status_code == 400

    def test_start_stores_state_in_url(self, social_client: TestClient) -> None:
        resp = social_client.get("/oauth/start?platform=threads", follow_redirects=False)
        assert resp.status_code == 302


class TestOAuthCallbackRoute:
    def test_callback_happy_path(self, social_client: TestClient, tmp_path: Path) -> None:
        # Generate a valid state.
        from claude_soma.social.main import app
        state = app.state.state_store.generate("instagram")

        resp = social_client.get(f"/oauth/callback?code=AUTH_CODE&state={state}")
        assert resp.status_code == 200
        assert "Authorization successful" in resp.text
        assert "instagram" in resp.text
        # Ensure no raw token in the response.
        assert "test_long_lived_token" not in resp.text

    def test_callback_bad_state_returns_400(self, social_client: TestClient) -> None:
        resp = social_client.get("/oauth/callback?code=X&state=INVALID_STATE_XYZ")
        assert resp.status_code == 400

    def test_callback_missing_code_returns_400(self, social_client: TestClient) -> None:
        from claude_soma.social.main import app
        state = app.state.state_store.generate("instagram")
        resp = social_client.get(f"/oauth/callback?state={state}")
        assert resp.status_code == 400

    def test_callback_missing_state_returns_400(self, social_client: TestClient) -> None:
        resp = social_client.get("/oauth/callback?code=X")
        assert resp.status_code == 400

    def test_callback_provider_error_returns_400(self, social_client: TestClient) -> None:
        resp = social_client.get(
            "/oauth/callback?error=access_denied&error_reason=user_denied"
        )
        assert resp.status_code == 400

    def test_callback_exchange_error_returns_502(self, social_client: TestClient) -> None:
        from claude_soma.social.main import app
        state = app.state.state_store.generate("threads")
        # Make the adapter raise.
        app.state.adapters["threads"].exchange_code.side_effect = RuntimeError(
            "threads exchange failed"
        )
        resp = social_client.get(f"/oauth/callback?code=CODE&state={state}")
        assert resp.status_code == 502

    def test_state_is_single_use(self, social_client: TestClient) -> None:
        from claude_soma.social.main import app
        state = app.state.state_store.generate("instagram")
        resp1 = social_client.get(f"/oauth/callback?code=C1&state={state}")
        assert resp1.status_code == 200
        # Second use → 400.
        resp2 = social_client.get(f"/oauth/callback?code=C2&state={state}")
        assert resp2.status_code == 400


class TestHealthRoute:
    def test_health_returns_200_json(self, social_client: TestClient) -> None:
        resp = social_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "platforms" in data

    def test_health_no_raw_tokens(self, social_client: TestClient) -> None:
        resp = social_client.get("/health")
        text = resp.text
        assert "test_long_lived_token" not in text

    def test_health_platform_entries_present(self, social_client: TestClient) -> None:
        resp = social_client.get("/health")
        data = resp.json()
        platform_names = {p["platform"] for p in data["platforms"]}
        assert "instagram" in platform_names
        assert "threads" in platform_names
        assert "facebook_page" in platform_names


class TestTokensStatusRoute:
    def test_tokens_status_no_raw_tokens(self, social_client: TestClient) -> None:
        resp = social_client.get("/api/tokens/status")
        assert resp.status_code == 200
        # Verify structure — list of dicts, no raw token field exposed by accident.
        rows = resp.json()
        for row in rows:
            assert "token" not in row  # raw token must not be in the response
            assert "platform" in row
            assert "expires_at" in row
