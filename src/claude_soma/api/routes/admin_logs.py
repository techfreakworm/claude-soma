from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from claude_soma.api.auth import require_authed_user


_LOG_DIR_DEFAULT = "/var/log/claude-soma"
_CHUNK_BYTES = 80 * 1024  # 80 KB per read
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_\-][A-Za-z0-9_\-.]{0,127}$")
_ANSI = re.compile(r"\x1b\[[0-9;]*[mKHfABCDsuJ]")

router = APIRouter(prefix="/admin/logs", dependencies=[Depends(require_authed_user)])


class LogPage(BaseModel):
    lines: list[str]
    total_bytes: int
    has_more: bool
    start_byte: int


def _log_dir() -> Path:
    return Path(os.environ.get("HERMES_LEAD_LOG_DIR", _LOG_DIR_DEFAULT))


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def _validate_lead(lead_name: str) -> str:
    if not _SAFE_NAME.match(lead_name) or ".." in lead_name:
        raise HTTPException(status_code=400, detail=f"invalid lead name: {lead_name!r}")
    return lead_name


def _guard_path(log_path: Path, log_dir: Path) -> None:
    try:
        log_path.resolve().relative_to(log_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="path outside log directory")


@router.get("/{lead_name}", response_model=LogPage)
def get_lead_log(
    lead_name: str,
    offset: int | None = None,
    limit: int = 1000,
) -> LogPage:
    lead = _validate_lead(lead_name)
    log_dir = _log_dir()
    log_path = log_dir / f"{lead}.log"
    _guard_path(log_path, log_dir)

    if not log_path.exists():
        return LogPage(lines=[], total_bytes=0, has_more=False, start_byte=0)

    with log_path.open("rb") as fh:
        fh.seek(0, 2)
        total_bytes = fh.tell()

        if total_bytes == 0:
            return LogPage(lines=[], total_bytes=0, has_more=False, start_byte=0)

        if offset is None:
            # Tail mode: read the last _CHUNK_BYTES, return last `limit` lines.
            start = max(0, total_bytes - _CHUNK_BYTES)
            fh.seek(start)
            raw = fh.read(_CHUNK_BYTES)
            text = raw.decode("utf-8", errors="replace")
            all_lines = text.split("\n")
            if start > 0 and all_lines:
                all_lines = all_lines[1:]
            lines = [_strip_ansi(ln) for ln in all_lines if ln][-limit:]
            return LogPage(
                lines=lines,
                total_bytes=total_bytes,
                has_more=start > 0,
                start_byte=start,
            )
        else:
            # Offset mode: read forward from the given byte offset.
            start = max(0, min(offset, total_bytes))
            fh.seek(start)
            raw = fh.read(_CHUNK_BYTES)
            at_eof = fh.tell() >= total_bytes
            text = raw.decode("utf-8", errors="replace")
            all_lines = text.split("\n")
            if not at_eof and all_lines:
                all_lines = all_lines[:-1]
            lines = [_strip_ansi(ln) for ln in all_lines if ln][:limit]
            return LogPage(
                lines=lines,
                total_bytes=total_bytes,
                has_more=start > 0,
                start_byte=start,
            )
