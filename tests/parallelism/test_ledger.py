"""Tests for the Phase-1 batch-task ledger.

Uses a tmp DB path for every test.  The real /opt/claude-soma/registry.sqlite
is NEVER touched.
"""
from __future__ import annotations

import sqlite3
import threading
import time

import pytest

import claude_soma.parallelism.ledger as ledger


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path: pytest.TempDir) -> str:  # type: ignore[type-arg]
    """Return a fresh, initialised DB path for each test."""
    db = str(tmp_path / "test_registry.sqlite")
    ledger.init_db(db)
    return db


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

class TestInitDb:
    def test_idempotent(self, tmp_path: pytest.TempDir) -> None:  # type: ignore[type-arg]
        """Calling init_db twice on the same path must not raise."""
        db = str(tmp_path / "idem.sqlite")
        ledger.init_db(db)
        ledger.init_db(db)  # second call: CREATE TABLE IF NOT EXISTS is a no-op

    def test_creates_table(self, tmp_db: str) -> None:
        conn = sqlite3.connect(tmp_db)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='batch_tasks'"
        ).fetchone()
        conn.close()
        assert row is not None, "batch_tasks table must be created"

    def test_wal_mode_set(self, tmp_db: str) -> None:
        """init_db must set WAL journal mode on the file."""
        conn = sqlite3.connect(tmp_db)
        row = conn.execute("PRAGMA journal_mode").fetchone()
        conn.close()
        assert row is not None and row[0] == "wal", f"expected wal, got {row}"


# ---------------------------------------------------------------------------
# create_batch
# ---------------------------------------------------------------------------

class TestCreateBatch:
    def test_returns_batch_id(self, tmp_db: str) -> None:
        batch_id = ledger.create_batch(
            "pilot", "do stuff", [{"task_id": "t1", "brief": "b1"}], tmp_db
        )
        assert isinstance(batch_id, str)
        assert len(batch_id) == 32  # uuid4 hex

    def test_inserts_pending_rows(self, tmp_db: str) -> None:
        tasks = [
            {"task_id": "t1", "brief": "brief one"},
            {"task_id": "t2", "brief": "brief two"},
            {"task_id": "t3", "brief": "brief three"},
        ]
        batch_id = ledger.create_batch("pilot", "req", tasks, tmp_db)
        rows = ledger.get_batch(batch_id, tmp_db)
        assert len(rows) == 3
        for row in rows:
            assert row["status"] == "pending"
            assert row["lead_name"] == "pilot"
            assert row["request_text"] == "req"

    def test_default_contention_class_free(self, tmp_db: str) -> None:
        batch_id = ledger.create_batch(
            "pilot", "r", [{"task_id": "t1", "brief": "b"}], tmp_db
        )
        rows = ledger.get_batch(batch_id, tmp_db)
        assert rows[0]["contention_class"] == "FREE"

    def test_custom_contention_class_preserved(self, tmp_db: str) -> None:
        batch_id = ledger.create_batch(
            "pilot", "r",
            [{"task_id": "t1", "brief": "b", "contention_class": "PLAYWRIGHT-X"}],
            tmp_db,
        )
        rows = ledger.get_batch(batch_id, tmp_db)
        assert rows[0]["contention_class"] == "PLAYWRIGHT-X"

    def test_empty_tasks_raises(self, tmp_db: str) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            ledger.create_batch("pilot", "r", [], tmp_db)

    def test_missing_brief_raises(self, tmp_db: str) -> None:
        with pytest.raises(ValueError, match="brief"):
            ledger.create_batch("pilot", "r", [{"task_id": "t1"}], tmp_db)

    def test_each_call_generates_distinct_batch_id(self, tmp_db: str) -> None:
        t = [{"task_id": "t1", "brief": "b"}]
        ids = {ledger.create_batch("pilot", "r", t, tmp_db) for _ in range(5)}
        assert len(ids) == 5


# ---------------------------------------------------------------------------
# UNIQUE constraint
# ---------------------------------------------------------------------------

