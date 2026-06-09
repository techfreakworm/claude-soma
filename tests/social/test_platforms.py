"""Tests for platforms.py — per-platform adapter OAuth flows.

Uses a custom httpx transport stub so no real HTTP calls are made.
No external dependencies (no respx).
"""

from __future__ import annotations

import json
import time
import urllib.parse

import httpx
import pytest

from claude_soma.social.platforms import (
    FacebookPagePlatform,
    InstagramPlatform,
    ThreadsPlatform,
    Token,
    make_platform_registry,
)


# ---------------------------------------------------------------------------
# Fake httpx transport
# ---------------------------------------------------------------------------

class _FakeTransport(httpx.BaseTransport):
    """Returns pre-canned responses in FIFO order."""

    def __init__(self, responses: list[tuple[int, dict[str, object]]]) -> None:
        self._queue = list(responses)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if not self._queue:
            raise AssertionError(
                f"Unexpected request to {request.url} — no more canned responses."
            )
        status_code, body = self._queue.pop(0)
        return httpx.Response(
            status_code,
            headers={"content-type": "application/json"},
            content=json.dumps(body).encode(),
            request=request,
        )


def _fake_client(responses: list[tuple[int, dict[str, object]]]) -> httpx.Client:
    return httpx.Client(transport=_FakeTransport(responses))


# ---------------------------------------------------------------------------
# InstagramPlatform
# ---------------------------------------------------------------------------

class TestInstagramPlatform:
    _IG = InstagramPlatform(
        app_id="IG_APP_ID",
        app_secret="IG_APP_SECRET",
        redirect_uri="https://social.mayankgupta.in/oauth/callback",
    )

    def test_authorize_url_shape(self) -> None:
        url = self._IG.authorize_url("state_abc")
        parsed = urllib.parse.urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == "www.instagram.com"
        assert parsed.path == "/oauth/authorize"
        params = dict(urllib.parse.parse_qsl(parsed.query))
        assert params["client_id"] == "IG_APP_ID"
        assert params["state"] == "state_abc"
        assert params["response_type"] == "code"
        assert "instagram_business_basic" in params["scope"]

    def test_exchange_code_returns_token(self) -> None:
        now = int(time.time())
        client = _fake_client([
            (200, {"access_token": "short_ig", "token_type": "bearer", "expires_in": 3600,
                   "user_id": "26101391282871017"}),
            (200, {"access_token": "long_ig", "token_type": "bearer",
                   "expires_in": 5184000}),
        ])
        ig = InstagramPlatform("IG_APP_ID", "IG_APP_SECRET",
                               "https://social.mayankgupta.in/oauth/callback", client)
        tok = ig.exchange_code("auth_code_123")
        assert tok.platform == "instagram"
        assert tok.account_id == "26101391282871017"
        assert tok.token == "long_ig"
        assert tok.token_type == "bearer"
        assert tok.expires_at is not None
        assert tok.expires_at > now + 5_100_000
        assert tok.issued_at >= now

    def test_exchange_code_step1_error_raises(self) -> None:
        client = _fake_client([(400, {"error": {"message": "bad code"}})])
        ig = InstagramPlatform("IG_APP_ID", "IG_APP_SECRET",
                               "https://social.mayankgupta.in/oauth/callback", client)
        with pytest.raises(RuntimeError, match="instagram short-lived exchange failed"):
            ig.exchange_code("bad_code")

    def test_refresh_token(self) -> None:
        now = int(time.time())
        client = _fake_client([
            (200, {"access_token": "refreshed_ig", "token_type": "bearer",
                   "expires_in": 5184000}),
        ])
        ig = InstagramPlatform("IG_APP_ID", "IG_APP_SECRET",
                               "https://social.mayankgupta.in/oauth/callback", client)
        old = Token(
            platform="instagram", account_id="26101391282871017",
            token="old_tok", token_type="bearer",
            issued_at=now - 2 * 86400,
            expires_at=now + 5 * 86400,
            last_refreshed=None, scopes="instagram_business_basic",
        )
        new = ig.refresh(old)
        assert new is not None
        assert new.token == "refreshed_ig"
        assert new.last_refreshed is not None
        assert new.last_refreshed >= now

    def test_refresh_too_young_raises(self) -> None:
        now = int(time.time())
        ig = InstagramPlatform("IG_APP_ID", "IG_APP_SECRET",
                               "https://social.mayankgupta.in/oauth/callback")
        old = Token(
            platform="instagram", account_id="u1",
            token="tok", token_type="bearer",
            issued_at=now - 3600,  # only 1h old
            expires_at=now + 3 * 86400,
            last_refreshed=None, scopes="",
        )
        with pytest.raises(RuntimeError, match="24 h"):
            ig.refresh(old)

    def test_refresh_error_raises(self) -> None:
        now = int(time.time())
        client = _fake_client([(400, {"error": {"message": "token invalid"}})])
        ig = InstagramPlatform("IG_APP_ID", "IG_APP_SECRET",
                               "https://social.mayankgupta.in/oauth/callback", client)
        old = Token(
            platform="instagram", account_id="u1",
            token="tok", token_type="bearer",
            issued_at=now - 2 * 86400,
            expires_at=now + 3 * 86400,
            last_refreshed=None, scopes="",
        )
        with pytest.raises(RuntimeError, match="instagram refresh failed"):
            ig.refresh(old)


