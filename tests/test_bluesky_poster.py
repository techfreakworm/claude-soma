"""Tests for scripts/bluesky-post.py (AT Protocol poster helper).

Mocks urllib.request.urlopen to verify createSession and app.bsky.feed.post
call shapes without hitting the network.
"""
from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import importlib.util

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "bluesky-post.py"

# bluesky-post.py has a hyphen in the filename; load it via importlib.
_spec = importlib.util.spec_from_file_location("bluesky_post", SCRIPT)
assert _spec is not None and _spec.loader is not None
bspost = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bspost)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_jwt(did: str = "did:plc:testdid123") -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').rstrip(b"=").decode()
    payload_data = json.dumps({"sub": did, "exp": 9999999999}).encode()
    payload = base64.urlsafe_b64encode(payload_data).rstrip(b"=").decode()
    return f"{header}.{payload}.fakesig"


def _mock_response(data: dict, status: int = 200) -> MagicMock:
    body = json.dumps(data).encode()
    mock = MagicMock()
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    mock.read.return_value = body
    mock.status = status
    return mock


# ---------------------------------------------------------------------------
# create_session
# ---------------------------------------------------------------------------

def test_create_session_returns_access_jwt() -> None:
    jwt = _make_jwt()
    mock_resp = _mock_response({"accessJwt": jwt, "did": "did:plc:testdid123"})
    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
        token = bspost.create_session("handle.bsky.social", "test-app-pw")
    assert token == jwt
    req = mock_open.call_args[0][0]
    assert req.full_url.endswith("/com.atproto.server.createSession")
    body = json.loads(req.data)
    assert body["identifier"] == "handle.bsky.social"
    assert body["password"] == "test-app-pw"
    assert req.get_header("Content-type") == "application/json"


def test_create_session_raises_on_missing_jwt() -> None:
    mock_resp = _mock_response({"error": "AuthenticationRequired"})
    with patch("urllib.request.urlopen", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="no accessJwt"):
            bspost.create_session("bad@handle", "wrongpw")


# ---------------------------------------------------------------------------
# make_post
# ---------------------------------------------------------------------------

def test_make_post_basic_text() -> None:
    jwt = _make_jwt("did:plc:abc123")
    post_resp = {"uri": "at://did:plc:abc123/app.bsky.feed.post/xyz", "cid": "bafyreitest"}
    mock_resp = _mock_response(post_resp)

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
        result = bspost.make_post(
            text="Hello Bluesky from AT Protocol.",
            token=jwt,
            reply_uri=None,
            reply_cid=None,
            image_paths=[],
            image_alts=[],
        )

    assert result["uri"] == post_resp["uri"]
    assert result["cid"] == post_resp["cid"]

    req = mock_open.call_args[0][0]
    assert req.full_url.endswith("/com.atproto.repo.createRecord")
    assert req.get_header("Authorization") == f"Bearer {jwt}"

    body = json.loads(req.data)
    assert body["repo"] == "did:plc:abc123"
    assert body["collection"] == "app.bsky.feed.post"
    assert body["record"]["text"] == "Hello Bluesky from AT Protocol."
    assert body["record"]["$type"] == "app.bsky.feed.post"
    assert "createdAt" in body["record"]
    assert "reply" not in body["record"]
    assert "embed" not in body["record"]


def test_make_post_with_reply_ref() -> None:
    jwt = _make_jwt("did:plc:abc123")
    reply_uri = "at://did:plc:abc123/app.bsky.feed.post/parent"
    reply_cid = "bafyreiparen"
    post_resp = {"uri": "at://did:plc:abc123/app.bsky.feed.post/child", "cid": "bafyreichild"}
    mock_resp = _mock_response(post_resp)

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
        result = bspost.make_post(
            text="Reply text.",
            token=jwt,
            reply_uri=reply_uri,
            reply_cid=reply_cid,
            image_paths=[],
            image_alts=[],
        )

    assert result["uri"] == post_resp["uri"]
    req = mock_open.call_args[0][0]
    body = json.loads(req.data)
    record = body["record"]
    assert "reply" in record
    assert record["reply"]["root"]["uri"] == reply_uri
    assert record["reply"]["root"]["cid"] == reply_cid
    assert record["reply"]["parent"]["uri"] == reply_uri
    assert record["reply"]["parent"]["cid"] == reply_cid


def test_make_post_with_images(tmp_path: Path) -> None:
    jwt = _make_jwt("did:plc:imguser")
    img_file = tmp_path / "screenshot.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    blob_resp = {"blob": {"$type": "blob", "ref": {"$link": "bafyrei123"}, "mimeType": "image/png", "size": 108}}
    post_resp = {"uri": "at://did:plc:imguser/app.bsky.feed.post/imgpost", "cid": "bafyreipost"}

    responses = iter([
        _mock_response(blob_resp),
        _mock_response(post_resp),
    ])

    with patch("urllib.request.urlopen", side_effect=lambda req, timeout: next(responses)) as mock_open:
        result = bspost.make_post(
            text="Post with image.",
            token=jwt,
            reply_uri=None,
            reply_cid=None,
            image_paths=[str(img_file)],
            image_alts=["A screenshot of the dashboard"],
        )

    assert result["uri"] == post_resp["uri"]
    assert mock_open.call_count == 2

    upload_req = mock_open.call_args_list[0][0][0]
    assert upload_req.full_url.endswith("/com.atproto.repo.uploadBlob")
    assert upload_req.get_header("Authorization") == f"Bearer {jwt}"

    post_req = mock_open.call_args_list[1][0][0]
    body = json.loads(post_req.data)
    embed = body["record"]["embed"]
    assert embed["$type"] == "app.bsky.embed.images"
    assert len(embed["images"]) == 1
    assert embed["images"][0]["alt"] == "A screenshot of the dashboard"


# ---------------------------------------------------------------------------
# JWT parsing (DID extraction)
# ---------------------------------------------------------------------------

def test_did_from_token_extracts_sub_field() -> None:
    jwt = _make_jwt("did:plc:myspecificdid")
    did = bspost._did_from_token(jwt)
    assert did == "did:plc:myspecificdid"


def test_did_from_token_raises_on_bad_jwt() -> None:
    with pytest.raises(RuntimeError, match="JWT"):
        bspost._did_from_token("notajwt")


# ---------------------------------------------------------------------------
# CLI argument validation (subprocess)
# ---------------------------------------------------------------------------

def test_script_syntax_valid() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"Syntax error: {result.stderr}"


def test_too_many_images_exits_nonzero(tmp_path: Path) -> None:
    creds = tmp_path / "bluesky.json"
    creds.write_text(json.dumps({"identifier": "test.bsky.social", "app_password": "fake"}))
    img = tmp_path / "img.png"
    img.write_bytes(b"PNG")

    args = [
        sys.executable, str(SCRIPT),
        "--creds", str(creds),
        "--text", "too many images",
    ]
    for i in range(5):
        args += ["--image-path", str(img), "--image-alt", f"alt {i}"]

    result = subprocess.run(args, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "4 images" in result.stderr


def test_missing_creds_file_exits_nonzero(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--creds", str(tmp_path / "no-such-file.json"),
            "--text", "hello",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "bluesky-login.sh" in result.stderr


def test_encrypted_creds_exits_nonzero(tmp_path: Path) -> None:
    creds = tmp_path / "bluesky.json"
    creds.write_text(json.dumps({
        "encrypted": True,
        "identifier": "x",
        "app_password": "ciphertext",
    }))
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--creds", str(creds),
            "--text", "hello",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "encrypted" in result.stderr
