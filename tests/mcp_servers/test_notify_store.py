from __future__ import annotations

import time
from pathlib import Path

import pytest

from claude_soma.mcp_servers.hermes_api.notify_store import EventStore


@pytest.fixture()
def store(tmp_path: Path) -> EventStore:
    db = tmp_path / "test_notify_store.sqlite"
    return EventStore(db_path=db)


def test_claim_auto_restart_first_wins(store: EventStore) -> None:
    """First claim_auto_restart sets the column; second call on the same id returns False."""
    eid = store.insert_event(
        lead="l",
        type_="MILESTONE",
        ts=time.time(),
        payload_json='{"progress": "RESTART REQUIRED (services: svc1)"}',
    )

    first = store.claim_auto_restart(eid)
    assert first is True

    row = store.get_event(eid)
    assert row is not None
    assert row["auto_restart_fired_at"] is not None
    assert isinstance(row["auto_restart_fired_at"], float)

    second = store.claim_auto_restart(eid)
    assert second is False

    row2 = store.get_event(eid)
    assert row2 is not None
    assert row2["auto_restart_fired_at"] == row["auto_restart_fired_at"]


def test_claim_auto_restart_unknown_id_returns_false(store: EventStore) -> None:
    result = store.claim_auto_restart(99999)
    assert result is False


def test_auto_restart_fired_at_column_absent_before_claim(store: EventStore) -> None:
    eid = store.insert_event(
        lead="l",
        type_="MILESTONE",
        ts=time.time(),
        payload_json='{"progress": "step 1"}',
    )
    row = store.get_event(eid)
    assert row is not None
    assert row["auto_restart_fired_at"] is None
