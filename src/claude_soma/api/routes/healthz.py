from __future__ import annotations

import time

from fastapi import APIRouter


router = APIRouter()
_started = time.time()


@router.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "uptime_seconds": round(time.time() - _started, 1)}
