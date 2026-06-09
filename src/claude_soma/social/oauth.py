"""OAuth state (CSRF nonce) management for FI-SOCIAL-SERVICE.

States are held in memory; they are single-use and expire after 10 minutes.
Thread-safe via a threading.Lock.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import Final, TypedDict


_STATE_TTL: Final[int] = 600  # 10 minutes


class _StateEntry(TypedDict):
    platform: str
    created_at: int


class StateStore:
    """Thread-safe, single-use CSRF state store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, _StateEntry] = {}

    def generate(self, platform: str) -> str:
        """Mint a new state token and store it.  Returns the state string."""
        state = secrets.token_urlsafe(32)
        now = int(time.time())
        with self._lock:
            self._states[state] = _StateEntry(platform=platform, created_at=now)
        return state

    def validate_and_consume(self, state: str) -> str:
        """Validate the state, consume it (single-use), return the platform name.

        Raises ValueError on unknown, already-used, or expired state.
        """
        now = int(time.time())
        with self._lock:
            entry = self._states.pop(state, None)

        if entry is None:
            raise ValueError("Invalid or already-used OAuth state token.")

        created_at = entry["created_at"]
        if now - created_at > _STATE_TTL:
            raise ValueError(
                f"OAuth state token expired after {_STATE_TTL}s "
                f"(age={now - created_at}s)."
            )

        return entry["platform"]

    def prune_expired(self) -> int:
        """Remove expired entries.  Returns the count removed."""
        now = int(time.time())
        expired = []
        with self._lock:
            for state, entry in list(self._states.items()):
                if now - entry["created_at"] > _STATE_TTL:
                    expired.append(state)
            for s in expired:
                del self._states[s]
        return len(expired)
