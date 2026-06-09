"""Per-platform OAuth adapter implementations for FI-SOCIAL-SERVICE (Phase 1).

Provides a ``Platform`` Protocol with three implementations:
  - InstagramPlatform   (instagram.com authorize, api.instagram.com + graph.instagram.com exchange)
  - ThreadsPlatform     (threads.net authorize, graph.threads.net exchange)
  - FacebookPagePlatform (graph.facebook.com v25.0; non-expiring page token; refresh is no-op)

All HTTP calls use httpx with timeouts.  A custom ``client`` can be injected
for testing (pass ``httpx.Client(transport=FakeTransport(...))``).

Phase 2/3 methods (publish, comment, insights, dm_reply, business_discovery)
are stubbed to raise ``NotImplementedError``.
"""

from __future__ import annotations

import time
import urllib.parse
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx


_TIMEOUT = 30.0  # seconds for all Graph API calls

# IG/Threads 60-day window in seconds.
_TOKEN_TTL = 5_184_000

# Minimum token age before refresh is allowed (24 h).
_MIN_AGE_FOR_REFRESH = 86_400


# ---------------------------------------------------------------------------
# Shared data types
# ---------------------------------------------------------------------------

@dataclass
class Token:
    platform: str
    account_id: str
    token: str
    token_type: str
    issued_at: int
    expires_at: int | None
    last_refreshed: int | None
    scopes: str


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class Platform(Protocol):
    """Minimal contract for Phase-1 OAuth + refresh operations."""

    name: str

    def authorize_url(self, state: str) -> str:
        """Return the authorization URL to redirect the user to."""
        ...

    def exchange_code(self, code: str) -> Token:
        """Exchange an OAuth authorization code for a long-lived token."""
        ...

    def refresh(self, token: Token) -> Token | None:
        """Refresh a long-lived token.  Returns None for non-expiring tokens.

        Raises RuntimeError if the platform API returns an error.
        """
        ...


# ---------------------------------------------------------------------------
# Helper: build httpx client
# ---------------------------------------------------------------------------

def _make_client(injected: httpx.Client | None) -> httpx.Client:
    if injected is not None:
        return injected
    return httpx.Client(timeout=_TIMEOUT)


# ---------------------------------------------------------------------------
# Instagram (Instagram-Login variant)
# ---------------------------------------------------------------------------

class InstagramPlatform:
    """Instagram OAuth adapter using the Instagram-Login product."""

    name = "instagram"

    _AUTHORIZE_HOST = "https://www.instagram.com"
    _SHORT_LIVED_HOST = "https://api.instagram.com"
    _GRAPH_HOST = "https://graph.instagram.com"

    _SCOPES = ",".join([
        "instagram_business_basic",
        "instagram_business_content_publish",
        "instagram_business_manage_comments",
        "instagram_business_manage_messages",
        "instagram_manage_insights",
    ])

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        redirect_uri: str,
        client: httpx.Client | None = None,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._redirect_uri = redirect_uri
        self._client = client

    def authorize_url(self, state: str) -> str:
        params = urllib.parse.urlencode({
            "client_id": self._app_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": self._SCOPES,
            "state": state,
        })
        return f"{self._AUTHORIZE_HOST}/oauth/authorize?{params}"

    def exchange_code(self, code: str) -> Token:
        """code → short-lived (api.instagram.com) → long-lived (graph.instagram.com)."""
        client = _make_client(self._client)
        now = int(time.time())

        # Step 1: code → short-lived token.
        try:
            r1 = client.post(
                f"{self._SHORT_LIVED_HOST}/oauth/access_token",
                data={
                    "client_id": self._app_id,
                    "client_secret": self._app_secret,
                    "grant_type": "authorization_code",
                    "redirect_uri": self._redirect_uri,
                    "code": code,
                },
            )
            r1.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"instagram short-lived exchange failed ({exc.response.status_code}): "
                f"{exc.response.text[-500:]}"
            ) from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"instagram short-lived exchange request error: {exc}") from exc

        short_data = r1.json()
        short_token: str = short_data["access_token"]
        user_id: str = str(short_data["user_id"])

        # Step 2: short-lived → long-lived (60d).
        try:
            r2 = client.get(
                f"{self._GRAPH_HOST}/access_token",
                params={
                    "grant_type": "ig_exchange_token",
                    "client_secret": self._app_secret,
                    "access_token": short_token,
                },
            )
            r2.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"instagram long-lived exchange failed ({exc.response.status_code}): "
                f"{exc.response.text[-500:]}"
            ) from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"instagram long-lived exchange request error: {exc}") from exc

        long_data = r2.json()
        long_token: str = long_data["access_token"]
        expires_in: int = int(long_data.get("expires_in", _TOKEN_TTL))

        return Token(
            platform=self.name,
            account_id=user_id,
            token=long_token,
            token_type="bearer",
            issued_at=now,
            expires_at=now + expires_in,
            last_refreshed=None,
            scopes=self._SCOPES,
        )

    def refresh(self, token: Token) -> Token | None:
        """Refresh a long-lived IG token.  Token must be ≥24 h old."""
        now = int(time.time())
        age = now - token.issued_at
        if age < _MIN_AGE_FOR_REFRESH:
            raise RuntimeError(
                f"instagram token is only {age}s old; must be ≥{_MIN_AGE_FOR_REFRESH}s "
                f"(24 h) before refresh is permitted."
            )

        client = _make_client(self._client)
        try:
            r = client.get(
                f"{self._GRAPH_HOST}/refresh_access_token",
                params={
                    "grant_type": "ig_refresh_token",
                    "access_token": token.token,
                },
            )
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"instagram refresh failed ({exc.response.status_code}): "
                f"{exc.response.text[-500:]}"
            ) from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"instagram refresh request error: {exc}") from exc

        data = r.json()
        new_token: str = data["access_token"]
        expires_in = int(data.get("expires_in", _TOKEN_TTL))

        return Token(
            platform=self.name,
            account_id=token.account_id,
            token=new_token,
            token_type="bearer",
            issued_at=now,
            expires_at=now + expires_in,
            last_refreshed=now,
            scopes=token.scopes,
        )


# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------

class ThreadsPlatform:
    """Threads OAuth adapter."""

    name = "threads"

    _AUTHORIZE_HOST = "https://threads.net"
    _GRAPH_HOST = "https://graph.threads.net"

    _SCOPES = ",".join([
        "threads_basic",
        "threads_content_publish",
        "threads_manage_replies",
        "threads_manage_insights",
    ])

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        redirect_uri: str,
        client: httpx.Client | None = None,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._redirect_uri = redirect_uri
        self._client = client

    def authorize_url(self, state: str) -> str:
        params = urllib.parse.urlencode({
            "client_id": self._app_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": self._SCOPES,
            "state": state,
        })
        return f"{self._AUTHORIZE_HOST}/oauth/authorize?{params}"

    def exchange_code(self, code: str) -> Token:
        """code → short-lived → long-lived (both via graph.threads.net)."""
        client = _make_client(self._client)
        now = int(time.time())

        # Step 1: code → short-lived token.
        try:
            r1 = client.post(
                f"{self._GRAPH_HOST}/oauth/access_token",
                data={
                    "client_id": self._app_id,
                    "client_secret": self._app_secret,
                    "grant_type": "authorization_code",
                    "redirect_uri": self._redirect_uri,
                    "code": code,
                },
            )
            r1.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"threads short-lived exchange failed ({exc.response.status_code}): "
                f"{exc.response.text[-500:]}"
            ) from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"threads short-lived exchange request error: {exc}") from exc

        short_data = r1.json()
        short_token: str = short_data["access_token"]
        user_id: str = str(short_data["user_id"])

        # Step 2: short-lived → long-lived (60d).
        try:
            r2 = client.get(
                f"{self._GRAPH_HOST}/access_token",
                params={
                    "grant_type": "th_exchange_token",
                    "client_secret": self._app_secret,
                    "access_token": short_token,
                },
            )
            r2.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"threads long-lived exchange failed ({exc.response.status_code}): "
                f"{exc.response.text[-500:]}"
            ) from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"threads long-lived exchange request error: {exc}") from exc

        long_data = r2.json()
        long_token: str = long_data["access_token"]
        expires_in: int = int(long_data.get("expires_in", _TOKEN_TTL))

        return Token(
            platform=self.name,
            account_id=user_id,
            token=long_token,
            token_type="bearer",
            issued_at=now,
            expires_at=now + expires_in,
            last_refreshed=None,
            scopes=self._SCOPES,
        )

    def refresh(self, token: Token) -> Token | None:
        """Refresh a long-lived Threads token.  Token must be ≥24 h old."""
        now = int(time.time())
        age = now - token.issued_at
        if age < _MIN_AGE_FOR_REFRESH:
            raise RuntimeError(
                f"threads token is only {age}s old; must be ≥{_MIN_AGE_FOR_REFRESH}s "
                f"(24 h) before refresh is permitted."
            )

        client = _make_client(self._client)
        try:
            r = client.get(
                f"{self._GRAPH_HOST}/refresh_access_token",
                params={
                    "grant_type": "th_refresh_token",
                    "access_token": token.token,
                },
            )
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"threads refresh failed ({exc.response.status_code}): "
                f"{exc.response.text[-500:]}"
            ) from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"threads refresh request error: {exc}") from exc

        data = r.json()
        new_token: str = data["access_token"]
        expires_in = int(data.get("expires_in", _TOKEN_TTL))

        return Token(
            platform=self.name,
            account_id=token.account_id,
            token=new_token,
            token_type="bearer",
            issued_at=now,
            expires_at=now + expires_in,
            last_refreshed=now,
            scopes=token.scopes,
        )


