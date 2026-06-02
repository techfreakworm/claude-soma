import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from claude_soma.api.main import create_app

HEADERS = {"X-GitHub-Handle": "techfreakworm"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_snapshots(
    date TEXT PRIMARY KEY,
    interactive_credits_used REAL DEFAULT 0,
    interactive_ceiling REAL DEFAULT 0,
    agent_sdk_credits_used REAL DEFAULT 0,
    agent_sdk_ceiling REAL DEFAULT 0,
    recorded_at REAL DEFAULT 0
);
"""


def test_usage_returns_buckets_shape() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/usage", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert "interactive" in body and "agent_sdk" in body
    for k in ("today", "ceiling", "remaining_pct", "configured"):
        assert k in body["interactive"]
    assert body["interactive"]["configured"] is False
    assert body["agent_sdk"]["configured"] is False


def test_usage_response_includes_configured_field(tmp_path: Path) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    db = tmp_path / "usage.sqlite"
    conn = sqlite3.connect(str(db))
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO daily_snapshots VALUES (?, ?, ?, ?, ?, ?)",
        (today, 100.0, 0.0, 50.0, 0.0, 0.0),
    )
    conn.commit()
    conn.close()

    app = create_app()
    client = TestClient(app)
    r = client.get("/api/usage", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["interactive"]["configured"] is False
    assert body["agent_sdk"]["configured"] is False


def test_usage_response_configured_true_when_ceiling_nonzero(tmp_path: Path) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    db = tmp_path / "usage.sqlite"
    conn = sqlite3.connect(str(db))
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO daily_snapshots VALUES (?, ?, ?, ?, ?, ?)",
        (today, 100.0, 10000.0, 50.0, 20000.0, 0.0),
    )
    conn.commit()
    conn.close()

    app = create_app()
    client = TestClient(app)
    r = client.get("/api/usage", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["interactive"]["configured"] is True
    assert body["agent_sdk"]["configured"] is True
