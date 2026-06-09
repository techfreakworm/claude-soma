"""Tests for refresh.py — systemd-timer refresh entrypoint."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from claude_soma.social.platforms import Platform, Token
from claude_soma.social.vault import get_token, upsert_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db(tmp_path: Path) -> str:
    return str(tmp_path / "refresh_test.sqlite")


def _make_config(db_path: str) -> Any:
    """Build a minimal SocialConfig-like object for tests."""
    import os
    os.environ["META_APP_ID"] = "META_ID"
    os.environ["META_APP_SECRET"] = "META_SEC"
    os.environ["IG_APP_ID"] = "IG_ID"
    os.environ["IG_APP_SECRET"] = "IG_SEC"
    os.environ["THREADS_APP_ID"] = "TH_ID"
    os.environ["THREADS_APP_SECRET"] = "TH_SEC"
    os.environ["WEBHOOK_VERIFY_TOKEN"] = "wvt"
    os.environ["HERMES_SOCIAL_DB"] = db_path
    os.environ["HERMES_SOCIAL_REFRESH_WINDOW_DAYS"] = "7"

    from claude_soma.social.config import SocialConfig
    return SocialConfig.from_env()


def _insert_near_expiry_token(
    db: str,
    platform: str,
    account_id: str,
    days_until_expiry: int = 3,
) -> Token:
    now = int(time.time())
    issued = now - 2 * 86400  # 2 days old
    expires = now + days_until_expiry * 86400
    upsert_token(
        db,
        platform=platform,
        account_id=account_id,
        token=f"{platform}_old_token",
        token_type="bearer",
        issued_at=issued,
        expires_at=expires,
        scopes="test_scope",
    )
    return Token(
        platform=platform, account_id=account_id,
        token=f"{platform}_old_token", token_type="bearer",
        issued_at=issued, expires_at=expires,
        last_refreshed=None, scopes="test_scope",
    )


def _make_mock_adapter(name: str, new_token: Token | None = None) -> MagicMock:
    m = MagicMock(spec=Platform)
    m.name = name
    m.refresh.return_value = new_token
    return m


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRefreshRun:
    def test_refreshes_near_expiry_token(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        cfg = _make_config(db)
        now = int(time.time())

        _insert_near_expiry_token(db, "instagram", "ig_user")
        new_tok = Token(
            platform="instagram", account_id="ig_user",
            token="ig_refreshed_token", token_type="bearer",
            issued_at=now, expires_at=now + 5_184_000,
            last_refreshed=now, scopes="test_scope",
        )

        mock_ig = _make_mock_adapter("instagram", new_tok)
        mock_th = _make_mock_adapter("threads", None)
        mock_fb = _make_mock_adapter("facebook_page", None)
        adapters: dict[str, Any] = {
            "instagram": mock_ig, "threads": mock_th, "facebook_page": mock_fb
        }

        from claude_soma.social.refresh import run
        result = run(cfg, adapters=adapters)

        assert result is True
        mock_ig.refresh.assert_called_once()
        row = get_token(db, "instagram", "ig_user")
        assert row is not None
        assert row["token"] == "ig_refreshed_token"

    def test_skips_far_expiry_token(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        cfg = _make_config(db)

        # Insert token expiring in 30 days — outside 7-day window.
        now = int(time.time())
        upsert_token(
            db,
            platform="instagram", account_id="ig_user",
            token="ig_old", token_type="bearer",
            issued_at=now - 2 * 86400,
            expires_at=now + 30 * 86400,
            scopes="",
        )

        mock_ig = _make_mock_adapter("instagram")
        adapters: dict[str, Any] = {"instagram": mock_ig, "threads": MagicMock(),
                                     "facebook_page": MagicMock()}

        from claude_soma.social.refresh import run
        result = run(cfg, adapters=adapters)

        assert result is True
        mock_ig.refresh.assert_not_called()

    def test_skips_facebook_page_null_expires(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        cfg = _make_config(db)
        now = int(time.time())

        # FB page token — expires_at NULL.
        upsert_token(
            db,
            platform="facebook_page", account_id="fb_page",
            token="fb_tok", token_type="page_token",
            issued_at=now - 2 * 86400,
            expires_at=None,
            scopes="",
        )

        mock_fb = _make_mock_adapter("facebook_page")
        adapters: dict[str, Any] = {"instagram": MagicMock(), "threads": MagicMock(),
                                     "facebook_page": mock_fb}

        from claude_soma.social.refresh import run
        result = run(cfg, adapters=adapters)

        assert result is True
        mock_fb.refresh.assert_not_called()

    def test_returns_false_on_refresh_failure(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        cfg = _make_config(db)

        _insert_near_expiry_token(db, "instagram", "ig_user")

        mock_ig = _make_mock_adapter("instagram")
        mock_ig.refresh.side_effect = RuntimeError("API rate limited")
        adapters: dict[str, Any] = {"instagram": mock_ig, "threads": MagicMock(),
                                     "facebook_page": MagicMock()}

        from claude_soma.social.refresh import run
        result = run(cfg, adapters=adapters)

        assert result is False

    def test_continues_after_single_failure(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        cfg = _make_config(db)
        now = int(time.time())

        _insert_near_expiry_token(db, "instagram", "ig_user")
        _insert_near_expiry_token(db, "threads", "th_user")

        new_th = Token(
            platform="threads", account_id="th_user",
            token="th_refreshed", token_type="bearer",
            issued_at=now, expires_at=now + 5_184_000,
            last_refreshed=now, scopes="",
        )
        mock_ig = _make_mock_adapter("instagram")
        mock_ig.refresh.side_effect = RuntimeError("IG failed")
        mock_th = _make_mock_adapter("threads", new_th)
        adapters: dict[str, Any] = {"instagram": mock_ig, "threads": mock_th,
                                     "facebook_page": MagicMock()}

        from claude_soma.social.refresh import run
        result = run(cfg, adapters=adapters)

        assert result is False  # one failure → False
        # Threads was still refreshed.
        row = get_token(db, "threads", "th_user")
        assert row is not None
        assert row["token"] == "th_refreshed"

    def test_empty_vault_returns_true(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        cfg = _make_config(db)
        adapters: dict[str, Any] = {}

        from claude_soma.social.refresh import run
        result = run(cfg, adapters=adapters)

        assert result is True

    def test_refresh_none_result_is_skipped(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        cfg = _make_config(db)

        _insert_near_expiry_token(db, "instagram", "ig_user")

        # Adapter returns None (shouldn't happen for IG, but test the guard).
        mock_ig = _make_mock_adapter("instagram", None)
        adapters: dict[str, Any] = {"instagram": mock_ig, "threads": MagicMock(),
                                     "facebook_page": MagicMock()}

        from claude_soma.social.refresh import run
        result = run(cfg, adapters=adapters)

        assert result is True
        # Token unchanged.
        row = get_token(db, "instagram", "ig_user")
        assert row is not None
        assert row["token"] == "instagram_old_token"
