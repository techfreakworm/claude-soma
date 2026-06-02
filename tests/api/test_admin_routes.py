from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from claude_soma.api.main import create_app

HEADERS = {"X-GitHub-Handle": "techfreakworm"}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


class _FakeResp:
    status = 200

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *a: object) -> None:
        return None


def test_broadcast_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "testtoken")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _FakeResp())

    r = client.post("/api/admin/broadcast", headers=HEADERS, json={"message": "hi"})
    assert r.status_code == 200
    body = r.json()
    assert body["delivered"] is True
    assert body["error"] is None

    queue = tmp_path / "broadcast.jsonl"
    assert queue.exists()
    row = json.loads(queue.read_text().strip())
    assert row["message"] == "hi"


def test_broadcast_telegram_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "testtoken")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    def _raise(req: object, timeout: object = None) -> None:
        raise urllib.error.URLError("timeout")

    monkeypatch.setattr("urllib.request.urlopen", _raise)

    r = client.post("/api/admin/broadcast", headers=HEADERS, json={"message": "hi"})
    assert r.status_code == 200
    body = r.json()
    assert body["delivered"] is False
    assert body["error"] is not None
    assert len(body["error"]) > 0

    queue = tmp_path / "broadcast.jsonl"
    assert queue.exists()


def test_broadcast_missing_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("HERMES_NOTIFY_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_OPERATOR_CHAT_ID", raising=False)

    r = client.post("/api/admin/broadcast", headers=HEADERS, json={"message": "hi"})
    assert r.status_code == 200
    body = r.json()
    assert body["delivered"] is False
    assert body["error"] is not None
    assert "missing" in body["error"]

    queue = tmp_path / "broadcast.jsonl"
    assert queue.exists()
