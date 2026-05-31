from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from mcp.server.fastmcp import FastMCP

from claude_soma.mcp_servers.hermes_api.notify_store import (
    EventStore,
    VALID_TYPES,
)
from claude_soma.mcp_servers.project_orchestrator.registry import Registry

mcp = FastMCP("hermes_notify")

_NOTIFY_PORT_DEFAULT = 9100
_ORCH_DB_DEFAULT = "/opt/claude-soma/registry.sqlite"

# Payload field limits (per PLAN-FI-NOTIFY.md)
_MAX_DESCRIPTION = 500
_MAX_ETA = 100
_MAX_PROGRESS = 300
_MAX_SUMMARY = 2000
_MAX_QUESTION = 500
_MAX_ERROR = 500
_MAX_CONTEXT = 500
_MAX_TRACEBACK = 5000

_store: EventStore | None = None
_registry: Registry | None = None


def _get_store() -> EventStore:
    global _store
    if _store is None:
        _store = EventStore()
    return _store


def _get_registry() -> Registry:
    global _registry
    if _registry is None:
        db_path = os.environ.get("HERMES_ORCH_DB", _ORCH_DB_DEFAULT)
        _registry = Registry(db_path)
    return _registry


def _reset_registry_for_tests() -> None:
    global _registry
    _registry = None


def _lead_name() -> str | None:
    return os.environ.get("HERMES_LEAD_NAME") or None


def _notify_port() -> int:
    return int(os.environ.get("HERMES_NOTIFY_PORT", str(_NOTIFY_PORT_DEFAULT)))


def _post_to_listener(event_id: int, lead: str, type_: str, payload_json: str) -> bool:
    """POST the event to the hermes_api HTTP listener. Returns True on 2xx."""
    url = f"http://127.0.0.1:{_notify_port()}/notify"
    body = json.dumps({
        "event_id": event_id,
        "lead": lead,
        "type": type_,
        "payload_json": payload_json,
    }).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError):
        return False


def _validate_started(payload: dict) -> str | None:
    """Return error string or None if valid."""
    desc = payload.get("description")
    if not desc or not isinstance(desc, str) or not desc.strip():
        return "STARTED requires non-empty 'description'"
    if len(desc) > _MAX_DESCRIPTION:
        return f"'description' exceeds {_MAX_DESCRIPTION} chars"
    eta = payload.get("eta")
    if eta is not None and (not isinstance(eta, str) or len(eta) > _MAX_ETA):
        return f"'eta' must be a string <= {_MAX_ETA} chars"
    return None


def _validate_milestone(payload: dict) -> str | None:
    prog = payload.get("progress")
    if not prog or not isinstance(prog, str) or not prog.strip():
        return "MILESTONE requires non-empty 'progress'"
    if len(prog) > _MAX_PROGRESS:
        return f"'progress' exceeds {_MAX_PROGRESS} chars"
    pct = payload.get("percent")
    if pct is not None:
        if not isinstance(pct, int) or not (0 <= pct <= 100):
            return "'percent' must be an integer in [0, 100]"
    eta_rem = payload.get("eta_remaining")
    if eta_rem is not None and (not isinstance(eta_rem, str) or len(eta_rem) > _MAX_ETA):
        return f"'eta_remaining' must be a string <= {_MAX_ETA} chars"
    return None


def _validate_completed(payload: dict) -> str | None:
    summary = payload.get("summary")
    if not summary or not isinstance(summary, str) or not summary.strip():
        return "COMPLETED requires non-empty 'summary'"
    if len(summary) > _MAX_SUMMARY:
        return f"'summary' exceeds {_MAX_SUMMARY} chars"
    paths = payload.get("paths", [])
    if not isinstance(paths, list) or len(paths) > 10:
        return "'paths' must be a list of at most 10 items"
    for p in paths:
        if not isinstance(p, str) or not p.startswith("/"):
            return "each path must be an absolute path string starting with '/'"
    urls = payload.get("urls", [])
    if not isinstance(urls, list) or len(urls) > 10:
        return "'urls' must be a list of at most 10 items"
    for u in urls:
        if not isinstance(u, str) or not u.startswith("http"):
            return "each URL must be a string starting with 'http'"
    return None


def _validate_needs_input(payload: dict) -> str | None:
    question = payload.get("question")
    if not question or not isinstance(question, str) or not question.strip():
        return "NEEDS_INPUT requires non-empty 'question'"
    if len(question) > _MAX_QUESTION:
        return f"'question' exceeds {_MAX_QUESTION} chars"
    options = payload.get("options")
    if options is not None:
        if not isinstance(options, list) or len(options) > 5:
            return "'options' must be a list of at most 5 items"
        for o in options:
            if not isinstance(o, str) or not o.strip() or len(o) > 100:
                return "each option must be a non-empty string <= 100 chars"
    timeout = payload.get("timeout")
    if timeout is not None:
        if not isinstance(timeout, int) or timeout <= 0 or timeout > 3600:
            return "'timeout' must be a positive integer <= 3600"
    return None


