from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from claude_soma.api.auth import require_authed_user
from claude_soma.mcp_servers.project_orchestrator.registry import Registry


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/routines", dependencies=[Depends(require_authed_user)])


def _call_claude_routines(
    action: str, body: dict[str, Any] | None = None, trigger_id: str | None = None
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
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "")[-500:]
        raise RuntimeError(f"claude -p failed: {stderr}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("claude -p timed out (120s)") from e
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
        if "claude-soma" not in unit:
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
        if "claude-soma" not in line:
            continue
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
    try:
        res = _call_claude_routines("list")
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
) -> list[dict[str, Any]]:
    """Registry rows are canonical; local + cloud fill in run timestamps.

    Entries seen only by systemd or the cloud are surfaced with synthesized
    created_by values so nothing is hidden.
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
        for alias in _candidate_unit_names(canonical_name):
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

    return sorted(merged.values(), key=lambda r: r["name"])


@router.get("")
def list_routines() -> list[dict[str, Any]]:
    registry_rows = _query_registry_routines()
    local_rows = _query_local_timers()
    cloud_rows = _query_cloud_routines()
    return _merge_routines(registry_rows, local_rows, cloud_rows)


@router.post("/{trigger_id}/run")
def run_routine(trigger_id: str) -> dict[str, Any]:
    try:
        return _call_claude_routines("run", trigger_id=trigger_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
