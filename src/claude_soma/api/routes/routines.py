from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from filelock import FileLock

from claude_soma.api.auth import require_authed_user
from claude_soma.mcp_servers.project_orchestrator.registry import Registry


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/routines", dependencies=[Depends(require_authed_user)])

# Cron sources to scan for system-level schedules. Module-level so tests can
# point them at fixtures instead of the real /etc.
ETC_CRONTAB = "/etc/crontab"
CRON_D_DIR = "/etc/cron.d"

# The cloud-routines query shells out to `claude -p`, which takes ~12s (it boots
# a whole claude process). That dominated /api/routines latency, so we cache its
# result briefly: only the first request per TTL window pays the cost. Cloud
# routines change rarely, so a short TTL is safe.
_CLOUD_CACHE: dict[str, Any] = {"ts": 0.0, "rows": [], "valid": False}


def _cloud_ttl() -> float:
    return float(os.environ.get("HERMES_ROUTINES_CLOUD_TTL", "300"))


def _clear_routines_cache() -> None:
    """Reset the cloud cache. Used by tests so cached cloud results don't leak
    between cases."""
    _CLOUD_CACHE.update(ts=0.0, rows=[], valid=False)


def _call_claude_routines(
    action: str,
    body: dict[str, Any] | None = None,
    trigger_id: str | None = None,
    timeout: float = 120,
) -> dict[str, Any]:
    """Invoke RemoteTrigger via `claude -p` so we don't reimplement the API."""
    cmd = [
        os.environ.get("HERMES_CLAUDE_BIN", "claude"),
        "-p",
        "--output-format",
        "json",
        f"Use the RemoteTrigger tool with action={action}"
        + (f", trigger_id={trigger_id}" if trigger_id else "")
        + (f", body={body!r}" if body else ""),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "")[-500:]
        raise RuntimeError(f"claude -p failed: {stderr}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"claude -p timed out ({timeout}s)") from e
    if r.returncode != 0:
        raise RuntimeError(f"claude -p failed: {r.stderr[-500:]}")
    last = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "{}"
    return json.loads(last)  # type: ignore[no-any-return]


def _us_to_seconds(value: Any) -> float | None:
    """systemctl JSON emits timestamps in microseconds since epoch; 0 means unset."""
    try:
        ival = int(value)
    except (TypeError, ValueError):
        return None
    if ival <= 0:
        return None
    return ival / 1_000_000.0


def _systemctl_show_field(unit: str, field: str) -> str | None:
    try:
        r = subprocess.run(
            ["systemctl", "show", "--property", field, "--value", unit],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.CalledProcessError as e:
        logger.debug("systemctl show %s %s failed: %s", unit, field, e.stderr[-200:])
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.debug("systemctl show %s %s unavailable: %s", unit, field, e)
        return None
    if r.returncode != 0:
        return None
    out = r.stdout.strip()
    return out or None


def _query_local_timers() -> list[dict[str, Any]]:
    """Return the live claude-soma-* systemd timers as routine dicts.

    Tries `systemctl list-timers --output=json` first; falls back to text
    parsing if JSON output isn't supported (older systemd).
    """
    try:
        r = subprocess.run(
            ["systemctl", "list-timers", "--all", "--no-pager", "--output=json"],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.CalledProcessError as e:
        logger.warning("systemctl list-timers failed: %s", e.stderr[-200:])
        return []
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("systemctl list-timers unavailable: %s", e)
        return []
    if r.returncode != 0:
        return _parse_text_timers(r.stdout)

    try:
        entries = json.loads(r.stdout) if r.stdout.strip() else []
    except json.JSONDecodeError:
        return _parse_text_timers(r.stdout)

    out: list[dict[str, Any]] = []
    for entry in entries:
        unit = entry.get("unit") or ""
        # Include ALL timers (the user wants every local schedule visible, not
        # only claude-soma ones); skip blank/non-timer rows list-timers emits.
        if not unit.endswith(".timer"):
            continue
        schedule = _systemctl_show_field(unit, "OnCalendar") or ""
        out.append(
            {
                "name": unit,
                "kind": "local",
                "schedule": schedule,
                "next_run": _us_to_seconds(entry.get("next")),
                "last_run": _us_to_seconds(entry.get("last")),
            }
        )
    return out


def _parse_text_timers(stdout: str) -> list[dict[str, Any]]:
    """Fallback parser for systemd that lacks --output=json."""
    out: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        parts = line.split()
        unit = next((p for p in parts if p.endswith(".timer")), None)
        if not unit:
            continue
        schedule = _systemctl_show_field(unit, "OnCalendar") or ""
        out.append(
            {
                "name": unit,
                "kind": "local",
                "schedule": schedule,
                "next_run": None,
                "last_run": None,
            }
        )
    return out


def _query_cloud_routines() -> list[dict[str, Any]]:
    # Cap the claude -p call well under its old 120s so a hung cloud query can't
    # wedge the page; if it overruns we just return [] and the other sources
    # still render.
    timeout = float(os.environ.get("HERMES_ROUTINES_CLOUD_TIMEOUT", "30"))
    try:
        res = _call_claude_routines("list", timeout=timeout)
    except Exception as exc:
        logger.warning("cloud routines query failed: %s", exc)
        return []
    triggers = res.get("triggers", []) if isinstance(res, dict) else []
    out: list[dict[str, Any]] = []
    for t in triggers:
        if not isinstance(t, dict) or "name" not in t:
            continue
        out.append(
            {
                "name": t["name"],
                "kind": "cloud",
                "schedule": t.get("schedule", ""),
                "next_run": t.get("next_run"),
                "last_run": t.get("last_run"),
            }
        )
    return out


def _query_cloud_routines_cached() -> list[dict[str, Any]]:
    """Cloud query is the slow one (~12s, spawns claude). Serve a cached result
    within the TTL window so repeated dashboard loads don't re-pay it.

    Uses a file lock to prevent 'thundering herd' when multiple requests arrive
    at a cold cache.
    """
    now = time.monotonic()
    if _CLOUD_CACHE["valid"] and (now - _CLOUD_CACHE["ts"]) < _cloud_ttl():
        return list(_CLOUD_CACHE["rows"])

    lock_path = "/tmp/hermes-routines.lock"
    with FileLock(lock_path, timeout=35):
        # Re-check cache inside the lock: another thread/process might have
        # populated it while we waited.
        now = time.monotonic()
        if _CLOUD_CACHE["valid"] and (now - _CLOUD_CACHE["ts"]) < _cloud_ttl():
            return list(_CLOUD_CACHE["rows"])

        rows = _query_cloud_routines()
        _CLOUD_CACHE.update(ts=now, rows=rows, valid=True)
        return rows


def _parse_cron_line(line: str, *, system: bool, source: str) -> dict[str, Any] | None:
    """Parse one crontab line into a routine dict, or None if it's not a job
    (blank / comment / env assignment). `system` lines (/etc/crontab,
    /etc/cron.d) have a user field between the schedule and the command; user
    crontabs (`crontab -l`) do not."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    # Skip env assignments like SHELL=/bin/sh, PATH=..., MAILTO=... (a NAME=VALUE
    # token before any whitespace).
    first = stripped.split()[0]
    if "=" in first and not first.startswith("@"):
        return None

    if stripped.startswith("@"):  # @reboot/@daily/@hourly... macros
        parts = stripped.split(None, 2 if system else 1)
        schedule = parts[0]
        rest = parts[1:]
    else:
        # 5 schedule fields, then (system: user) then command.
        nfields = 6 if system else 5
        parts = stripped.split(None, nfields)
        if len(parts) <= (nfields - 1):
            return None
        schedule = " ".join(parts[:5])
        rest = parts[5:]
    command = (rest[-1] if rest else "").strip()
    if not command:
        return None
    return {
        "name": f"cron: {command}"[:120],
        "kind": "local",
        "schedule": schedule,
        "target_skill": None,
        "description": f"cron ({source})",
        "next_run": None,
        "last_run": None,
        "created_by": "cron",
    }


def _query_cron_routines() -> list[dict[str, Any]]:
    """Aggregate cron jobs from the user crontab, /etc/crontab, and /etc/cron.d.

    Best-effort: each source is independent and unreadable ones are skipped, so
    a missing crontab or a permission-denied file never breaks the listing."""
    out: list[dict[str, Any]] = []

    # User crontab (no user field).
    try:
        r = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                row = _parse_cron_line(line, system=False, source="user crontab")
                if row:
                    out.append(row)
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("crontab -l unavailable: %s", exc)

    # System crontab + drop-ins (have a user field).
    files: list[Path] = [Path(ETC_CRONTAB)]
    try:
        files.extend(sorted(Path(CRON_D_DIR).glob("*")))
    except OSError as exc:
        logger.debug("listing %s failed: %s", CRON_D_DIR, exc)
    for path in files:
        try:
            text = path.read_text()
        except OSError:
            continue  # missing or unreadable -- skip
        for line in text.splitlines():
            row = _parse_cron_line(line, system=True, source=path.name)
            if row:
                out.append(row)
    return out


def _registry() -> Registry:
    db = os.environ.get("HERMES_ORCH_DB", "/opt/claude-soma/registry.sqlite")
    return Registry(db)


def _query_registry_routines() -> list[dict[str, Any]]:
    try:
        reg = _registry()
    except Exception as exc:
        logger.warning("registry open failed: %s", exc)
        return []
    try:
        return reg.list_routines()
    finally:
        reg.close()


def _empty_merged_row(name: str, kind: str, created_by: str) -> dict[str, Any]:
    return {
        "name": name,
        "kind": kind,
        "schedule": "",
        "target_skill": None,
        "description": None,
        "last_run": None,
        "next_run": None,
        "created_by": created_by,
    }


def _candidate_unit_names(name: str) -> set[str]:
    """A local routine in the registry may be tracked by a friendly name like
    'portfolio-oneliner' while systemd lists it as
    'claude-soma-portfolio-oneliner.timer'. Generate the candidate aliases."""
    bare = name.removesuffix(".timer")
    return {
        name,
        f"{name}.timer",
        f"claude-soma-{bare}.timer",
        f"claude-soma-{bare}",
    }


def _merge_routines(
    registry_rows: list[dict[str, Any]],
    local_rows: list[dict[str, Any]],
    cloud_rows: list[dict[str, Any]],
    cron_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Registry rows are canonical; local + cloud fill in run timestamps.

    Entries seen only by systemd, cron, or the cloud are surfaced with
    synthesized created_by values so nothing is hidden.
    """
    merged: dict[str, dict[str, Any]] = {}

    for row in registry_rows:
        merged[row["name"]] = {
            "name": row["name"],
            "kind": row["kind"],
            "schedule": row["schedule"],
            "target_skill": row.get("target_skill"),
            "description": row.get("description"),
            "last_run": row.get("last_run"),
            "next_run": row.get("next_run"),
            "created_by": row.get("created_by", "bot"),
        }

    local_by_name = {row["name"]: row for row in local_rows}
    consumed_local: set[str] = set()
    for canonical_name, entry in merged.items():
        if entry["kind"] != "local":
            continue
        meta = entry.get("metadata") or {}
        unit_hint = meta.get("unit") if isinstance(meta, dict) else None
        candidates = {unit_hint} if unit_hint else _candidate_unit_names(canonical_name)
        for alias in candidates:
            if alias in local_by_name:
                live = local_by_name[alias]
                if live.get("last_run") is not None:
                    entry["last_run"] = live["last_run"]
                if live.get("next_run") is not None:
                    entry["next_run"] = live["next_run"]
                consumed_local.add(alias)
                break

    for row in local_rows:
        name = row["name"]
        if name in consumed_local or name in merged:
            continue
        merged[name] = _empty_merged_row(name, "local", "system")
        merged[name]["schedule"] = row.get("schedule", "")
        if row.get("last_run") is not None:
            merged[name]["last_run"] = row["last_run"]
        if row.get("next_run") is not None:
            merged[name]["next_run"] = row["next_run"]

    cloud_by_name = {row["name"]: row for row in cloud_rows}
    consumed_cloud: set[str] = set()
    for canonical_name, entry in merged.items():
        if entry["kind"] != "cloud":
            continue
        if canonical_name in cloud_by_name:
            live = cloud_by_name[canonical_name]
            if live.get("last_run") is not None:
                entry["last_run"] = live["last_run"]
            if live.get("next_run") is not None:
                entry["next_run"] = live["next_run"]
            consumed_cloud.add(canonical_name)

    for row in cloud_rows:
        name = row["name"]
        if name in consumed_cloud or name in merged:
            continue
        merged[name] = _empty_merged_row(name, "cloud", "cloud")
        merged[name]["schedule"] = row.get("schedule", "")
        if row.get("last_run") is not None:
            merged[name]["last_run"] = row["last_run"]
        if row.get("next_run") is not None:
            merged[name]["next_run"] = row["next_run"]

    # Cron jobs are already complete routine dicts (created_by="cron"); add them
    # as-is so the standalone-local path above doesn't relabel them "system".
    for row in cron_rows or []:
        if row["name"] not in merged:
            merged[row["name"]] = dict(row)

    return sorted(merged.values(), key=lambda r: r["name"])


@router.get("")
def list_routines() -> list[dict[str, Any]]:
    # Run the four sources concurrently: registry (sqlite) and the systemctl /
    # crontab shell-outs are fast, but the cloud query spawns claude (~12s when
    # cold). Parallelizing means the cron shell-outs added here don't stack onto
    # the latency, and a cold cloud query overlaps the rest instead of summing.
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_registry = pool.submit(_query_registry_routines)
        f_local = pool.submit(_query_local_timers)
        f_cron = pool.submit(_query_cron_routines)
        f_cloud = pool.submit(_query_cloud_routines_cached)
        registry_rows = f_registry.result()
        local_rows = f_local.result()
        cron_rows = f_cron.result()
        cloud_rows = f_cloud.result()
    return _merge_routines(registry_rows, local_rows, cloud_rows, cron_rows)


@router.post("/{trigger_id}/run")
def run_routine(trigger_id: str) -> dict[str, Any]:
    lock_path = "/tmp/hermes-routines-run.lock"
    try:
        with FileLock(lock_path, timeout=60):
            return _call_claude_routines("run", trigger_id=trigger_id, timeout=30)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
