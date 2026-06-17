# tests/mcp_servers/test_watchdog.py
from __future__ import annotations

import time
from pathlib import Path

import pytest

from claude_soma.mcp_servers.project_orchestrator import watchdog
from claude_soma.mcp_servers.project_orchestrator.registry import Registry


# ---------------------------------------------------------------------------
# Fixtures / harness
# ---------------------------------------------------------------------------


@pytest.fixture
def reg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Registry:
    """A temp-sqlite Registry that watchdog._reg() also returns.

    watchdog.run_once() builds its own Registry from HERMES_ORCH_DB; point that
    at the same temp file AND patch watchdog._reg to hand back this very
    instance so the test and the code under test share one connection.
    """
    db = tmp_path / "reg.sqlite"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))
    r = Registry(db)
    monkeypatch.setattr(watchdog, "_reg", lambda: r)
    return r


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Patch every side-effecting spawner fn + _notify + the grace sleep.

    Returns a dict recording calls so tests can assert on them. `alive` is a
    list-of-bools queue popped by is_lead_alive; default behaviour is set
    per-test via the helper closures.
    """
    rec: dict = {
        "kill": [],
        "resume": [],
        "spawn": [],
        "notify": [],
        "alive_calls": [],
    }
    # Default: leads are dead. Tests override _alive_for.
    alive_map: dict[str, list[bool]] = {}
    rec["_alive_map"] = alive_map

    def fake_alive(name: str, host: str = "local") -> bool:
        rec["alive_calls"].append(name)
        bare = name
        seq = alive_map.get(bare)
        if seq is None:
            return False
        if len(seq) == 1:
            return seq[0]
        return seq.pop(0)

    def fake_liveness(name: str, host: str = "local") -> str:
        # Map the bool queue to tri-state so the new _process_row primary check
        # (lead_liveness) consumes the same queue as the legacy is_lead_alive flow.
        return "alive" if fake_alive(name, host) else "dead"

    def fake_kill(name: str, host: str = "local") -> None:
        rec["kill"].append(name)

    def fake_resume(**kwargs) -> dict:
        rec["resume"].append(kwargs)
        return {"agent_id": f"soma-proj-{kwargs['name']}", "session_uuid": kwargs["session_uuid"]}

    def fake_spawn(**kwargs) -> dict:
        rec["spawn"].append(kwargs)
        return {
            "agent_id": f"soma-proj-{kwargs['name']}",
            "session_uuid": "fresh-uuid-0001",
            "rc_url": "",
            "cwd": str(kwargs["cwd"]),
        }

    def fake_notify(text: str) -> None:
        rec["notify"].append(text)

    monkeypatch.setattr(watchdog, "is_lead_alive", fake_alive)
    monkeypatch.setattr(watchdog, "lead_liveness", fake_liveness)
    monkeypatch.setattr(watchdog, "kill_session", fake_kill)
    monkeypatch.setattr(watchdog, "resume_background_lead", fake_resume)
    monkeypatch.setattr(watchdog, "spawn_background_lead", fake_spawn)
    monkeypatch.setattr(watchdog, "_notify", fake_notify)
    # Don't actually sleep through the grace polls.
    monkeypatch.setattr(watchdog.time, "sleep", lambda *_: None)
    return rec


def _register(reg: Registry, name: str, *, status: str, session_uuid: str | None = None,
              brief: str = "do the thing", cwd: str = "/home/ubuntu/projects/x") -> None:
    reg.register(name, agent_id=f"soma-proj-{name}", type_="custom", cwd=cwd,
                 rc_url=None, brief=brief)
    if session_uuid:
        reg.set_session_uuid(name, session_uuid)
    if status != "active":
        reg.set_status(name, status, bump_activity=False)


# ---------------------------------------------------------------------------
# list_revivable (registry method)
# ---------------------------------------------------------------------------


def test_list_revivable_excludes_killed(reg: Registry) -> None:
    _register(reg, "a", status="active")
    _register(reg, "b", status="dead")
    _register(reg, "c", status="killed")
    names = {row["name"] for row in reg.list_revivable()}
    assert names == {"a", "b"}


# ---------------------------------------------------------------------------
# run_once behaviours
# ---------------------------------------------------------------------------


def test_alive_but_marked_dead_reconciled(reg: Registry, calls: dict) -> None:
    """A live lead wrongly marked 'dead' is reconciled to 'active', never respawned."""
    _register(reg, "ghost", status="dead", session_uuid="u-1")
    calls["_alive_map"]["ghost"] = [True]

    summary = watchdog.run_once()

    assert reg.get("ghost")["status"] == "active"
    assert summary["reconciled"] == 1
    assert summary["alive"] == 1
    assert summary["revived"] == 0
    assert calls["resume"] == []
    assert calls["spawn"] == []
    assert calls["kill"] == []


def test_alive_and_active_untouched(reg: Registry, calls: dict) -> None:
    """A live, already-active lead needs no reconcile and no respawn."""
    _register(reg, "fine", status="active", session_uuid="u-1")
    calls["_alive_map"]["fine"] = [True]

    summary = watchdog.run_once()

    assert summary["alive"] == 1
    assert summary["reconciled"] == 0
    assert summary["revived"] == 0
    assert calls["resume"] == []
    assert calls["spawn"] == []


def test_dead_with_uuid_resumes_and_reconciles_status(reg: Registry, calls: dict) -> None:
    """Dead + session_uuid -> resume; grace poll shows alive -> status active,
    revive DM sent, failures reset."""
    _register(reg, "crashed", status="dead", session_uuid="resume-me")
    # dead at first liveness check, dead at the pre-kill recheck, alive at grace.
    calls["_alive_map"]["crashed"] = [False, False, True]

    summary = watchdog.run_once()

    assert len(calls["resume"]) == 1
    assert calls["resume"][0]["session_uuid"] == "resume-me"
    assert calls["resume"][0]["name"] == "crashed"
    assert calls["resume"][0]["cwd"] == Path("/home/ubuntu/projects/x")
    assert calls["spawn"] == []
    assert calls["kill"] == ["crashed"]
    assert reg.get("crashed")["status"] == "active"
    assert summary["revived"] == 1
    state = reg.get_watchdog_state("crashed")
    assert state["consecutive_failures"] == 0
    assert state["last_method"] == "resume"
    assert any("revived" in t for t in calls["notify"])


def test_dead_no_uuid_fresh_spawn_persists_uuid(reg: Registry, calls: dict) -> None:
    """Dead with no session_uuid -> fresh spawn; the returned uuid is persisted."""
    _register(reg, "newborn", status="dead", session_uuid=None, brief="BRIEF-VERBATIM")
    calls["_alive_map"]["newborn"] = [False, False, True]

    summary = watchdog.run_once()

    assert calls["resume"] == []
    assert len(calls["spawn"]) == 1
    assert calls["spawn"][0]["brief"] == "BRIEF-VERBATIM"
    assert calls["spawn"][0]["name"] == "newborn"
    assert reg.get_session_uuid("newborn") == "fresh-uuid-0001"
    assert reg.get("newborn")["status"] == "active"
    assert summary["revived"] == 1


def test_resume_raises_falls_back_to_fresh(reg: Registry, calls: dict,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """resume raising RuntimeError (e.g. context guard) -> fresh-spawn fallback,
    fresh uuid persisted."""
    _register(reg, "toobig", status="dead", session_uuid="huge-ctx", brief="RESUME-BRIEF")
    calls["_alive_map"]["toobig"] = [False, False, True]

    def boom(**kwargs):
        raise RuntimeError("context guard: 250000 tokens > 200000")

    monkeypatch.setattr(watchdog, "resume_background_lead", boom)

    summary = watchdog.run_once()

    assert len(calls["spawn"]) == 1
    assert calls["spawn"][0]["brief"] == "RESUME-BRIEF"
    assert reg.get_session_uuid("toobig") == "fresh-uuid-0001"
    assert reg.get("toobig")["status"] == "active"
    assert summary["revived"] == 1
    assert reg.get_watchdog_state("toobig")["last_method"] == "fresh"


def test_repeated_failure_gives_up_once(reg: Registry, calls: dict) -> None:
    """A lead that stays dead after every respawn is given up after max_attempts,
    with exactly ONE give-up DM, then skipped while in cooldown."""
    _register(reg, "doomed", status="dead", session_uuid=None)
    # Always dead: first liveness + grace polls all return False.
    calls["_alive_map"]["doomed"] = [False]

    # max_attempts defaults to 3; run the sweep 3 times -> 3 failures -> give up.
    for _ in range(3):
        watchdog.run_once()

    state = reg.get_watchdog_state("doomed")
    assert state["consecutive_failures"] == 3
    assert state["gaveup_notified"] == 1
    gaveup_dms = [t for t in calls["notify"] if "GAVE UP" in t]
    assert len(gaveup_dms) == 1

    spawn_count_before = len(calls["spawn"])
    # 4th sweep: inside cooldown -> no new spawn, no new DM.
    summary = watchdog.run_once()
    assert len(calls["spawn"]) == spawn_count_before
    assert summary["gaveup"] == 0  # already notified; stays quiet
    assert len([t for t in calls["notify"] if "GAVE UP" in t]) == 1


def test_killed_lead_untouched(reg: Registry, calls: dict) -> None:
    """status 'killed' is intentional -> never revived, never even checked."""
    _register(reg, "stopped", status="killed", session_uuid="u-1")

    summary = watchdog.run_once()

    assert summary["checked"] == 0
    assert calls["resume"] == []
    assert calls["spawn"] == []
    assert calls["kill"] == []
    assert reg.get("stopped")["status"] == "killed"


def test_dry_run_no_mutation_no_dm(reg: Registry, calls: dict,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """dry_run -> no kill/resume/spawn/DM and no status mutation for dead leads."""
    monkeypatch.setenv("HERMES_LEAD_WATCHDOG_DRY_RUN", "1")
    _register(reg, "dead-dry", status="dead", session_uuid="u-1")
    calls["_alive_map"]["dead-dry"] = [False]

    summary = watchdog.run_once()

    assert summary["dry_run"] is True
    assert calls["kill"] == []
    assert calls["resume"] == []
    assert calls["spawn"] == []
    assert calls["notify"] == []
    # status untouched (still dead) and no watchdog state recorded.
    assert reg.get("dead-dry")["status"] == "dead"
    assert reg.get_watchdog_state("dead-dry") is None


def test_dry_run_does_not_self_heal_alive(reg: Registry, calls: dict,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """dry_run leaves a live-but-mismarked lead untouched (no status write)."""
    monkeypatch.setenv("HERMES_LEAD_WATCHDOG_DRY_RUN", "1")
    _register(reg, "ghost-dry", status="dead", session_uuid="u-1")
    calls["_alive_map"]["ghost-dry"] = [True]

    summary = watchdog.run_once()

    assert summary["dry_run"] is True
    assert summary["reconciled"] == 0
    assert reg.get("ghost-dry")["status"] == "dead"  # not self-healed in dry-run


def test_revive_skipped_if_lead_came_back(reg: Registry, calls: dict) -> None:
    """Dead at the first check but alive at the pre-kill recheck (revived by
    another path) -> self-healed, NOT killed/respawned."""
    _register(reg, "racer", status="dead", session_uuid="u-1")
    # dead at the initial check, alive at the TOCTOU recheck.
    calls["_alive_map"]["racer"] = [False, True]

    summary = watchdog.run_once()

    assert calls["kill"] == []
    assert calls["resume"] == []
    assert calls["spawn"] == []
    assert reg.get("racer")["status"] == "active"
    assert summary["reconciled"] == 1
    assert summary["revived"] == 0


def test_disabled_returns_early(reg: Registry, calls: dict,
                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_LEAD_WATCHDOG_DISABLED", "1")
    _register(reg, "whatever", status="dead", session_uuid=None)
    calls["_alive_map"]["whatever"] = [False]

    summary = watchdog.run_once()

    assert summary.get("disabled") is True
    assert summary["checked"] == 0
    assert calls["spawn"] == []
    assert calls["resume"] == []


def test_cooldown_elapsed_resets_and_retries(reg: Registry, calls: dict,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """Once the give-up cooldown lapses, backoff resets and the watchdog retries."""
    monkeypatch.setenv("HERMES_LEAD_WATCHDOG_COOLDOWN_SEC", "1")
    _register(reg, "retry", status="dead", session_uuid=None)
    # First three sweeps: stays dead -> 3 failures, gave up.
    calls["_alive_map"]["retry"] = [False]
    for _ in range(3):
        watchdog.run_once()
    assert reg.get_watchdog_state("retry")["consecutive_failures"] == 3

    # Force the last_attempt_ts into the past so cooldown is exceeded.
    reg.record_revive_attempt("retry", method="fresh", outcome="x", success=False)
    with reg._lock:  # noqa: SLF001 -- test reaches in to age the timestamp
        reg._conn.execute(
            "UPDATE lead_watchdog SET last_attempt_ts = ? WHERE name = ?",
            (time.time() - 10_000, "retry"),
        )

    # Next sweep: cooldown elapsed -> reset_watchdog -> retry (and this time it
    # comes up). Note: the first failed fresh spawn already persisted a uuid, so
    # the retry takes the RESUME path -- that's the intended production
    # behaviour (resume the cloud session rather than start over fresh).
    calls["_alive_map"]["retry"] = [False, False, True]
    resume_before = len(calls["resume"])
    summary = watchdog.run_once()
    assert len(calls["resume"]) == resume_before + 1
    assert summary["revived"] == 1
    assert reg.get_watchdog_state("retry")["consecutive_failures"] == 0


def test_spawn_raises_records_failure(reg: Registry, calls: dict,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """A spawn that raises outright is recorded as a failed attempt (no grace)."""
    _register(reg, "boom", status="dead", session_uuid=None)
    calls["_alive_map"]["boom"] = [False]

    def boom(**kwargs):
        raise RuntimeError("systemd unit already exists")

    monkeypatch.setattr(watchdog, "spawn_background_lead", boom)

    summary = watchdog.run_once()

    assert summary["failed"] == 1
    assert summary["revived"] == 0
    state = reg.get_watchdog_state("boom")
    assert state["consecutive_failures"] == 1
    assert "already exists" in state["last_outcome"]


# ---------------------------------------------------------------------------
# _notify fail-open
# ---------------------------------------------------------------------------


def test_notify_swallows_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """_notify must never raise, even if the HTTP call blows up."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("HERMES_NOTIFY_CHAT_ID", "12345")

    def boom(*_a, **_k):
        raise OSError("network down")

    monkeypatch.setattr(watchdog.urllib.request, "urlopen", boom)
    # Must not raise.
    watchdog._notify("hello")


def test_notify_skips_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """No token/chat -> no HTTP attempt, no raise."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("HERMES_NOTIFY_CHAT_ID", raising=False)
    called = {"hit": False}

    def boom(*_a, **_k):
        called["hit"] = True
        raise AssertionError("should not be called")

    monkeypatch.setattr(watchdog.urllib.request, "urlopen", boom)
    watchdog._notify("hello")
    assert called["hit"] is False
