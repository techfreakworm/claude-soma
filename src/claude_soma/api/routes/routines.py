from __future__ import annotations

import json
import os
import subprocess

from fastapi import APIRouter, Depends, HTTPException

from claude_soma.api.auth import require_authed_user


router = APIRouter(prefix="/routines", dependencies=[Depends(require_authed_user)])


def _call_claude_routines(
    action: str, body: dict | None = None, trigger_id: str | None = None
) -> dict:
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
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"claude -p failed: {r.stderr[:500]}")
    last = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "{}"
    return json.loads(last)


@router.get("")
def list_routines() -> list[dict]:
    try:
        res = _call_claude_routines("list")
    except Exception:
        return []
    return res.get("triggers", []) if isinstance(res, dict) else []


@router.post("/{trigger_id}/run")
def run_routine(trigger_id: str) -> dict:
    try:
        return _call_claude_routines("run", trigger_id=trigger_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
