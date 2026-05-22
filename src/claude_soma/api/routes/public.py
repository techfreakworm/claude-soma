from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter

from claude_soma.api.bridge import call_hermes


router = APIRouter()
_started = time.time()


@router.get("/public/stats")
async def public_stats() -> dict:
    activity = await call_hermes("read_activity_log", {"limit": 5000})
    today = datetime.now(timezone.utc).date().isoformat()
    items = activity.get("items", []) if isinstance(activity, dict) else []
    today_items = [x for x in items if x.get("ts", "").startswith(today)]
    sessions = await call_hermes("list_sessions", {})
    actives = sessions.get("items", []) if isinstance(sessions, dict) else []
    return {
        "messages_today": sum(
            1 for x in today_items if x.get("tool") == "channel_message"
        ),
        "active_projects": len([s for s in actives if s.get("status") == "running"]),
        "decisions_today": len(today_items),
        "uptime_hours": round((time.time() - _started) / 3600.0, 1),
    }