# ---------------------------------------------------------------------------
# ThreadsPlatform
# ---------------------------------------------------------------------------

class TestThreadsPlatform:
    _TH = ThreadsPlatform(
        app_id="TH_APP_ID",
        app_secret="TH_APP_SECRET",
        redirect_uri="https://social.mayankgupta.in/oauth/callback",
    )

    def test_authorize_url_shape(self) -> None:
        url = self._TH.authorize_url("state_xyz")
        parsed = urllib.parse.urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == "threads.net"
        assert parsed.path == "/oauth/authorize"
        params = dict(urllib.parse.parse_qsl(parsed.query))
        assert params["client_id"] == "TH_APP_ID"
        assert params["state"] == "state_xyz"
        assert "threads_basic" in params["scope"]

    def test_exchange_code_returns_token(self) -> None:
        now = int(time.time())
        client = _fake_client([
            (200, {"access_token": "short_th", "user_id": "36190611627220761"}),
            (200, {"access_token": "long_th", "token_type": "bearer",
                   "expires_in": 5184000}),
        ])
        th = ThreadsPlatform("TH_APP_ID", "TH_APP_SECRET",
                             "https://social.mayankgupta.in/oauth/callback", client)
        tok = th.exchange_code("th_code")
        assert tok.platform == "threads"
        assert tok.account_id == "36190611627220761"
        assert tok.token == "long_th"
        assert tok.expires_at is not None
        assert tok.expires_at > now

    def test_exchange_code_step1_error_raises(self) -> None:
        client = _fake_client([(400, {"error": "bad_verification_code"})])
        th = ThreadsPlatform("TH_APP_ID", "TH_APP_SECRET",
                             "https://social.mayankgupta.in/oauth/callback", client)
        with pytest.raises(RuntimeError, match="threads short-lived exchange failed"):
            th.exchange_code("bad_code")

    def test_refresh_token(self) -> None:
        now = int(time.time())
        client = _fake_client([
            (200, {"access_token": "refreshed_th", "token_type": "bearer",
                   "expires_in": 5184000}),
        ])
        th = ThreadsPlatform("TH_APP_ID", "TH_APP_SECRET",
                             "https://social.mayankgupta.in/oauth/callback", client)
        old = Token(
            platform="threads", account_id="th_user",
            token="old_th", token_type="bearer",
            issued_at=now - 2 * 86400,
            expires_at=now + 4 * 86400,
            last_refreshed=None, scopes="threads_basic",
        )
        new = th.refresh(old)
        assert new is not None
        assert new.token == "refreshed_th"
        assert new.last_refreshed is not None

    def test_refresh_too_young_raises(self) -> None:
        now = int(time.time())
        th = ThreadsPlatform("TH_APP_ID", "TH_APP_SECRET",
                             "https://social.mayankgupta.in/oauth/callback")
        old = Token(
            platform="threads", account_id="u1",
            token="tok", token_type="bearer",
            issued_at=now - 7200,  # 2h old
            expires_at=now + 5 * 86400,
            last_refreshed=None, scopes="",
        )
        with pytest.raises(RuntimeError, match="24 h"):
            th.refresh(old)


