from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from claude_soma.api.main import create_app

HEADERS = {"X-GitHub-Handle": "techfreakworm"}


@pytest.fixture()
def log_dir(tmp_path: Path) -> Path:
    d = tmp_path / "logs"
    d.mkdir()
    return d


@pytest.fixture()
def client(log_dir: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("HERMES_LEAD_LOG_DIR", str(log_dir))
    return TestClient(create_app())


def _get(client: TestClient, lead: str, **params: object) -> object:
    return client.get(
        f"/api/admin/logs/{lead}",
        params={k: v for k, v in params.items() if v is not None},
        headers=HEADERS,
    )


class TestAuthGate:
    def test_no_header_returns_403(self, client: TestClient) -> None:
        r = client.get("/api/admin/logs/test-lead")
        assert r.status_code == 403

    def test_wrong_handle_returns_403(self, client: TestClient) -> None:
        r = client.get(
            "/api/admin/logs/test-lead",
            headers={"X-GitHub-Handle": "attacker"},
        )
        assert r.status_code == 403

    def test_authed_request_succeeds(self, client: TestClient, log_dir: Path) -> None:
        (log_dir / "test-lead.log").write_text("hello\n")
        r = _get(client, "test-lead")
        assert r.status_code == 200


class TestFileMissing:
    def test_nonexistent_log_returns_empty(self, client: TestClient) -> None:
        r = _get(client, "no-such-lead")
        assert r.status_code == 200
        body = r.json()
        assert body["lines"] == []
        assert body["total_bytes"] == 0
        assert body["has_more"] is False
        assert body["start_byte"] == 0


class TestAnsiStrip:
    def test_sgr_codes_stripped(self, client: TestClient, log_dir: Path) -> None:
        (log_dir / "mybot.log").write_text(
            "\x1b[32mGreen text\x1b[0m\n"
            "\x1b[1;31mBold red\x1b[0m\n"
            "plain line\n"
        )
        r = _get(client, "mybot")
        assert r.status_code == 200
        lines = r.json()["lines"]
        assert "Green text" in lines
        assert "Bold red" in lines
        assert "plain line" in lines
        for line in lines:
            assert "\x1b" not in line

    def test_cursor_movement_codes_stripped(
        self, client: TestClient, log_dir: Path
    ) -> None:
        (log_dir / "cursor.log").write_text("\x1b[2J\x1b[Hclear screen\n")
        r = _get(client, "cursor")
        lines = r.json()["lines"]
        assert lines == ["clear screen"]


class TestPathTraversal:
    def test_dotdot_in_lead_name_rejected(self, client: TestClient) -> None:
        r = _get(client, "../etc/passwd")
        assert r.status_code in (400, 404, 422)

    def test_double_dot_segment_rejected(self, client: TestClient) -> None:
        r = client.get("/api/admin/logs/..%2Fetc%2Fpasswd", headers=HEADERS)
        assert r.status_code in (400, 404, 422)

    def test_simple_dotdot_name_rejected(self, client: TestClient) -> None:
        r = _get(client, "bad..name")
        assert r.status_code == 400

    def test_leading_slash_rejected(self, client: TestClient) -> None:
        r = client.get("/api/admin/logs/%2Fetc%2Fpasswd", headers=HEADERS)
        assert r.status_code in (400, 404, 422)


class TestPagination:
    def test_total_bytes_matches_file_size(
        self, client: TestClient, log_dir: Path
    ) -> None:
        content = "line one\nline two\nline three\n"
        (log_dir / "pagtest.log").write_bytes(content.encode())
        r = _get(client, "pagtest")
        body = r.json()
        assert body["total_bytes"] == len(content.encode())

    def test_tail_returns_last_lines(
        self, client: TestClient, log_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HERMES_LEAD_LOG_DIR", str(log_dir))
        lines = [f"line {i}" for i in range(2000)]
        (log_dir / "big.log").write_text("\n".join(lines) + "\n")

        r = _get(client, "big", limit=100)
        body = r.json()
        returned = body["lines"]
        assert len(returned) == 100
        assert returned[-1] == "line 1999"

    def test_has_more_false_for_small_file(
        self, client: TestClient, log_dir: Path
    ) -> None:
        (log_dir / "small.log").write_text("a\nb\nc\n")
        r = _get(client, "small")
        body = r.json()
        assert body["has_more"] is False

    def test_has_more_true_for_large_file(
        self, client: TestClient, log_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HERMES_LEAD_LOG_DIR", str(log_dir))
        content = ("x" * 100 + "\n") * 1000
        (log_dir / "large.log").write_text(content)
        r = _get(client, "large")
        body = r.json()
        assert body["has_more"] is True
        assert body["start_byte"] > 0

    def test_offset_mode_reads_from_given_position(
        self, client: TestClient, log_dir: Path
    ) -> None:
        (log_dir / "offset.log").write_text("line A\nline B\nline C\n")
        r = _get(client, "offset", offset=0, limit=2)
        body = r.json()
        assert "line A" in body["lines"]
        assert "line B" in body["lines"]

    def test_start_byte_returned_in_response(
        self, client: TestClient, log_dir: Path
    ) -> None:
        (log_dir / "sb.log").write_text("hello\nworld\n")
        r = _get(client, "sb", offset=0)
        body = r.json()
        assert body["start_byte"] == 0

    def test_empty_file_returns_empty(
        self, client: TestClient, log_dir: Path
    ) -> None:
        (log_dir / "empty.log").write_text("")
        r = _get(client, "empty")
        body = r.json()
        assert body["lines"] == []
        assert body["total_bytes"] == 0
