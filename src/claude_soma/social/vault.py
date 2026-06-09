"""SQLite token vault for FI-SOCIAL-SERVICE.

Path:    HERMES_SOCIAL_DB  (default /var/lib/claude-soma/social.sqlite)
Mode:    WAL, created 0600
Schema:  tokens(platform, account_id, ...) PRIMARY KEY(platform, account_id)

All public functions accept an explicit ``db_path`` so tests can pass a
tmp_path without touching any module-level state.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import time
from pathlib import Path
from typing import cast


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """\
CREATE TABLE IF NOT EXISTS tokens (
    platform        TEXT    NOT NULL,
    account_id      TEXT    NOT NULL,
    token           TEXT    NOT NULL,
    token_type      TEXT    NOT NULL DEFAULT 'bearer',
    issued_at       INTEGER NOT NULL,
    expires_at      INTEGER,
    last_refreshed  INTEGER,
    scopes          TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (platform, account_id)
);
"""


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def _connect(db_path: str) -> sqlite3.Connection:
    """Open (and create if needed) the vault database.

    * Creates parent directories and the file with mode 0600 if absent.
    * Enables WAL mode and foreign-key enforcement.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Create the file with 0600 before sqlite3 touches it so no other process
    # can read the token between file creation and the chmod.
    if not path.exists():
        path.touch(mode=stat.S_IRUSR | stat.S_IWUSR)

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(_DDL)
    conn.commit()

    # Ensure permissions even if the file pre-existed with wrong mode.
    os.chmod(db_path, stat.S_IRUSR | stat.S_IWUSR)
    return conn


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def upsert_token(
    db_path: str,
    platform: str,
    account_id: str,
    token: str,
    token_type: str,
    issued_at: int,
    expires_at: int | None,
    scopes: str,
    last_refreshed: int | None = None,
) -> None:
    """Insert or replace a token row (full replace on conflict)."""
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO tokens
                (platform, account_id, token, token_type,
                 issued_at, expires_at, last_refreshed, scopes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, account_id) DO UPDATE SET
                token          = excluded.token,
                token_type     = excluded.token_type,
                issued_at      = excluded.issued_at,
                expires_at     = excluded.expires_at,
                last_refreshed = excluded.last_refreshed,
                scopes         = excluded.scopes
            """,
            (platform, account_id, token, token_type,
             issued_at, expires_at, last_refreshed, scopes),
        )
        conn.commit()
    finally:
        conn.close()


def get_token(
    db_path: str,
    platform: str,
    account_id: str | None = None,
) -> sqlite3.Row | None:
    """Return a single token row.

    If account_id is None, return the first row for the platform (useful for
    single-account deployments where there is exactly one account per platform).
    """
    conn = _connect(db_path)
    try:
        if account_id is not None:
            row = conn.execute(
                "SELECT * FROM tokens WHERE platform=? AND account_id=?",
                (platform, account_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM tokens WHERE platform=? LIMIT 1",
                (platform,),
            ).fetchone()
        return cast(sqlite3.Row | None, row)
    finally:
        conn.close()


def list_tokens(db_path: str) -> list[sqlite3.Row]:
    """Return all token rows (no raw token values used by callers — only metadata)."""
    conn = _connect(db_path)
    try:
        rows = conn.execute("SELECT * FROM tokens ORDER BY platform").fetchall()
        return cast(list[sqlite3.Row], rows)
    finally:
        conn.close()


def tokens_needing_refresh(db_path: str, within_days: int = 7) -> list[sqlite3.Row]:
    """Return rows where refresh is required.

    Criteria:
      * expires_at IS NOT NULL  (FB page tokens are non-expiring — skip them)
      * expires_at - now < within_days * 86400  (within the refresh window)
      * now - issued_at >= 86400  (IG/Threads require the token to be ≥24h old)
    """
    now = int(time.time())
    window_secs = within_days * 86400
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT * FROM tokens
            WHERE expires_at IS NOT NULL
              AND (expires_at - ?) < ?
              AND (? - issued_at) >= 86400
            """,
            (now, window_secs, now),
        ).fetchall()
        return cast(list[sqlite3.Row], rows)
    finally:
        conn.close()


def bootstrap_vault_from_config(
    db_path: str,
    *,
    ig_token: str,
    ig_user_id: str,
    ig_scopes: str,
    threads_token: str,
    threads_user_id: str,
    threads_scopes: str,
    fb_page_token: str,
    fb_page_id: str,
    fb_scopes: str,
    issued_at: int | None = None,
) -> dict[str, bool]:
    """Seed the vault from pre-provisioned long-lived tokens.

    Idempotent: only inserts when the platform row is absent.
    Returns a dict mapping platform → True if inserted, False if skipped.

    App ID/Secret are NOT written here; they stay in SocialConfig only.
    """
    now = issued_at if issued_at is not None else int(time.time())
    sixty_days = 5_184_000  # seconds in 60 days
    result: dict[str, bool] = {}

    conn = _connect(db_path)
    try:
        for platform, account_id, token, expires_at, scopes in [
            (
                "instagram",
                ig_user_id,
                ig_token,
                now + sixty_days,
                ig_scopes,
            ),
            (
                "threads",
                threads_user_id,
                threads_token,
                now + sixty_days,
                threads_scopes,
            ),
            (
                "facebook_page",
                fb_page_id,
                fb_page_token,
                None,  # non-expiring
                fb_scopes,
            ),
        ]:
            if not token or not account_id:
                result[platform] = False
                continue

            existing = conn.execute(
                "SELECT 1 FROM tokens WHERE platform=? AND account_id=?",
                (platform, account_id),
            ).fetchone()

            if existing:
                result[platform] = False
            else:
                conn.execute(
                    """
                    INSERT INTO tokens
                        (platform, account_id, token, token_type,
                         issued_at, expires_at, last_refreshed, scopes)
                    VALUES (?, ?, ?, 'bearer', ?, ?, NULL, ?)
                    """,
                    (platform, account_id, token, now, expires_at, scopes),
                )
                result[platform] = True

        conn.commit()
    finally:
        conn.close()

    return result