def _validate_error(payload: dict) -> str | None:
    error = payload.get("error")
    if not error or not isinstance(error, str) or not error.strip():
        return "ERROR requires non-empty 'error'"
    if len(error) > _MAX_ERROR:
        return f"'error' exceeds {_MAX_ERROR} chars"
    context = payload.get("context")
    if not context or not isinstance(context, str) or not context.strip():
        return "ERROR requires non-empty 'context'"
    if len(context) > _MAX_CONTEXT:
        return f"'context' exceeds {_MAX_CONTEXT} chars"
    recoverable = payload.get("recoverable")
    if recoverable is not None and not isinstance(recoverable, bool):
        return "'recoverable' must be a JSON boolean"
    return None


_VALIDATORS = {
    "STARTED": _validate_started,
    "MILESTONE": _validate_milestone,
    "COMPLETED": _validate_completed,
    "NEEDS_INPUT": _validate_needs_input,
    "ERROR": _validate_error,
}


def _normalize_payload(type_: str, payload: dict) -> dict:
    """Normalize + truncate payload fields per type limits."""
    p = dict(payload)
    if type_ == "ERROR":
        tb = p.get("traceback", "")
        if isinstance(tb, str) and len(tb) > _MAX_TRACEBACK:
            p["traceback"] = tb[:_MAX_TRACEBACK] + "..."
        if "recoverable" not in p:
            p["recoverable"] = True
    return p


@mcp.tool()
def notify_orchestrator(
    type: str,
    payload: dict,
) -> dict:
    """Signal the orchestrator about a lead lifecycle event.

    type must be one of: STARTED, MILESTONE, COMPLETED, NEEDS_INPUT, ERROR.
    payload must match the schema for the given type (see PLAN-FI-NOTIFY.md).
    The lead name is read automatically from the HERMES_LEAD_NAME environment
    variable — you do not supply it.

    Returns:
        {"stored_id": int, "delivered": bool}
        stored_id: the SQLite row id for the event (durable; survives restarts).
        delivered: true if the DM to the user was queued for immediate delivery;
                   false if only stored (will be delivered on next drain cycle).
    """
    lead = _lead_name()
    if not lead:
        raise ValueError(
            "HERMES_LEAD_NAME is not set in the environment. "
            "This tool is only available in spawned project leads."
        )

    if type not in VALID_TYPES:
        raise ValueError(
            f"unknown event type {type!r}; must be one of "
            f"{sorted(VALID_TYPES)}"
        )

    validator = _VALIDATORS[type]
    err = validator(payload)
    if err:
        raise ValueError(f"payload validation failed for {type}: {err}")

    payload = _normalize_payload(type, payload)
    payload_json = json.dumps(payload)

    store = _get_store()

    if type == "NEEDS_INPUT":
        options_json = json.dumps(payload.get("options", [])) if payload.get("options") else None
        timeout_secs = payload.get("timeout")
        event_id, _pending_id = store.insert_event_with_pending_input(
            lead=lead,
            ts=time.time(),
            payload_json=payload_json,
            question=payload["question"],
            options_json=options_json,
            timeout_secs=timeout_secs,
        )
    else:
        event_id = store.insert_event(
            lead=lead,
            type_=type,
            ts=time.time(),
            payload_json=payload_json,
        )

    delivered = _post_to_listener(event_id, lead, type, payload_json)
    return {"stored_id": event_id, "delivered": delivered}


@mcp.tool()
def set_teammate_handle(handle: str, role: str) -> dict:
    """Self-report this lead's canonical teammate handle into the registry.

    Writes (HERMES_LEAD_NAME, handle, role) into team_members so the orchestrator
    can surface the real @handle instead of the pane-derived teammate-N placeholder.
    Call this once early in a task to register the lead's identity.

    Returns:
        {"lead": str, "handle": str, "role": str}
    """
    lead = _lead_name()
    if not lead:
        raise ValueError(
            "HERMES_LEAD_NAME is not set in the environment. "
            "This tool is only available in spawned project leads."
        )
    if not handle or not handle.strip():
        raise ValueError("handle must be a non-empty string")
    if not role or not role.strip():
        raise ValueError("role must be a non-empty string")

    handle = handle.strip()
    role = role.strip()
    _get_registry().upsert_team_member(
        lead_name=lead,
        teammate_handle=handle,
        role=role,
        brief="self-reported",
    )
    return {"lead": lead, "handle": handle, "role": role}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
