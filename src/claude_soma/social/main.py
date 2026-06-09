"""FastAPI application for FI-SOCIAL-SERVICE Phase 1.

Routes:
  GET  /oauth/start?platform={instagram|threads|facebook_page}
       Redirects the user to the platform authorization page.
  GET  /oauth/callback?code=&state=
       Receives the OAuth code, validates state (CSRF), runs server-side
       token exchange, upserts the vault, returns a confirmation page.
  GET  /health
       Liveness + per-platform token presence / expiry (no raw tokens).
  GET  /api/tokens/status
       Per-platform metadata: issued_at, expires_at, days_to_expiry,
       last_refreshed.  No raw token values.

Bind: 127.0.0.1:{HERMES_SOCIAL_PORT} (default 9200).

Internal/admin notes:
  /api/tokens/status is only reachable on localhost — Caddy does NOT proxy
  it.  The public Caddy block for social.mayankgupta.in exposes only
  /oauth/*, /health, and /api/webhooks/* (Phase 3).
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .config import SocialConfig
from .oauth import StateStore
from .platforms import Platform, make_platform_registry
from .vault import bootstrap_vault_from_config, get_token, list_tokens, upsert_token

_log = logging.getLogger("claude_soma.social.main")

_UNPROVISIONED_DETAIL = (
    "social service not provisioned — populate meta-tokens.env and restart "
    "(see docs/social-provisioning.md)"
)


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Load config, bootstrap vault, create adapters on startup.

    Tolerates an unprovisioned box: if config is missing/incomplete, the
    service still boots cleanly (cfg=None, no adapters) so the systemd unit
    can be enabled before the operator finishes provisioning.
    """
    app.state.state_store = StateStore()

    try:
        cfg = SocialConfig.from_env()
    except ValueError as exc:
        app.state.cfg = None
        app.state.adapters = {}
        _log.warning(
            "social service starting UNPROVISIONED — populate meta-tokens.env then "
            "restart; see docs/social-provisioning.md (reason: %s)",
            exc,
        )
        yield  # app running, unprovisioned
        return

    app.state.cfg = cfg

    adapters = make_platform_registry(
        ig_app_id=cfg.ig_app_id,
        ig_app_secret=cfg.ig_app_secret,
        threads_app_id=cfg.threads_app_id,
        threads_app_secret=cfg.threads_app_secret,
        meta_app_id=cfg.meta_app_id,
        meta_app_secret=cfg.meta_app_secret,
        fb_page_id=cfg.fb_page_id,
        redirect_uri=cfg.redirect_uri,
    )
    app.state.adapters = adapters

    bootstrap_vault_from_config(
        cfg.db_path,
        ig_token=cfg.ig_long_lived_token,
        ig_user_id=cfg.ig_user_id,
        ig_scopes=adapters["instagram"].name,
        threads_token=cfg.threads_long_lived_token,
        threads_user_id=cfg.threads_user_id,
        threads_scopes=adapters["threads"].name,
        fb_page_token=cfg.fb_page_access_token,
        fb_page_id=cfg.fb_page_id,
        fb_scopes=adapters["facebook_page"].name,
    )

    yield  # app running


app = FastAPI(title="claude-soma-social", lifespan=_lifespan)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SUCCESS_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Authorized</title></head>
<body>
<h2>Authorization successful.</h2>
<p>Platform: {platform}</p>
<p>You may close this window.</p>
</body>
</html>
"""


def _days_to_expiry(expires_at: int | None) -> float | None:
    if expires_at is None:
        return None
    return round((expires_at - time.time()) / 86400, 2)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/oauth/start")
def oauth_start(platform: str, request: Request) -> RedirectResponse:
    """Redirect the user to the platform OAuth authorization page."""
    if request.app.state.cfg is None or not request.app.state.adapters:
        raise HTTPException(status_code=503, detail=_UNPROVISIONED_DETAIL)

    adapters: dict[str, Platform] = request.app.state.adapters
    if platform not in adapters:
        raise HTTPException(status_code=400, detail=f"Unknown platform: {platform!r}")

    state_store: StateStore = request.app.state.state_store
    state = state_store.generate(platform)
    url = adapters[platform].authorize_url(state)
    return RedirectResponse(url=url, status_code=302)


@app.get("/oauth/callback")
def oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_reason: str | None = None,
) -> HTMLResponse:
    """Receive the OAuth callback, exchange the code, upsert the vault."""
    if request.app.state.cfg is None or not request.app.state.adapters:
        raise HTTPException(status_code=503, detail=_UNPROVISIONED_DETAIL)

    if error:
        raise HTTPException(
            status_code=400,
            detail=f"OAuth error from provider: {error} ({error_reason})",
        )

    if not code or not state:
        raise HTTPException(
            status_code=400, detail="Missing 'code' or 'state' query parameter."
        )

    state_store: StateStore = request.app.state.state_store
    try:
        platform = state_store.validate_and_consume(state)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    adapters: dict[str, Platform] = request.app.state.adapters
    if platform not in adapters:
        raise HTTPException(status_code=400, detail=f"Unknown platform: {platform!r}")

    cfg: SocialConfig = request.app.state.cfg

    try:
        token = adapters[platform].exchange_code(code)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    upsert_token(
        cfg.db_path,
        platform=token.platform,
        account_id=token.account_id,
        token=token.token,
        token_type=token.token_type,
        issued_at=token.issued_at,
        expires_at=token.expires_at,
        scopes=token.scopes,
        last_refreshed=token.last_refreshed,
    )

    html = _SUCCESS_HTML.format(platform=platform)
    return HTMLResponse(content=html, status_code=200)


@app.get("/health")
def health(request: Request) -> JSONResponse:
    """Liveness + per-platform token presence and expiry (no raw tokens)."""
    cfg: SocialConfig | None = request.app.state.cfg
    if cfg is None:
        return JSONResponse({"status": "ok", "provisioned": False})

    platforms_info: list[dict[str, Any]] = []

    for platform in ("instagram", "threads", "facebook_page"):
        row = get_token(cfg.db_path, platform)
        if row is None:
            platforms_info.append({
                "platform": platform,
                "token_present": False,
                "expires_at": None,
                "days_to_expiry": None,
                "last_refreshed": None,
            })
        else:
            exp: int | None = row["expires_at"]
            platforms_info.append({
                "platform": platform,
                "token_present": True,
                "expires_at": exp,
                "days_to_expiry": _days_to_expiry(exp),
                "last_refreshed": row["last_refreshed"],
            })

    return JSONResponse({"status": "ok", "provisioned": True, "platforms": platforms_info})


@app.get("/api/tokens/status")
def tokens_status(request: Request) -> JSONResponse:
    """Per-platform token metadata.  Internal / localhost only.  No raw tokens."""
    cfg: SocialConfig | None = request.app.state.cfg
    if cfg is None or not request.app.state.adapters:
        raise HTTPException(status_code=503, detail=_UNPROVISIONED_DETAIL)

    rows = list_tokens(cfg.db_path)
    result: list[dict[str, Any]] = []
    for row in rows:
        exp: int | None = row["expires_at"]
        result.append({
            "platform": row["platform"],
            "account_id": row["account_id"],
            "token_type": row["token_type"],
            "issued_at": row["issued_at"],
            "expires_at": exp,
            "days_to_expiry": _days_to_expiry(exp),
            "last_refreshed": row["last_refreshed"],
            "scopes": row["scopes"],
        })
    return JSONResponse(result)