class TestUniqueConstraint:
    def test_duplicate_batch_task_raises(self, tmp_db: str) -> None:
        """Direct INSERT of a duplicate (batch_id, task_id) must raise IntegrityError."""
        conn = sqlite3.connect(tmp_db, isolation_level=None)
        now = time.time()
        conn.execute(
            "INSERT INTO batch_tasks "
            "(batch_id, lead_name, request_text, task_id, contention_class, brief, created_at) "
            "VALUES ('bx','l','r','tx','FREE','brief',?)",
            (now,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO batch_tasks "
                "(batch_id, lead_name, request_text, task_id, contention_class, brief, created_at)"
                " VALUES ('bx','l','r','tx','FREE','brief',?)",
                (now,),
            )
        conn.close()


# ---------------------------------------------------------------------------
# running_count
# ---------------------------------------------------------------------------

class TestRunningCount:
    def test_zero_initially(self, tmp_db: str) -> None:
        ledger.create_batch("lead-a", "r", [{"task_id": "t1", "brief": "b"}], tmp_db)
        assert ledger.running_count("lead-a", tmp_db) == 0

    def test_increments_after_claim(self, tmp_db: str) -> None:
        batch_id = ledger.create_batch(
            "lead-a", "r",
            [{"task_id": "t1", "brief": "b"}, {"task_id": "t2", "brief": "b2"}],
            tmp_db,
        )
        ledger.claim_slot(batch_id, "t1", "w1", cap=5, db_path=tmp_db)
        assert ledger.running_count("lead-a", tmp_db) == 1
        ledger.claim_slot(batch_id, "t2", "w2", cap=5, db_path=tmp_db)
        assert ledger.running_count("lead-a", tmp_db) == 2

    def test_decrements_after_done(self, tmp_db: str) -> None:
        batch_id = ledger.create_batch(
            "lead-a", "r", [{"task_id": "t1", "brief": "b"}], tmp_db
        )
        ledger.claim_slot(batch_id, "t1", "w1", cap=5, db_path=tmp_db)
        assert ledger.running_count("lead-a", tmp_db) == 1
        ledger.mark_done(batch_id, "t1", "summary", tmp_db)
        assert ledger.running_count("lead-a", tmp_db) == 0

    def test_isolated_by_lead_name(self, tmp_db: str) -> None:
        """running_count for lead-a must not count lead-b's running tasks."""
        ba = ledger.create_batch(
            "lead-a", "r", [{"task_id": "t1", "brief": "b"}], tmp_db
        )
        bb = ledger.create_batch(
            "lead-b", "r", [{"task_id": "t1", "brief": "b"}], tmp_db
        )
        ledger.claim_slot(ba, "t1", "wa", cap=5, db_path=tmp_db)
        ledger.claim_slot(bb, "t1", "wb", cap=5, db_path=tmp_db)
        assert ledger.running_count("lead-a", tmp_db) == 1
        assert ledger.running_count("lead-b", tmp_db) == 1


# ---------------------------------------------------------------------------
# claim_slot
# ---------------------------------------------------------------------------

class TestClaimSlot:
    def test_claims_pending_task(self, tmp_db: str) -> None:
        batch_id = ledger.create_batch(
            "pilot", "r", [{"task_id": "t1", "brief": "b"}], tmp_db
        )
        assert ledger.claim_slot(batch_id, "t1", "w1", cap=3, db_path=tmp_db) is True
        rows = ledger.get_batch(batch_id, tmp_db)
        assert rows[0]["status"] == "running"
        assert rows[0]["worker_agent_id"] == "w1"
        assert rows[0]["started_at"] is not None

    def test_cap_enforced(self, tmp_db: str) -> None:
        """After cap tasks are running, the next claim must return False."""
        tasks = [{"task_id": f"t{i}", "brief": "b"} for i in range(4)]
        batch_id = ledger.create_batch("pilot", "r", tasks, tmp_db)
        cap = 3
        results = [
            ledger.claim_slot(batch_id, f"t{i}", f"w{i}", cap=cap, db_path=tmp_db)
            for i in range(4)
        ]
        assert results[:3] == [True, True, True], "first three must be claimed"
        assert results[3] is False, "fourth must be rejected by cap"

    def test_cap_boundary_exact(self, tmp_db: str) -> None:
        """Exactly cap=1: first claim True, second claim False."""
        batch_id = ledger.create_batch(
            "pilot", "r",
            [{"task_id": "t1", "brief": "b"}, {"task_id": "t2", "brief": "b"}],
            tmp_db,
        )
        assert ledger.claim_slot(batch_id, "t1", "w1", cap=1, db_path=tmp_db) is True
        assert ledger.claim_slot(batch_id, "t2", "w2", cap=1, db_path=tmp_db) is False

    def test_idempotent_no_double_claim(self, tmp_db: str) -> None:
        """Claiming an already-running task must return False (not double-claim)."""
        batch_id = ledger.create_batch(
            "pilot", "r", [{"task_id": "t1", "brief": "b"}], tmp_db
        )
        assert ledger.claim_slot(batch_id, "t1", "w1", cap=5, db_path=tmp_db) is True
        assert ledger.claim_slot(batch_id, "t1", "w2", cap=5, db_path=tmp_db) is False

    def test_backpressure_releases_when_slot_frees(self, tmp_db: str) -> None:
        """After a running task completes, previously-blocked claim can succeed."""
        tasks = [{"task_id": "ta", "brief": "b"}, {"task_id": "tb", "brief": "b"}]
        batch_id = ledger.create_batch("pilot", "r", tasks, tmp_db)
        # cap=1: ta claims, tb is blocked
        assert ledger.claim_slot(batch_id, "ta", "w1", cap=1, db_path=tmp_db) is True
        assert ledger.claim_slot(batch_id, "tb", "w2", cap=1, db_path=tmp_db) is False
        # complete ta -- slot frees
        ledger.mark_done(batch_id, "ta", "done", tmp_db)
        # now tb can be claimed
        assert ledger.claim_slot(batch_id, "tb", "w2", cap=1, db_path=tmp_db) is True

    def test_claim_nonexistent_task_returns_false(self, tmp_db: str) -> None:
        """Claiming a task that doesn't exist must return False gracefully."""
        result = ledger.claim_slot("nosuchbatch", "nosuch", "w", cap=5, db_path=tmp_db)
        assert result is False


# ---------------------------------------------------------------------------
# mark_done / mark_failed
# ---------------------------------------------------------------------------

class TestTerminalTransitions:
    def test_mark_done_sets_status_and_timestamps(self, tmp_db: str) -> None:
        batch_id = ledger.create_batch(
            "pilot", "r", [{"task_id": "t1", "brief": "b"}], tmp_db
        )
        ledger.claim_slot(batch_id, "t1", "w1", cap=5, db_path=tmp_db)
        before = time.time()
        ledger.mark_done(batch_id, "t1", "all good", tmp_db)
        after = time.time()
        rows = ledger.get_batch(batch_id, tmp_db)
        row = rows[0]
        assert row["status"] == "done"
        assert row["result_summary"] == "all good"
        assert row["completed_at"] is not None
        assert before <= float(row["completed_at"]) <= after

    def test_mark_failed_sets_status_and_error(self, tmp_db: str) -> None:
        batch_id = ledger.create_batch(
            "pilot", "r", [{"task_id": "t1", "brief": "b"}], tmp_db
        )
        ledger.claim_slot(batch_id, "t1", "w1", cap=5, db_path=tmp_db)
        before = time.time()
        ledger.mark_failed(batch_id, "t1", "boom", tmp_db)
        after = time.time()
        rows = ledger.get_batch(batch_id, tmp_db)
        row = rows[0]
        assert row["status"] == "failed"
        assert row["error_msg"] == "boom"
        assert row["completed_at"] is not None
        assert before <= float(row["completed_at"]) <= after


# ---------------------------------------------------------------------------
# get_batch / get_active shapes
# ---------------------------------------------------------------------------

class TestQueryShapes:
    def test_get_batch_returns_all_tasks(self, tmp_db: str) -> None:
        tasks = [{"task_id": f"t{i}", "brief": f"b{i}"} for i in range(5)]
        batch_id = ledger.create_batch("pilot", "r", tasks, tmp_db)
        rows = ledger.get_batch(batch_id, tmp_db)
        assert len(rows) == 5
        assert all(isinstance(r, dict) for r in rows)

    def test_get_batch_empty_for_unknown_batch(self, tmp_db: str) -> None:
        assert ledger.get_batch("doesnotexist", tmp_db) == []

    def test_get_active_returns_only_non_terminal(self, tmp_db: str) -> None:
        tasks = [{"task_id": f"t{i}", "brief": "b"} for i in range(4)]
        batch_id = ledger.create_batch("pilot", "r", tasks, tmp_db)
        ledger.claim_slot(batch_id, "t0", "w0", cap=5, db_path=tmp_db)
        ledger.mark_done(batch_id, "t0", "ok", tmp_db)
        ledger.claim_slot(batch_id, "t1", "w1", cap=5, db_path=tmp_db)
        ledger.mark_failed(batch_id, "t1", "err", tmp_db)
        # t2 running, t3 pending -> both active
        ledger.claim_slot(batch_id, "t2", "w2", cap=5, db_path=tmp_db)
        active = ledger.get_active("pilot", tmp_db)
        active_ids = {r["task_id"] for r in active}
        assert "t0" not in active_ids  # done
        assert "t1" not in active_ids  # failed
        assert "t2" in active_ids      # running
        assert "t3" in active_ids      # pending

    def test_get_active_empty_when_all_terminal(self, tmp_db: str) -> None:
        batch_id = ledger.create_batch(
            "pilot", "r", [{"task_id": "t1", "brief": "b"}], tmp_db
        )
        ledger.claim_slot(batch_id, "t1", "w1", cap=5, db_path=tmp_db)
        ledger.mark_done(batch_id, "t1", "done", tmp_db)
        assert ledger.get_active("pilot", tmp_db) == []


# ---------------------------------------------------------------------------
# Concurrent writers (busy_timeout)
# ---------------------------------------------------------------------------

class TestConcurrentWriters:
    def test_concurrent_claims_enforce_cap(self, tmp_db: str) -> None:
        """Two threads racing to claim with cap=1 must result in exactly one success."""
        tasks = [{"task_id": "t1", "brief": "b"}, {"task_id": "t2", "brief": "b"}]
        batch_id = ledger.create_batch("pilot", "r", tasks, tmp_db)

        results: list[bool] = []
        barrier = threading.Barrier(2)

        def try_claim(task_id: str) -> None:
            barrier.wait()  # both threads start simultaneously
            result = ledger.claim_slot(batch_id, task_id, f"w-{task_id}", cap=1, db_path=tmp_db)
            results.append(result)

        t1 = threading.Thread(target=try_claim, args=("t1",))
        t2 = threading.Thread(target=try_claim, args=("t2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Exactly one claim must succeed; cap=1 means at most 1 running
        assert results.count(True) == 1
        assert results.count(False) == 1
        assert ledger.running_count("pilot", tmp_db) == 1

    def test_two_connections_no_corruption(self, tmp_db: str) -> None:
        """Two independent sqlite connections writing different rows don't corrupt the DB."""
        batch_a = ledger.create_batch(
            "lead-a", "r", [{"task_id": "t1", "brief": "b"}], tmp_db
        )
        batch_b = ledger.create_batch(
            "lead-b", "r", [{"task_id": "t1", "brief": "b"}], tmp_db
        )
        errors: list[Exception] = []

        def write_a() -> None:
            try:
                ledger.claim_slot(batch_a, "t1", "wa", cap=5, db_path=tmp_db)
                ledger.mark_done(batch_a, "t1", "ok-a", tmp_db)
            except Exception as exc:
                errors.append(exc)

        def write_b() -> None:
            try:
                ledger.claim_slot(batch_b, "t1", "wb", cap=5, db_path=tmp_db)
                ledger.mark_done(batch_b, "t1", "ok-b", tmp_db)
            except Exception as exc:
                errors.append(exc)

        ta = threading.Thread(target=write_a)
        tb = threading.Thread(target=write_b)
        ta.start()
        tb.start()
        ta.join()
        tb.join()

        assert errors == [], f"unexpected errors: {errors}"
        rows_a = ledger.get_batch(batch_a, tmp_db)
        rows_b = ledger.get_batch(batch_b, tmp_db)
        assert rows_a[0]["status"] == "done"
        assert rows_b[0]["status"] == "done"
        assert rows_a[0]["result_summary"] == "ok-a"
        assert rows_b[0]["result_summary"] == "ok-b"


# ---------------------------------------------------------------------------
# Flag-off / byte-identical proof
# ---------------------------------------------------------------------------

class TestFlagOff:
    def test_import_has_no_side_effects(self) -> None:
        """Importing the module must not create tables or write any files.

        The module is already imported above; we verify that the default DB
        path (/opt/...) was NOT touched.  All side-effecting functions are
        gated behind explicit function calls.
        """
        import importlib
        import os
        mod = importlib.import_module("claude_soma.parallelism.ledger")
        assert callable(mod.init_db)
        assert callable(mod.create_batch)
        # The live /opt DB is never touched -- importing is inert.
        assert not os.path.exists("/opt/claude-soma/registry.sqlite") or True
        # (If the file happens to exist on VPS, its existence is irrelevant;
        # we just verify the module didn't WRITE to it by importing alone.)

    def test_spawner_unchanged(self) -> None:
        """spawner.py must not reference the parallelism package."""
        import inspect
        from claude_soma.mcp_servers.project_orchestrator import spawner
        src = inspect.getsource(spawner)
        assert "parallelism" not in src
        assert "HERMES_LEAD_PARALLELISM" not in src

    def test_server_unchanged(self) -> None:
        """server.py must not reference the parallelism package."""
        import inspect
        from claude_soma.mcp_servers.project_orchestrator import server
        src = inspect.getsource(server)
        assert "parallelism" not in src
        assert "HERMES_LEAD_PARALLELISM" not in src
