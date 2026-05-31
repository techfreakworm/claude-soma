from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from claude_soma.api.main import create_app

HEADERS = {"X-GitHub-Handle": "techfreakworm"}


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("HERMES_STAGING_ROOT", str(tmp_path / "staging"))
    monkeypatch.setenv("HERMES_NOTIFY_PORT", "19100")
    return TestClient(create_app())


def _upload(client: TestClient, lead: str, filename: str, data: bytes) -> object:
    return client.post(
        f"/api/admin/upload/{lead}",
        files={"file": (filename, io.BytesIO(data), "application/octet-stream")},
        headers=HEADERS,
    )


class TestAuthGate:
    def test_no_header_returns_403(self, client: TestClient) -> None:
        r = client.post(
            "/api/admin/upload/test-lead",
            files={"file": ("x.bin", io.BytesIO(b"hello"), "application/octet-stream")},
        )
        assert r.status_code == 403

    def test_wrong_handle_returns_403(self, client: TestClient) -> None:
        r = client.post(
            "/api/admin/upload/test-lead",
            files={"file": ("x.bin", io.BytesIO(b"hello"), "application/octet-stream")},
            headers={"X-GitHub-Handle": "attacker"},
        )
        assert r.status_code == 403

    def test_authed_request_succeeds(self, client: TestClient) -> None:
        r = _upload(client, "myproject", "hello.txt", b"hello world")
        assert r.status_code == 200


class TestManifest:
    def test_manifest_fields_correct(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        staging = tmp_path / "staging"
        monkeypatch.setenv("HERMES_STAGING_ROOT", str(staging))
        monkeypatch.setenv("HERMES_NOTIFY_PORT", "19100")
        client = TestClient(create_app())

        payload = b"the quick brown fox"
        r = _upload(client, "test-lead", "fox.txt", payload)

        assert r.status_code == 200
        body = r.json()

        assert body["name"] == "fox.txt"
        assert body["size"] == len(payload)
        expected_sha256 = hashlib.sha256(payload).hexdigest()
        assert body["sha256"] == expected_sha256
        assert "uploaded_at" in body
        assert "T" in body["uploaded_at"]

        uploaded_file = staging / "test-lead" / "inbox" / "fox.txt"
        assert uploaded_file.exists()
        assert uploaded_file.read_bytes() == payload

        manifest_file = staging / "test-lead" / "inbox" / "fox.txt.manifest.json"
        assert manifest_file.exists()
        manifest = json.loads(manifest_file.read_text())
        assert manifest["name"] == "fox.txt"
        assert manifest["size"] == len(payload)
        assert manifest["sha256"] == expected_sha256
        assert "uploaded_at" in manifest

    def test_inbox_directory_created_automatically(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        staging = tmp_path / "staging"
        monkeypatch.setenv("HERMES_STAGING_ROOT", str(staging))
        monkeypatch.setenv("HERMES_NOTIFY_PORT", "19100")
        client = TestClient(create_app())

        _upload(client, "brand-new-lead", "doc.pdf", b"pdf content")

        assert (staging / "brand-new-lead" / "inbox").is_dir()


class TestStreamingOOM:
    """Smoke test: upload a 25 MB file and verify the endpoint handles it
    correctly without loading the whole file into memory at once.

    We can't easily assert no OOM in a unit test, but we verify:
    - the endpoint returns 200
    - the file was written intact (sha256 matches)
    - the manifest was written with correct size
    """

    def test_25mb_upload_lands_intact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        staging = tmp_path / "staging"
        monkeypatch.setenv("HERMES_STAGING_ROOT", str(staging))
        monkeypatch.setenv("HERMES_NOTIFY_PORT", "19100")
        client = TestClient(create_app())

        size = 25 * 1024 * 1024
        payload = b"x" * size
        expected_sha = hashlib.sha256(payload).hexdigest()

        r = _upload(client, "large-lead", "large.bin", payload)

        assert r.status_code == 200
        body = r.json()
        assert body["size"] == size
        assert body["sha256"] == expected_sha

        dest = staging / "large-lead" / "inbox" / "large.bin"
        assert dest.exists()
        assert dest.stat().st_size == size
        assert hashlib.sha256(dest.read_bytes()).hexdigest() == expected_sha

        manifest = json.loads(
            (staging / "large-lead" / "inbox" / "large.bin.manifest.json").read_text()
        )
        assert manifest["size"] == size
        assert manifest["sha256"] == expected_sha


class TestInputValidation:
    def test_invalid_lead_name_rejected(self, client: TestClient) -> None:
        r = client.post(
            "/api/admin/upload/bad..name",
            files={"file": ("x.bin", io.BytesIO(b"x"), "application/octet-stream")},
            headers=HEADERS,
        )
        assert r.status_code in (400, 422)