# ---------------------------------------------------------------------------
# Facebook Page
# ---------------------------------------------------------------------------

class FacebookPagePlatform:
    """Facebook Page OAuth adapter.

    Token flow (3 steps):
      code → short-lived user token
           → long-lived user token (fb_exchange_token)
           → non-expiring Page token (/me/accounts)

    refresh() is a no-op (returns None) because Page tokens do not expire.
    """

    name = "facebook_page"

    _AUTHORIZE_HOST = "https://www.facebook.com"
    _GRAPH_HOST = "https://graph.facebook.com"
    _API_VERSION = "v25.0"

    _SCOPES = ",".join([
        "pages_show_list",
        "pages_read_engagement",
        "pages_manage_posts",
        "pages_manage_engagement",
        "pages_manage_metadata",
        "pages_read_user_content",
        "pages_messaging",
    ])

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        redirect_uri: str,
        page_id: str,
        client: httpx.Client | None = None,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._redirect_uri = redirect_uri
        self._page_id = page_id
        self._client = client

    def authorize_url(self, state: str) -> str:
        params = urllib.parse.urlencode({
            "client_id": self._app_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": self._SCOPES,
            "state": state,
        })
        return f"{self._AUTHORIZE_HOST}/dialog/oauth?{params}"

    def exchange_code(self, code: str) -> Token:
        """code → short-lived user token → long-lived user token → Page token."""
        client = _make_client(self._client)
        now = int(time.time())
        base = f"{self._GRAPH_HOST}/{self._API_VERSION}"

        # Step 1: code → short-lived user token.
        try:
            r1 = client.post(
                f"{base}/oauth/access_token",
                params={
                    "client_id": self._app_id,
                    "client_secret": self._app_secret,
                    "redirect_uri": self._redirect_uri,
                    "code": code,
                },
            )
            r1.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"facebook short-lived exchange failed ({exc.response.status_code}): "
                f"{exc.response.text[-500:]}"
            ) from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"facebook short-lived exchange request error: {exc}") from exc

        short_user_token: str = r1.json()["access_token"]

        # Step 2: short-lived user token → long-lived user token.
        try:
            r2 = client.get(
                f"{base}/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": self._app_id,
                    "client_secret": self._app_secret,
                    "fb_exchange_token": short_user_token,
                },
            )
            r2.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"facebook long-lived user token exchange failed "
                f"({exc.response.status_code}): {exc.response.text[-500:]}"
            ) from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"facebook long-lived exchange request error: {exc}") from exc

        long_user_token: str = r2.json()["access_token"]

        # Step 3: long-lived user token → non-expiring Page token.
        try:
            r3 = client.get(
                f"{base}/me/accounts",
                params={"access_token": long_user_token},
            )
            r3.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"facebook /me/accounts failed ({exc.response.status_code}): "
                f"{exc.response.text[-500:]}"
            ) from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"facebook /me/accounts request error: {exc}") from exc

        accounts_data: dict[str, object] = r3.json()
        pages: list[dict[str, object]] = accounts_data.get("data", [])  # type: ignore[assignment]

        page_token: str | None = None
        for page in pages:
            if str(page.get("id", "")) == self._page_id:
                page_token = str(page["access_token"])
                break

        if page_token is None:
            page_ids = [str(p.get("id", "")) for p in pages]
            raise RuntimeError(
                f"facebook page_id {self._page_id!r} not found in /me/accounts. "
                f"Available page ids: {page_ids}"
            )

        return Token(
            platform=self.name,
            account_id=self._page_id,
            token=page_token,
            token_type="page_token",
            issued_at=now,
            expires_at=None,  # non-expiring
            last_refreshed=None,
            scopes=self._SCOPES,
        )

    def refresh(self, token: Token) -> Token | None:
        """No-op: Facebook Page tokens are non-expiring."""
        return None


# ---------------------------------------------------------------------------
# Registry helper
# ---------------------------------------------------------------------------

def make_platform_registry(
    *,
    ig_app_id: str,
    ig_app_secret: str,
    threads_app_id: str,
    threads_app_secret: str,
    meta_app_id: str,
    meta_app_secret: str,
    fb_page_id: str,
    redirect_uri: str,
    client: httpx.Client | None = None,
) -> dict[str, Platform]:
    """Construct all three adapters keyed by platform name."""
    ig: Platform = InstagramPlatform(ig_app_id, ig_app_secret, redirect_uri, client)
    th: Platform = ThreadsPlatform(threads_app_id, threads_app_secret, redirect_uri, client)
    fb: Platform = FacebookPagePlatform(
        meta_app_id, meta_app_secret, redirect_uri, fb_page_id, client
    )
    return {"instagram": ig, "threads": th, "facebook_page": fb}
