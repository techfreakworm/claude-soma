"""Config loader for FI-SOCIAL-SERVICE.

Reads the user-provisioned env file (HERMES_SOCIAL_CONFIG, default
~/.config/social-manager/meta-tokens.env) and merges with os.environ.
The resulting SocialConfig is held in memory; nothing is written to the vault.

The three App ID/Secret pairs are never written to the vault.  They stay in
memory only for the lifetime of the process.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Internal env-file parser (stdlib only, no python-dotenv)
# ---------------------------------------------------------------------------

def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE env file, ignoring comments and blank lines."""
    result: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return result
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        key = k.strip()
        val = v.strip()
        # Strip optional surrounding quotes (single or double).
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1]
        if key:
            result[key] = val
    return result


def _load_env() -> dict[str, str]:
    """Merge env file (if present) with os.environ.  os.environ wins on conflict."""
    env_file = Path(
        os.environ.get("HERMES_SOCIAL_CONFIG",
                       os.path.expanduser("~/.config/social-manager/meta-tokens.env"))
    )
    merged = _parse_env_file(env_file)
    # os.environ always overrides the file so that EnvironmentFile= in the
    # systemd unit takes precedence (the unit loads into the process env).
    merged.update(os.environ)
    return merged


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SocialConfig:
    # App credentials (three independent spaces).
    meta_app_id: str
    meta_app_secret: str
    ig_app_id: str
    ig_app_secret: str
    threads_app_id: str
    threads_app_secret: str

    # Webhook verification.
    webhook_verify_token: str

    # Pre-provisioned long-lived tokens (bootstrap material).
    # Any of these may be empty string — bootstrap skips missing ones.
    ig_long_lived_token: str
    ig_user_id: str
    fb_page_id: str
    fb_page_access_token: str
    threads_long_lived_token: str
    threads_user_id: str

    # Runtime paths / tuning (not secrets).
    db_path: str = field(
        default_factory=lambda: os.environ.get(
            "HERMES_SOCIAL_DB", "/var/lib/claude-soma/social.sqlite"
        )
    )
    port: int = field(
        default_factory=lambda: int(os.environ.get("HERMES_SOCIAL_PORT", "9200"))
    )
    refresh_window_days: int = field(
        default_factory=lambda: int(
            os.environ.get("HERMES_SOCIAL_REFRESH_WINDOW_DAYS", "7")
        )
    )
    redirect_uri: str = field(
        default_factory=lambda: os.environ.get(
            "HERMES_SOCIAL_REDIRECT_URI",
            "https://social.mayankgupta.in/oauth/callback",
        )
    )

    @classmethod
    def from_env(cls) -> "SocialConfig":
        """Load config from env file + os.environ.  Raises ValueError on missing keys."""
        env = _load_env()

        def _req(key: str) -> str:
            val = env.get(key, "").strip()
            if not val:
                raise ValueError(
                    f"HERMES_SOCIAL_SERVICE: required env var '{key}' is missing or empty. "
                    f"Populate {os.environ.get('HERMES_SOCIAL_CONFIG', '~/.config/social-manager/meta-tokens.env')}."
                )
            return val

        def _opt(key: str) -> str:
            return env.get(key, "").strip()

        return cls(
            meta_app_id=_req("META_APP_ID"),
            meta_app_secret=_req("META_APP_SECRET"),
            ig_app_id=_req("IG_APP_ID"),
            ig_app_secret=_req("IG_APP_SECRET"),
            threads_app_id=_req("THREADS_APP_ID"),
            threads_app_secret=_req("THREADS_APP_SECRET"),
            webhook_verify_token=_req("WEBHOOK_VERIFY_TOKEN"),
            ig_long_lived_token=_opt("IG_LONG_LIVED_TOKEN"),
            ig_user_id=_opt("IG_USER_ID"),
            fb_page_id=_opt("FB_PAGE_ID"),
            fb_page_access_token=_opt("FB_PAGE_ACCESS_TOKEN"),
            threads_long_lived_token=_opt("THREADS_LONG_LIVED_TOKEN"),
            threads_user_id=_opt("THREADS_USER_ID"),
        )
