"""Tests for vault.py — SQLite token vault CRUD + refresh window logic."""

from __future__ import annotations

import stat
import time
from pathlib import Path

from claude_soma.social.vault import (
    bootstrap_vault_from_config,
    get_token,
    list_tokens,
    tokens_needing_refresh,
    upsert_token,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db(tmp_path: Path) -> str:
    return str(tmp_path / "test.sqlite")


def _insert_token(
    db: str,
    platform: str = "instagram",
    account_id: str = "user123",
    token: str = "tok_abc",
    issued_at: int | None = None,
    expires_at: int | None = None,
) -> None:
    now = issued_at if issued_at is not None else int(time.time()) - 100_000
    upsert_token(
        db,
        platform=platform,
        account_id=account_id,
        token=token,
        token_type="bearer",
        issued_at=now,
        expires_at=expires_at,
        scopes="instagram_basic",
    )


# ---------------------------------------------------------------------------
# Schema / permissions
# ---------------------------------------------------------------------------

def test_db_created_with_0600(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _insert_token(db)
    mode = stat.S_IMODE(Path(db).stat().st_mode)
    assert mode == 0o600


def test_db_is_idempotent_on_second_connect(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _insert_token(db)
    _insert_token(db, token="tok_xyz")  # should overwrite, not fail
    row = get_token(db, "instagram", "user123")
    assert row is not None
    assert row["token"] == "tok_xyz"


# ---------------------------------------------------------------------------
# upsert_token / get_token
# ---------------------------------------------------------------------------

def test_upsert_and_get_token(tmp_path: Path) -> None:
    db = _db(tmp_path)
    now = int(time.time())
    upsert_token(
        db,
        platform="threads",
        account_id="t_user",
        token="threads_tok",
        token_type="bearer",
        issued_at=now,
        expires_at=now + 5_184_000,
        scopes="threads_basic",
    )
    row = get_token(db, "threads", "t_user")
    assert row is not None
    assert row["token"] == "threads_tok"
    assert row["platform"] == "threads"
    assert row["account_id"] == "t_user"
    assert row["expires_at"] == now + 5_184_000


def test_get_token_without_account_id_returns_first(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _insert_token(db, account_id="only_account")
    row = get_token(db, "instagram")
    assert row is not None
    assert row["account_id"] == "only_account"


def test_get_token_missing_returns_none(tmp_path: Path) -> None:
    db = _db(tmp_path)
    row = get_token(db, "instagram", "nonexistent")
    assert row is None


def test_upsert_overwrites_existing(tmp_path: Path) -> None:
    db = _db(tmp_path)
    now = int(time.time())
    upsert_token(
        db, platform="instagram", account_id="u1",
        token="old", token_type="bearer", issued_at=now,
        expires_at=now + 1000, scopes="scope_a",
    )
    upsert_token(
        db, platform="instagram", account_id="u1",
        token="new", token_type="bearer", issued_at=now + 1,
        expires_at=now + 2000, scopes="scope_b",
    )
    row = get_token(db, "instagram", "u1")
    assert row is not None
    assert row["token"] == "new"
    assert row["scopes"] == "scope_b"


# ---------------------------------------------------------------------------
# list_tokens
# ---------------------------------------------------------------------------

def test_list_tokens_empty(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert list_tokens(db) == []


def test_list_tokens_multiple_platforms(tmp_path: Path) -> None:
    db = _db(tmp_path)
    now = int(time.time())
    for plat, acc in [("instagram", "ig_user"), ("threads", "th_user"), ("facebook_page", "fb_page")]:
        upsert_token(
            db, platform=plat, account_id=acc,
            token="tok", token_type="bearer",
            issued_at=now, expires_at=None, scopes="",
        )
    rows = list_tokens(db)
    assert len(rows) == 3
    platforms = {r["platform"] for r in rows}
    assert platforms == {"instagram", "threads", "facebook_page"}


# ---------------------------------------------------------------------------
# tokens_needing_refresh
# ---------------------------------------------------------------------------

def test_tokens_needing_refresh_includes_near_expiry(tmp_path: Path) -> None:
    db = _db(tmp_path)
    now = int(time.time())
    # issued 2 days ago, expires in 3 days → within 7-day window
    upsert_token(
        db, platform="instagram", account_id="ig",
        token="tok", token_type="bearer",
        issued_at=now - 2 * 86400,
        expires_at=now + 3 * 86400,
        scopes="",
    )
    rows = tokens_needing_refresh(db, within_days=7)
    assert len(rows) == 1
    assert rows[0]["platform"] == "instagram"


def test_tokens_needing_refresh_excludes_far_expiry(tmp_path: Path) -> None:
    db = _db(tmp_path)
    now = int(time.time())
    # expires in 30 days → NOT within 7-day window
    upsert_token(
        db, platform="instagram", account_id="ig",
        token="tok", token_type="bearer",
        issued_at=now - 2 * 86400,
        expires_at=now + 30 * 86400,
        scopes="",
    )
    rows = tokens_needing_refresh(db, within_days=7)
    assert rows == []


def test_tokens_needing_refresh_excludes_null_expires(tmp_path: Path) -> None:
    db = _db(tmp_path)
    now = int(time.time())
    # facebook_page — expires_at IS NULL → never needs refresh
    upsert_token(
        db, platform="facebook_page", account_id="fb",
        token="tok", token_type="page_token",
        issued_at=now - 2 * 86400,
        expires_at=None,
        scopes="",
    )
    rows = tokens_needing_refresh(db, within_days=7)
    assert rows == []


def test_tokens_needing_refresh_excludes_too_young(tmp_path: Path) -> None:
    db = _db(tmp_path)
    now = int(time.time())
    # issued 30 minutes ago (< 24h) — not yet eligible
    upsert_token(
        db, platform="instagram", account_id="ig",
        token="tok", token_type="bearer",
        issued_at=now - 1800,
        expires_at=now + 3 * 86400,
        scopes="",
    )
    rows = tokens_needing_refresh(db, within_days=7)
    assert rows == []


def test_tokens_needing_refresh_selects_multiple(tmp_path: Path) -> None:
    db = _db(tmp_path)
    now = int(time.time())
    # Both IG and Threads near expiry
    for plat, acc in [("instagram", "ig"), ("threads", "th")]:
        upsert_token(
            db, platform=plat, account_id=acc,
            token="tok", token_type="bearer",
            issued_at=now - 2 * 86400,
            expires_at=now + 2 * 86400,
            scopes="",
        )
    rows = tokens_needing_refresh(db, within_days=7)
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# bootstrap_vault_from_config
# ---------------------------------------------------------------------------

def test_bootstrap_inserts_missing_tokens(tmp_path: Path) -> None:
    db = _db(tmp_path)
    now = int(time.time())
    result = bootstrap_vault_from_config(
        db,
        ig_token="ig_tok",
        ig_user_id="ig_user_1",
        ig_scopes="instagram",
        threads_token="th_tok",
        threads_user_id="th_user_1",
        threads_scopes="threads",
        fb_page_token="fb_tok",
        fb_page_id="fb_page_1",
        fb_scopes="pages",
        issued_at=now,
    )
    assert result["instagram"] is True
    assert result["threads"] is True
    assert result["facebook_page"] is True

    row_ig = get_token(db, "instagram", "ig_user_1")
    assert row_ig is not None
    assert row_ig["token"] == "ig_tok"
    assert row_ig["expires_at"] == now + 5_184_000

    row_fb = get_token(db, "facebook_page", "fb_page_1")
    assert row_fb is not None
    assert row_fb["expires_at"] is None  # non-expiring


def test_bootstrap_is_idempotent(tmp_path: Path) -> None:
    db = _db(tmp_path)
    now = int(time.time())
    kwargs: dict[str, object] = dict(
        ig_token="ig_tok_orig", ig_user_id="ig_u", ig_scopes="",
        threads_token="th_tok_orig", threads_user_id="th_u", threads_scopes="",
        fb_page_token="fb_tok_orig", fb_page_id="fb_p", fb_scopes="",
        issued_at=now,
    )
    bootstrap_vault_from_config(db, **kwargs)  # type: ignore[arg-type]

    # Second call with different token values — must not overwrite.
    kwargs2: dict[str, object] = dict(
        ig_token="ig_tok_NEW", ig_user_id="ig_u", ig_scopes="",
        threads_token="th_tok_NEW", threads_user_id="th_u", threads_scopes="",
        fb_page_token="fb_tok_NEW", fb_page_id="fb_p", fb_scopes="",
        issued_at=now,
    )
    result2 = bootstrap_vault_from_config(db, **kwargs2)  # type: ignore[arg-type]
    assert result2["instagram"] is False
    assert result2["threads"] is False
    assert result2["facebook_page"] is False

    # Original tokens are preserved.
    row = get_token(db, "instagram", "ig_u")
    assert row is not None
    assert row["token"] == "ig_tok_orig"


def test_bootstrap_skips_empty_tokens(tmp_path: Path) -> None:
    db = _db(tmp_path)
    result = bootstrap_vault_from_config(
        db,
        ig_token="",         # empty → skip
        ig_user_id="ig_u",
        ig_scopes="",
        threads_token="th_tok",
        threads_user_id="th_u",
        threads_scopes="",
        fb_page_token="fb_tok",
        fb_page_id="fb_p",
        fb_scopes="",
    )
    assert result["instagram"] is False
    assert result["threads"] is True
    # IG row should not exist.
    assert get_token(db, "instagram", "ig_u") is None
