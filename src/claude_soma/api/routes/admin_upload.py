from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile

from claude_soma.api.auth import require_authed_user


_STAGING_DEFAULT = "/var/lib/claude-soma/staging"
_CHUNK = 1024 * 1024  # 1 MB
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_\-][A-Za-z0-9_\-.]{0,127}$")
_NOTIFY_PORT_DEFAULT = 9100

router = APIRouter(prefix="/admin/upload", dependencies=[Depends(require_authed_user)])


def _staging_root() -> Path:
    return Path(os.environ.get("HERMES_STAGING_ROOT", _STAGING_DEFAULT))


def _inbox(lead_name: str) -> Path:
    return _staging_root() / lead_name / "inbox"


def _safe(name: str, label: str) -> str:
    if not _SAFE_NAME.match(name) or ".." in name:
        raise HTTPException(status_code=400, detail=f"invalid {label}: {name!r}")
    return name


def _sanitize_filename(raw: str) -> str:
    """Produce a filesystem-safe filename from user-supplied raw name.

    Replaces spaces with underscores, strips characters outside
    [A-Za-z0-9_-.], strips leading dots, and caps length at 255.
    Raises 400 if nothing survives sanitization.
    """
    name = raw.replace(" ", "_")
    name = re.sub(r"[^A-Za-z0-9_\-.]", "", name)
    name = name.lstrip(".")
    if not name:
        raise HTTPException(status_code=400, detail=f"filename produces empty name after sanitization: {raw!r}")
    return name[:255]


async def _stream_to_file(upload: UploadFile, dest: Path) -> tuple[int, str]:
    hasher = hashlib.sha256()
    size = 0
    with dest.open("wb") as f:
        while True:
            chunk = await upload.read(_CHUNK)
            if not chunk:
                break
            f.write(chunk)
            hasher.update(chunk)
            size += len(chunk)
    return size, hasher.hexdigest()


def _write_manifest(dest: Path, name: str, size: int, sha256: str, uploaded_at: str) -> None:
    manifest = {
        "name": name,
        "size": size,
        "sha256": sha256,
        "uploaded_at": uploaded_at,
    }
    manifest_path = dest.with_suffix(dest.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")


def _notify_file_landed(lead_name: str, file_path: str) -> None:
    port = int(os.environ.get("HERMES_NOTIFY_PORT", str(_NOTIFY_PORT_DEFAULT)))
    payload = {
        "type": "NEEDS_INPUT",
        "payload": {
            "question": f"File uploaded for lead {lead_name!r}; review at {file_path}",
            "context": f"path={file_path}",
        },
    }
    try:
        with httpx.Client(timeout=5) as client:
            client.post(
                f"http://127.0.0.1:{port}/notify",
                json=payload,
                headers={"X-Lead-Name": lead_name},
            )
    except Exception:
        pass


@router.post("/{lead_name}")
async def upload_file(
    lead_name: str,
    file: UploadFile,
) -> dict:
    lead = _safe(lead_name, "lead_name")
    filename = _sanitize_filename(file.filename or "upload")

    inbox = _inbox(lead)
    inbox.mkdir(parents=True, exist_ok=True)

    dest = inbox / filename
    size, sha256 = await _stream_to_file(file, dest)

    uploaded_at = datetime.now(timezone.utc).isoformat()
    _write_manifest(dest, filename, size, sha256, uploaded_at)

    _notify_file_landed(lead, str(dest))

    return {
        "name": filename,
        "size": size,
        "sha256": sha256,
        "uploaded_at": uploaded_at,
        "path": str(dest),
    }