# ---------------------------------------------------------------------------
# FacebookPagePlatform
# ---------------------------------------------------------------------------

class TestFacebookPagePlatform:
    _FB = FacebookPagePlatform(
        app_id="META_APP_ID",
        app_secret="META_APP_SECRET",
        redirect_uri="https://social.mayankgupta.in/oauth/callback",
        page_id="1042685982267843",
    )

    def test_authorize_url_shape(self) -> None:
        url = self._FB.authorize_url("state_fb")
        parsed = urllib.parse.urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == "www.facebook.com"
        assert parsed.path == "/dialog/oauth"
        params = dict(urllib.parse.parse_qsl(parsed.query))
        assert params["client_id"] == "META_APP_ID"
        assert "pages_show_list" in params["scope"]

    def test_exchange_code_returns_page_token(self) -> None:
        now = int(time.time())
        client = _fake_client([
            # Step 1: code → short user token
            (200, {"access_token": "short_user_tok", "token_type": "bearer",
                   "expires_in": 3600}),
            # Step 2: short user → long user token (fb_exchange_token)
            (200, {"access_token": "long_user_tok", "token_type": "bearer",
                   "expires_in": 5184000}),
            # Step 3: /me/accounts → list of pages
            (200, {"data": [
                {"id": "1042685982267843",
                 "access_token": "PAGE_TOKEN_NONEXPIRING",
                 "name": "My Page"},
            ]}),
        ])
        fb = FacebookPagePlatform("META_APP_ID", "META_APP_SECRET",
                                  "https://social.mayankgupta.in/oauth/callback",
                                  "1042685982267843", client)
        tok = fb.exchange_code("fb_code")
        assert tok.platform == "facebook_page"
        assert tok.account_id == "1042685982267843"
        assert tok.token == "PAGE_TOKEN_NONEXPIRING"
        assert tok.token_type == "page_token"
        assert tok.expires_at is None  # non-expiring
        assert tok.issued_at >= now

    def test_exchange_code_page_not_found_raises(self) -> None:
        client = _fake_client([
            (200, {"access_token": "short_user", "token_type": "bearer", "expires_in": 3600}),
            (200, {"access_token": "long_user", "token_type": "bearer", "expires_in": 5184000}),
            (200, {"data": [{"id": "DIFFERENT_PAGE", "access_token": "other"}]}),
        ])
        fb = FacebookPagePlatform("META_APP_ID", "META_APP_SECRET",
                                  "https://social.mayankgupta.in/oauth/callback",
                                  "1042685982267843", client)
        with pytest.raises(RuntimeError, match="not found in /me/accounts"):
            fb.exchange_code("fb_code")

    def test_exchange_code_step1_error_raises(self) -> None:
        client = _fake_client([(400, {"error": {"message": "bad code"}})])
        fb = FacebookPagePlatform("META_APP_ID", "META_APP_SECRET",
                                  "https://social.mayankgupta.in/oauth/callback",
                                  "1042685982267843", client)
        with pytest.raises(RuntimeError, match="facebook short-lived exchange failed"):
            fb.exchange_code("bad")

    def test_refresh_returns_none(self) -> None:
        now = int(time.time())
        tok = Token(
            platform="facebook_page", account_id="fb_page",
            token="PAGE_TOK", token_type="page_token",
            issued_at=now, expires_at=None,
            last_refreshed=None, scopes="",
        )
        result = self._FB.refresh(tok)
        assert result is None


# ---------------------------------------------------------------------------
# make_platform_registry
# ---------------------------------------------------------------------------

def test_make_platform_registry_returns_all_three() -> None:
    reg = make_platform_registry(
        ig_app_id="IG", ig_app_secret="IG_S",
        threads_app_id="TH", threads_app_secret="TH_S",
        meta_app_id="META", meta_app_secret="META_S",
        fb_page_id="PAGE_ID",
        redirect_uri="https://example.com/callback",
    )
    assert set(reg.keys()) == {"instagram", "threads", "facebook_page"}
    for name, adapter in reg.items():
        assert adapter.name == name
