"""Token-refresh entrypoint for FI-SOCIAL-SERVICE.

Invoked by the systemd timer:
    python -m claude_soma.social.refresh

For each vault token within HERMES_SOCIAL_REFRESH_WINDOW_DAYS (default 7)
of expiry, calls the platform's refresh() method and upserts the new token.

Exit codes:
    0  — all refreshes succeeded (or no tokens needed refreshing)
    1  — one or more refreshes failed

Non-expiring tokens (facebook_page, expires_at IS NULL) are automatically
excluded by tokens_needing_refresh().
"""

from __future__ import annotations

import logging
import sys
import time

from .config import SocialConfig
from .platforms import Platform, Token, make_platform_registry
from .vault import tokens_needing_refresh, upsert_token

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
_log = logging.getLogger("claude_soma.social.refresh")


def run(cfg: SocialConfig, adapters: dict[str, Platform] | None = None) -> bool:
    """Perform one refresh sweep.  Returns True if all succeeded, False if any failed.

    ``adapters`` is injectable for unit tests; if None, real adapters are created.
    """
    if adapters is None:
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

    rows = tokens_needing_refresh(cfg.db_path, within_days=cfg.refresh_window_days)
    if not rows:
        _log.info("No tokens need refreshing within %d-day window.", cfg.refresh_window_days)
        return True

    all_ok = True
    for row in rows:
        platform_name: str = row["platform"]
        account_id: str = row["account_id"]
        expires_at: int | None = row["expires_at"]
        days = round((expires_at - time.time()) / 86400, 1) if expires_at else None
        _log.info(
            "Refreshing %s / %s  (expires_at=%s, days_left=%s)",
            platform_name, account_id, expires_at, days,
        )

        adapter = adapters.get(platform_name)
        if adapter is None:
            _log.error("No adapter registered for platform %r — skipping.", platform_name)
            all_ok = False
            continue

        old_token = Token(
            platform=platform_name,
            account_id=account_id,
            token=row["token"],
            token_type=row["token_type"],
            issued_at=row["issued_at"],
            expires_at=expires_at,
            last_refreshed=row["last_refreshed"],
            scopes=row["scopes"],
        )

        try:
            new_token = adapter.refresh(old_token)
        except RuntimeError as exc:
            _log.error("Refresh failed for %s / %s: %s", platform_name, account_id, exc)
            all_ok = False
            continue

        if new_token is None:
            _log.info(
                "Skipped %s / %s: refresh returned None (non-expiring token).",
                platform_name, account_id,
            )
            continue

        upsert_token(
            cfg.db_path,
            platform=new_token.platform,
            account_id=new_token.account_id,
            token=new_token.token,
            token_type=new_token.token_type,
            issued_at=new_token.issued_at,
            expires_at=new_token.expires_at,
            scopes=new_token.scopes,
            last_refreshed=new_token.last_refreshed,
        )
        _log.info(
            "Refreshed %s / %s  new_expires_at=%s",
            platform_name, account_id, new_token.expires_at,
        )

    return all_ok


def main() -> None:
    try:
        cfg = SocialConfig.from_env()
    except ValueError as exc:
        _log.info("unprovisioned/no tokens — nothing to refresh (reason: %s)", exc)
        sys.exit(0)
    success = run(cfg)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
