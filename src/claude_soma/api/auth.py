from __future__ import annotations

import os

from fastapi import Header, HTTPException


ALLOWED_GITHUB_HANDLES = {h.strip() for h in os.environ.get(
    "HERMES_ALLOWED_GITHUB_HANDLES", "techfreakworm"
).split(",") if h.strip()}


def require_authed_user(x_github_handle: str = Header(default="")) -> str:
    """V1: trust the Next.js auth layer to set X-GitHub-Handle.
    The frontend's middleware verifies the GitHub OAuth session and
    forwards this header. Bypass attempts get 403."""
    if x_github_handle not in ALLOWED_GITHUB_HANDLES:
        raise HTTPException(status_code=403, detail="not authorized")
    return x_github_handle
