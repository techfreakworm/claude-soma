# src/claude_soma/mcp_servers/project_orchestrator/watchdog.py
"""External watchdog/reaper that revives silently-dead project leads.

Background
==========
Each project lead is a `claude` process inside a detached tmux session, wrapped
in a transient systemd unit created with `systemd-run --property=Type=oneshot
--property=RemainAfterExit=yes`. Because the unit is oneshot it reads
`active (exited)` forever and systemd never supervises the real claude process
(it's a child of the daemonized tmux server). So when a lead's claude dies --
crash, context/usage-limit exit, or a reap -- the death is INVISIBLE to systemd
and the lead simply stays dead.

This module is run from a systemd .timer every few minutes. It checks live tmux
(the only trustworthy liveness signal, via spawner.is_lead_alive) for every
revivable lead and respawns the genuinely-dead ones, with per-lead exponential
backoff so a lead that cannot be revived isn't hammered forever.

Design rules (see FI-LEAD-WATCHDOG):
- Liveness is ONLY live tmux; registry status is NOT trusted as ground truth.
- status 'killed' = intentional stop -> NEVER revived (filtered in
  Registry.list_revivable).
- A live lead is NEVER respawned; if it's alive but marked non-active we
  self-heal the status to 'active'.
- All revival goes through the low-level spawner fns directly (resume first if
  a session_uuid exists, else fresh spawn) -- NEVER spawn_project_impl (which
  would enforce the concurrency cap and re-reconcile).
- main() ALWAYS exits 0: a watchdog that crashes the timer unit is worse than
  one that no-ops a cycle.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .registry import Registry
from .spawner import (
    is_lead_alive,
    kill_session,
    resume_background_lead,
    spawn_background_lead,
)

logger = logging.getLogger(__name__)

DB_PATH_DEFAULT = "/opt/claude-soma/registry.sqlite"

# How many consecutive failed revive attempts before the watchdog gives up on a
# lead (sends a single give-up DM and stops trying until the cooldown lapses).
MAX_ATTEMPTS_DEFAULT = 3
# After giving up, how long (seconds) before the backoff state is reset and the
# watchdog tries again from scratch.
COOLDOWN_SEC_DEFAULT = 3600

# After a respawn, poll live tmux this many times at this interval to confirm
# the lead actually came up (claude can fail to start even when spawn returns 0).
# Both overridable via env so a slow-resuming lead isn't miscounted as dead.
GRACE_POLLS = 8
GRACE_INTERVAL_SEC = 1.0

# Telegram Bot API send timeout (fail-open, best-effort DM).
NOTIFY_TIMEOUT_SEC = 10


def _truthy(val: str | None) -> bool:
    return bool(val and val.strip().lower() not in {"0", "false", "no", "off"})


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _reg() -> Registry:
    return Registry(os.environ.get("HERMES_ORCH_DB", DB_PATH_DEFAULT))


def _notify(text: str) -> None:
    """Fail-open Telegram DM to the operator via the Bot API sendMessage.

    Reads TELEGRAM_BOT_TOKEN + HERMES_NOTIFY_CHAT_ID from the process env
    (systemd loads them via EnvironmentFile). Swallows ALL exceptions so a
    notification failure never affects revival. Never logs the token value.
    Uses urllib so the watchdog adds no new dependency.
    """
    try:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("HERMES_NOTIFY_CHAT_ID", "")
        if not token or not chat_id:
            logger.info("watchdog: notify skipped (token or chat_id missing)")
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        urllib.request.urlopen(req, timeout=NOTIFY_TIMEOUT_SEC).close()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.info("watchdog: notify failed: %s", exc)
    except Exception as exc:  # noqa: BLE001 -- DM must never raise
        logger.info("watchdog: notify failed (unexpected): %s", exc)


def _grace_alive(name: str) -> bool:
    """Poll live tmux up to GRACE_POLLS times; True as soon as the lead is alive."""
    polls = max(1, _env_int("HERMES_LEAD_WATCHDOG_GRACE_POLLS", GRACE_POLLS))
    interval = _env_float("HERMES_LEAD_WATCHDOG_GRACE_INTERVAL_SEC", GRACE_INTERVAL_SEC)
    for _ in range(polls):
        if is_lead_alive(name):
            return True
        time.sleep(interval)
    return is_lead_alive(name)


def run_once() -> dict[str, Any]:
    """Single watchdog sweep. Returns a summary dict."""
    summary: dict[str, Any] = {
        "checked": 0,
        "alive": 0,
        "reconciled": 0,
        "revived": 0,
        "failed": 0,
        "gaveup": 0,
        "dry_run": False,
    }

    if _truthy(os.environ.get("HERMES_LEAD_WATCHDOG_DISABLED")):
        logger.info("watchdog: disabled via HERMES_LEAD_WATCHDOG_DISABLED; skipping")
        summary["disabled"] = True
        return summary

    dry_run = _truthy(os.environ.get("HERMES_LEAD_WATCHDOG_DRY_RUN"))
    summary["dry_run"] = dry_run
    max_attempts = _env_int("HERMES_LEAD_WATCHDOG_MAX_ATTEMPTS", MAX_ATTEMPTS_DEFAULT)
    cooldown = _env_int("HERMES_LEAD_WATCHDOG_COOLDOWN_SEC", COOLDOWN_SEC_DEFAULT)

    reg = _reg()
    rows = reg.list_revivable()

    for row in rows:
        summary["checked"] += 1
        # Guard every lead independently: an unexpected error on one (a bad name,
        # a malformed cwd, an sqlite hiccup) must not abort revival for the rest.
        try:
            _process_row(
                reg, row,
                dry_run=dry_run,
                max_attempts=max_attempts,
                cooldown=cooldown,
                summary=summary,
            )
        except Exception as exc:  # noqa: BLE001 -- one bad lead must not abort the sweep
            logger.exception(
                "watchdog: error processing %s (skipping): %s", row.get("name"), exc,
            )
            summary["failed"] += 1

    return summary


def _process_row(
    reg: Registry,
    row: dict[str, Any],
    *,
    dry_run: bool,
    max_attempts: int,
    cooldown: int,
    summary: dict[str, Any],
) -> None:
    """Process one revivable lead: self-heal if alive, else revive with backoff."""
    name = row["name"]
    status = row["status"]

    if is_lead_alive(name):
        summary["alive"] += 1
        if status != "active":
            if dry_run:
                logger.info("watchdog: WOULD reconcile-alive %s (was %s)", name, status)
            else:
                # Self-heal: a live lead wrongly marked non-active (reconciled
                # during a transient relaunch and never flipped back). Bookkeeping
                # only, so don't bump the idle clock.
                reg.set_status(name, "active", bump_activity=False)
                summary["reconciled"] += 1
                logger.info("watchdog: reconciled-alive %s (was %s)", name, status)
        return  # NEVER respawn a live lead.

    # --- lead is genuinely dead: consider reviving with backoff ---
    state = reg.get_watchdog_state(name)
    if state and state["consecutive_failures"] >= max_attempts:
        last_ts = state["last_attempt_ts"] or 0.0
        if time.time() - last_ts < cooldown:
            # Still inside the give-up cooldown: stay quiet, but emit the give-up
            # DM exactly once.
            if not state["gaveup_notified"]:
                if not dry_run:
                    _notify(
                        f"Lead watchdog: GAVE UP on '{name}' after "
                        f"{state['consecutive_failures']} failed revive "
                        f"attempts (last: {state.get('last_outcome')!r}). "
                        f"Will retry after cooldown; manual attention needed."
                    )
                    reg.mark_gaveup_notified(name)
                summary["gaveup"] += 1
                logger.info("watchdog: gave-up %s (cooldown active)", name)
            return
        # Cooldown elapsed: wipe the backoff state and fall through to retry.
        reg.reset_watchdog(name)
        logger.info("watchdog: cooldown elapsed for %s; resetting backoff", name)

    method = "resume" if row.get("session_uuid") else "fresh"

    if dry_run:
        logger.info("watchdog: WOULD revive %s via %s", name, method)
        return

    # TOCTOU guard: re-check liveness right before the destructive kill+respawn.
    # Another revival path (operator resume_project, orchestrator auto-restart)
    # may have brought the lead back since the check above -- killing it here
    # would tear down a live session.
    if is_lead_alive(name):
        summary["alive"] += 1
        if status != "active":
            reg.set_status(name, "active", bump_activity=False)
            summary["reconciled"] += 1
        logger.info("watchdog: %s came back before revive; skipping", name)
        return

    cwd = Path(row["cwd"])
    permission_mode = row["permission_mode"]

    # Clear any lingering `active (exited)` unit so the respawn isn't rejected
    # with "already exists". Best-effort.
    try:
        kill_session(name)
    except Exception as exc:  # noqa: BLE001
        logger.info("watchdog: kill_session(%s) failed (continuing): %s", name, exc)

    spawn_err: str | None = None
    if method == "resume":
        try:
            resume_background_lead(
                name=name,
                cwd=cwd,
                permission_mode=permission_mode,
                session_uuid=row["session_uuid"],
                force=False,
            )
        except Exception as exc:  # noqa: BLE001 -- any resume failure -> fresh fallback
            # Context guard >200k tokens, a bad session, etc. -- fall back to a
            # fresh spawn with the stored brief.
            logger.info(
                "watchdog: resume(%s) failed, falling back to fresh: %s", name, exc,
            )
            method = "fresh"

    if method == "fresh":
        try:
            spawn = spawn_background_lead(
                name=name,
                brief=row.get("brief") or "",
                cwd=cwd,
                permission_mode=permission_mode,
            )
            reg.set_session_uuid(name, spawn["session_uuid"])
        except Exception as exc:  # noqa: BLE001
            spawn_err = str(exc)
            logger.info("watchdog: fresh spawn(%s) failed: %s", name, exc)

    if spawn_err is not None:
        # Spawn raised outright -- a failed attempt without a grace poll (nothing
        # was started).
        _record_failure(reg, name, method, spawn_err, max_attempts, summary, dry_run)
        return

    # Grace period: confirm the respawned lead actually came up.
    if _grace_alive(name):
        reg.set_status(name, "active", bump_activity=False)
        reg.record_revive_attempt(name, method=method, outcome="revived", success=True)
        summary["revived"] += 1
        logger.info("watchdog: revived %s via %s", name, method)
        _notify(f"Lead watchdog: revived '{name}' via {method}.")
    else:
        _record_failure(reg, name, method, "dead-after-grace", max_attempts, summary, dry_run)


def _record_failure(
    reg: Registry,
    name: str,
    method: str,
    outcome: str,
    max_attempts: int,
    summary: dict[str, Any],
    dry_run: bool,
) -> None:
    """Record a failed revive attempt and, if it reaches the give-up threshold,
    send the one-time give-up DM."""
    reg.record_revive_attempt(name, method=method, outcome=outcome[:200], success=False)
    summary["failed"] += 1
    logger.info("watchdog: revive failed for %s via %s (%s)", name, method, outcome)
    state = reg.get_watchdog_state(name)
    if state and state["consecutive_failures"] >= max_attempts and not state["gaveup_notified"]:
        if not dry_run:
            _notify(
                f"Lead watchdog: GAVE UP on '{name}' after "
                f"{state['consecutive_failures']} failed revive attempts "
                f"(last: {outcome!r}). Manual attention needed."
            )
            reg.mark_gaveup_notified(name)
        summary["gaveup"] += 1
        logger.info("watchdog: gave-up %s (reached max_attempts)", name)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        summary = run_once()
        logger.info("watchdog summary: %s", json.dumps(summary, sort_keys=True))
    except Exception as exc:  # noqa: BLE001 -- the timer unit must never fail
        logger.exception("watchdog: top-level error (exiting 0): %s", exc)
    # ALWAYS exit 0 so the systemd timer unit never enters a failed state.
    raise SystemExit(0)


if __name__ == "__main__":
    main()
