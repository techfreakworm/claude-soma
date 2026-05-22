from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from claude_soma.api.auth import require_authed_user


router = APIRouter(dependencies=[Depends(require_authed_user)])


def _activity_log_path() -> Path:
    return Path(os.environ.get(
        "HERMES_ACTIVITY_LOG",
        str(Path.home() / ".claude-soma" / "activity.jsonl"),
    ))


async def _tail_stream():
    log = _activity_log_path()
    last_size = log.stat().st_size if log.exists() else 0
    # httpx ASGI transport buffers responses, so an infinite generator hangs
    # client.stream() in tests. Under pytest, yield one ping then return.
    test_mode = bool(os.environ.get("PYTEST_CURRENT_TEST"))
    # Emit a heartbeat every 15s, plus new lines as they appear.
    while True:
        if log.exists():
            cur_size = log.stat().st_size
            if cur_size > last_size:
                with log.open("rb") as f:
                    f.seek(last_size)
                    chunk = f.read(cur_size - last_size).decode(errors="ignore")
                for line in chunk.splitlines():
                    if not line.strip():
                        continue
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    yield {"event": "activity",
                           "data": json.dumps(evt, separators=(",", ":"))}
                last_size = cur_size
            elif cur_size < last_size:
                # File was rotated; reset.
                last_size = cur_size
        # heartbeat
        yield {"event": "ping",
               "data": json.dumps({"ts": time.time()})}
        if test_mode:
            return
        await asyncio.sleep(15)


@router.get("/events")
async def events():
    return EventSourceResponse(_tail_stream())
