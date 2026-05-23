from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from claude_soma.mcp_servers.project_orchestrator.registry import Registry


def test_register_then_list_routine(tmp_path: Path) -> None:
    r = Registry(tmp_path / "reg.sqlite")
    r.register_routine(
        "portfolio-oneliner",
        kind="local",
        schedule="Mon..Fri *-*-* 03:30:00",
        target_skill="portfolio-oneliner",
        description="Weekday portfolio brief at 09:00 IST",
    )
    routines = r.list_routines()
    assert len(routines) == 1
    routine = routines[0]
    assert routine["name"] == "portfolio-oneliner"
    assert routine["kind"] == "local"
    assert routine["schedule"] == "Mon..Fri *-*-* 03:30:00"
    assert routine["target_skill"] == "portfolio-oneliner"
    assert routine["description"] == "Weekday portfolio brief at 09:00 IST"
    assert routine["created_by"] == "bot"
    assert routine["last_run"] is None
    assert routine["next_run"] is None
    assert isinstance(routine["created_at"], float)


def test_get_unknown_routine_returns_none(tmp_path: Path) -> None:
    r = Registry(tmp_path / "reg.sqlite")
    assert r.get_routine("does-not-exist") is None


def test_delete_routine_removes_from_list(tmp_path: Path) -> None:
    r = Registry(tmp_path / "reg.sqlite")
    r.register_routine("alpha", kind="cloud", schedule="0 9 * * *")
    r.register_routine("beta", kind="local", schedule="*-*-* 12:00:00")
    assert {x["name"] for x in r.list_routines()} == {"alpha", "beta"}
    r.delete_routine("alpha")
    remaining = r.list_routines()
    assert {x["name"] for x in remaining} == {"beta"}


def test_update_routine_run_persists_timestamps(tmp_path: Path) -> None:
    r = Registry(tmp_path / "reg.sqlite")
    r.register_routine("nightly", kind="cloud", schedule="0 0 * * *")
    last = time.time() - 3600
    nxt = time.time() + 3600
    r.update_routine_run("nightly", last_run=last, next_run=nxt)
    got = r.get_routine("nightly")
    assert got is not None
    assert got["last_run"] == pytest.approx(last)
    assert got["next_run"] == pytest.approx(nxt)


def test_register_rejects_invalid_kind(tmp_path: Path) -> None:
    r = Registry(tmp_path / "reg.sqlite")
    with pytest.raises((sqlite3.IntegrityError, ValueError)):
        r.register_routine("oops", kind="invalid", schedule="* * * * *")


def test_register_with_metadata_round_trips(tmp_path: Path) -> None:
    r = Registry(tmp_path / "reg.sqlite")
    r.register_routine(
        "with-meta",
        kind="cloud",
        schedule="0 9 * * *",
        metadata={"trigger_id": "trg_xyz", "tz": "Asia/Kolkata"},
    )
    got = r.get_routine("with-meta")
    assert got is not None
    assert got["metadata"] == {"trigger_id": "trg_xyz", "tz": "Asia/Kolkata"}


def test_register_routine_upserts(tmp_path: Path) -> None:
    r = Registry(tmp_path / "reg.sqlite")
    r.register_routine("dup", kind="local", schedule="*-*-* 01:00:00")
    r.register_routine(
        "dup", kind="local", schedule="*-*-* 02:00:00", description="updated"
    )
    routines = r.list_routines()
    assert len(routines) == 1
    assert routines[0]["schedule"] == "*-*-* 02:00:00"
    assert routines[0]["description"] == "updated"
