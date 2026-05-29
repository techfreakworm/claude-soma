"""Tests for send_tg_reply MCP tool in hermes_api/server.py."""
from __future__ import annotations

import json
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from claude_soma.mcp_servers.hermes_api.server import send_tg_reply, _load_tg_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok_response(message_id: int = 100) -> MagicMock:
    """Return a mock urllib response that looks like a Telegram success."""
    body = json.dumps({"ok": True, "result": {"message_id": message_id}}).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def _error_response(code: int, body: bytes = b'{"ok":false,"description":"Bad Request"}') -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://api.telegram.org/test",
        code=code,
        msg="Bad Request",
        hdrs=MagicMock(),  # type: ignore[arg-type]
        fp=BytesIO(body),
    )


# ---------------------------------------------------------------------------
# Token loading
# ---------------------------------------------------------------------------

def test_load_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token-123")
    assert _load_tg_token() == "env-token-123"


def test_load_token_from_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=file-token-456\n")

    import claude_soma.mcp_servers.hermes_api.server as srv_mod
    original = srv_mod._TG_ENV_FILE
    try:
        srv_mod._TG_ENV_FILE = env_file  # type: ignore[assignment]
        assert _load_tg_token() == "file-token-456"
    finally:
        srv_mod._TG_ENV_FILE = original


def test_load_token_missing_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    import claude_soma.mcp_servers.hermes_api.server as srv_mod
    original = srv_mod._TG_ENV_FILE
    try:
        srv_mod._TG_ENV_FILE = tmp_path / "nonexistent.env"  # type: ignore[assignment]
        with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
            _load_tg_token()
    finally:
        srv_mod._TG_ENV_FILE = original


# ---------------------------------------------------------------------------
# Single-chunk send
# ---------------------------------------------------------------------------

def test_single_chunk_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")

    with patch("urllib.request.urlopen", return_value=_ok_response(101)) as mock_open:
        result = send_tg_reply(chat_id="123", text="Hello **world**")

    assert result["chunks"] == 1
    assert result["files_sent"] == 0
    assert 101 in result["sent_message_ids"]

    mock_open.assert_called_once()
    req = mock_open.call_args[0][0]
    assert "sendMessage" in req.full_url
    body = json.loads(req.data)
    assert body["parse_mode"] == "HTML"
    assert body["chat_id"] == "123"
    assert "<b>world</b>" in body["text"]


# ---------------------------------------------------------------------------
# Multi-chunk send (long text → multiple POSTs)
# ---------------------------------------------------------------------------

def test_multi_chunk_send_calls_multiple_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")

    # Build a text that will produce at least 2 chunks after conversion.
    # Use two paragraphs totalling > 4096 chars.
    long_text = "a" * 2100 + "\n\n" + "b" * 2100

    responses = [_ok_response(i) for i in range(10)]
    call_count = 0

    def _fake_urlopen(req, timeout=None):
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        return resp

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        result = send_tg_reply(chat_id="456", text=long_text)

    assert result["chunks"] >= 2
    assert len(result["sent_message_ids"]) == result["chunks"]


# ---------------------------------------------------------------------------
# reply_to only on first chunk
# ---------------------------------------------------------------------------

def test_reply_to_only_on_first_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")

    long_text = "x" * 2100 + "\n\n" + "y" * 2100
    captured_bodies: list[dict] = []

    def _fake_urlopen(req, timeout=None):
        captured_bodies.append(json.loads(req.data))
        return _ok_response(len(captured_bodies))

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        result = send_tg_reply(chat_id="789", text=long_text, reply_to="42")

    assert result["chunks"] >= 2
    # First chunk must have reply_parameters
    assert "reply_parameters" in captured_bodies[0]
    assert captured_bodies[0]["reply_parameters"]["message_id"] == 42
    # Subsequent chunks must NOT have reply_parameters
    for body in captured_bodies[1:]:
        assert "reply_parameters" not in body


# ---------------------------------------------------------------------------
# File attachment — PNG → sendPhoto
# ---------------------------------------------------------------------------

def test_file_attachment_png_uses_send_photo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    png = tmp_path / "test.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    called_urls: list[str] = []

    def _fake_urlopen(req, timeout=None):
        called_urls.append(req.full_url)
        return _ok_response(len(called_urls))

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        result = send_tg_reply(chat_id="123", text="hi", files=[str(png)])

    assert result["files_sent"] == 1
    assert any("sendPhoto" in url for url in called_urls)
    assert not any("sendDocument" in url for url in called_urls)


# ---------------------------------------------------------------------------
# File attachment — PDF → sendDocument
# ---------------------------------------------------------------------------

def test_file_attachment_pdf_uses_send_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    called_urls: list[str] = []

    def _fake_urlopen(req, timeout=None):
        called_urls.append(req.full_url)
        return _ok_response(len(called_urls))

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        result = send_tg_reply(chat_id="123", text="doc", files=[str(pdf)])

    assert result["files_sent"] == 1
    assert any("sendDocument" in url for url in called_urls)
    assert not any("sendPhoto" in url for url in called_urls)


# ---------------------------------------------------------------------------
# Telegram returns HTTP 400 → RuntimeError with body excerpt
# ---------------------------------------------------------------------------

def test_telegram_400_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")

    err = _error_response(400, b'{"ok":false,"description":"Bad Request: message text is empty"}')

    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(RuntimeError, match="400"):
            send_tg_reply(chat_id="123", text="hello")


# ---------------------------------------------------------------------------
# parse_mode=HTML is always set in the sendMessage payload
# ---------------------------------------------------------------------------

def test_parse_mode_html_always_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")

    with patch("urllib.request.urlopen", return_value=_ok_response()) as mock_open:
        send_tg_reply(chat_id="123", text="plain text no markdown")

    req = mock_open.call_args[0][0]
    body = json.loads(req.data)
    assert body["parse_mode"] == "HTML"
