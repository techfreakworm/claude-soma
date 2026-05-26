from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from claude_soma.api.main import create_app
from claude_soma.api.routes import routines as routines_route


HEADERS = {"X-GitHub-Handle": "techfreakworm"}


@pytest.fixture(autouse=True)
def _isolate_routines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The cloud result is cached module-side; clear it so cases don't leak.
    routines_route._clear_routines_cache()
    # Deterministic cron sources -- empty unless a case points them at fixtures.
    monkeypatch.setattr(routines_route, "ETC_CRONTAB", str(tmp_path / "nocrontab"))
    monkeypatch.setattr(routines_route, "CRON_D_DIR", str(tmp_path / "nocron.d"))
    # Never spawn a real `claude -p` in tests; cases that exercise cloud routines
    # override this. (Without it, the smoke test below would shell out for ~12s.)
    monkeypatch.setattr(
        routines_route, "_call_claude_routines", lambda *a, **k: {"triggers": []}
    )


def test_list_routines_returns_list() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/routines", headers=HEADERS)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def _systemd_json_payload() -> str:
    return json.dumps(
        [
            {
                "next": 1748073600000000,
                "left": "1h",
                "last": 1748060000000000,
                "passed": "3min ago",
                "unit": "claude-soma-portfolio-oneliner.timer",
                "activates": "claude-soma-portfolio-oneliner.service",
            },
            {
                "next": 1748080000000000,
                "left": "2h",
                "last": 0,
                "passed": "n/a",
                "unit": "claude-soma-cache-refresh.timer",
                "activates": "claude-soma-cache-refresh.service",
            },
        ]
    )


def _systemctl_show_payload(unit: str) -> str:
    if "portfolio" in unit:
        return "OnCalendar=Mon..Fri *-*-* 03:30:00\nResult=success\n"
    if "cache" in unit:
        return "OnCalendar=*-*-* *:00:00\nResult=success\n"
    return "OnCalendar=\nResult=success\n"


def _fake_subprocess_run(*, raise_for_show: bool = False) -> Any:
    def runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        argv = " ".join(cmd)
        if "list-timers" in argv:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=_systemd_json_payload(), stderr=""
            )
        if cmd[:2] == ["systemctl", "show"]:
            if raise_for_show:
                raise subprocess.CalledProcessError(1, cmd, output="", stderr="boom")
            unit = cmd[-1]
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=_systemctl_show_payload(unit), stderr=""
            )
        if cmd[:1] == ["crontab"]:  # the route now also scans the user crontab
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected subprocess call: {cmd!r}")
    return runner


def test_list_routines_merges_registry_and_systemd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "reg.sqlite"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))

    from claude_soma.mcp_servers.project_orchestrator.registry import Registry

    reg = Registry(db)
    reg.register_routine(
        "portfolio-oneliner",
        kind="local",
        schedule="Mon..Fri *-*-* 03:30:00",
        target_skill="portfolio-oneliner",
        description="Weekday brief",
    )
    reg.register_routine(
        "daily-tldr",
        kind="cloud",
        schedule="0 9 * * *",
        target_skill="tldr",
        description="Cloud routine summary",
    )
    reg.close()

    monkeypatch.setattr(
        routines_route.subprocess, "run", _fake_subprocess_run(raise_for_show=False)
    )

    def fake_cloud(action: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "triggers": [
                {
                    "name": "daily-tldr",
                    "schedule": "0 9 * * *",
                    "next_run": 1748100000.0,
                    "last_run": 1748000000.0,
                },
                {
                    "name": "weekly-report",
                    "schedule": "0 9 * * MON",
                    "next_run": 1748200000.0,
                    "last_run": None,
                },
            ]
        }

    monkeypatch.setattr(routines_route, "_call_claude_routines", fake_cloud)

    app = create_app()
    client = TestClient(app)
    r = client.get("/api/routines", headers=HEADERS)
    assert r.status_code == 200
    payload = r.json()
    assert isinstance(payload, list)
    by_name = {x["name"]: x for x in payload}

    assert "portfolio-oneliner" in by_name
    p = by_name["portfolio-oneliner"]
    assert p["kind"] == "local"
    assert p["created_by"] == "bot"
    assert p["next_run"] is not None
    assert p["last_run"] is not None
    assert p["target_skill"] == "portfolio-oneliner"

    assert "daily-tldr" in by_name
    d = by_name["daily-tldr"]
    assert d["kind"] == "cloud"
    assert d["created_by"] == "bot"
    assert d["next_run"] == pytest.approx(1748100000.0)
    assert d["last_run"] == pytest.approx(1748000000.0)
    assert d["target_skill"] == "tldr"

    assert "weekly-report" in by_name
    w = by_name["weekly-report"]
    assert w["kind"] == "cloud"
    assert w["created_by"] == "cloud"
    assert w["schedule"] == "0 9 * * MON"

    assert "claude-soma-cache-refresh.timer" in by_name
    c = by_name["claude-soma-cache-refresh.timer"]
    assert c["kind"] == "local"
    assert c["created_by"] == "system"


def test_list_routines_handles_cloud_failure_gracefully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "reg.sqlite"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))

    from claude_soma.mcp_servers.project_orchestrator.registry import Registry

    reg = Registry(db)
    reg.register_routine(
        "portfolio-oneliner",
        kind="local",
        schedule="Mon..Fri *-*-* 03:30:00",
        target_skill="portfolio-oneliner",
    )
    reg.close()

    monkeypatch.setattr(
        routines_route.subprocess, "run", _fake_subprocess_run(raise_for_show=False)
    )

    def fake_cloud_fail(action: str, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("claude -p blew up")

    monkeypatch.setattr(routines_route, "_call_claude_routines", fake_cloud_fail)

    app = create_app()
    client = TestClient(app)
    r = client.get("/api/routines", headers=HEADERS)
    assert r.status_code == 200
    payload = r.json()
    by_name = {x["name"]: x for x in payload}
    assert "portfolio-oneliner" in by_name
    assert "claude-soma-cache-refresh.timer" in by_name


def test_list_routines_handles_systemd_failure_gracefully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "reg.sqlite"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))

    from claude_soma.mcp_servers.project_orchestrator.registry import Registry

    reg = Registry(db)
    reg.register_routine(
        "daily-tldr",
        kind="cloud",
        schedule="0 9 * * *",
        target_skill="tldr",
    )
    reg.close()

    def boom(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("systemctl not installed")

    monkeypatch.setattr(routines_route.subprocess, "run", boom)

    def fake_cloud(action: str, **kwargs: Any) -> dict[str, Any]:
        return {"triggers": [{"name": "daily-tldr", "schedule": "0 9 * * *"}]}

    monkeypatch.setattr(routines_route, "_call_claude_routines", fake_cloud)

    app = create_app()
    client = TestClient(app)
    r = client.get("/api/routines", headers=HEADERS)
    assert r.status_code == 200
    payload = r.json()
    by_name = {x["name"]: x for x in payload}
    assert "daily-tldr" in by_name


def test_list_routines_includes_cron_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cron jobs from the user crontab, /etc/crontab, and /etc/cron.d must be
    aggregated and labelled created_by='cron', kind='local'."""
    db = tmp_path / "reg.sqlite"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))

    etc_crontab = tmp_path / "crontab"
    etc_crontab.write_text(
        "# /etc/crontab\nSHELL=/bin/sh\nPATH=/usr/bin\n"
        "17 *\t* * *\troot\tcd / && run-parts --report /etc/cron.hourly\n"
    )
    crond = tmp_path / "cron.d"
    crond.mkdir()
    (crond / "sysstat").write_text(
        "# sysstat\n5 10 * * *\troot\t/usr/lib/sysstat/debian-sa1 1 1\n"
    )
    monkeypatch.setattr(routines_route, "ETC_CRONTAB", str(etc_crontab))
    monkeypatch.setattr(routines_route, "CRON_D_DIR", str(crond))

    def runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        argv = " ".join(cmd)
        if cmd[:1] == ["crontab"]:  # user crontab with a macro schedule
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout="@daily /home/ubuntu/backup.sh\n", stderr="",
            )
        if "list-timers" in argv:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="[]", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(routines_route.subprocess, "run", runner)

    app = create_app()
    client = TestClient(app)
    rows = client.get("/api/routines", headers=HEADERS).json()
    crons = [x for x in rows if x.get("created_by") == "cron"]
    schedules = {c["schedule"] for c in crons}
    assert "17 * * * *" in schedules     # /etc/crontab (user field stripped)
    assert "5 10 * * *" in schedules     # /etc/cron.d/sysstat
    assert "@daily" in schedules         # user crontab macro
    assert all(c["kind"] == "local" for c in crons)


def test_list_routines_includes_non_claude_soma_timers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Timer aggregation must surface ALL systemd timers, not only claude-soma
    ones (the user wants every local schedule visible)."""
    db = tmp_path / "reg.sqlite"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))

    def runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        argv = " ".join(cmd)
        if "list-timers" in argv:
            payload = json.dumps([
                {"unit": "fstrim.timer", "next": 1748073600000000, "last": 0},
                {"unit": "claude-soma-healthcheck.timer", "next": 1748073600000000, "last": 0},
            ])
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=payload, stderr="")
        if cmd[:2] == ["systemctl", "show"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="OnCalendar=daily\nResult=success\n", stderr=""
            )
        if cmd[:1] == ["crontab"]:
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(routines_route.subprocess, "run", runner)

    app = create_app()
    client = TestClient(app)
    names = {x["name"] for x in client.get("/api/routines", headers=HEADERS).json()}
    assert "fstrim.timer" in names                  # broadened beyond claude-soma
    assert "claude-soma-healthcheck.timer" in names


def test_cloud_query_is_cached_across_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The slow cloud query (claude -p) must be cached: a second /api/routines
    request within the TTL must not re-invoke it."""
    db = tmp_path / "reg.sqlite"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))
    monkeypatch.setattr(routines_route.subprocess, "run", _fake_subprocess_run())

    calls = {"n": 0}

    def counting_cloud(action: str, **kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        return {"triggers": [{"name": "c1", "schedule": "0 9 * * *"}]}

    monkeypatch.setattr(routines_route, "_call_claude_routines", counting_cloud)

    app = create_app()
    client = TestClient(app)
    assert client.get("/api/routines", headers=HEADERS).status_code == 200
    assert client.get("/api/routines", headers=HEADERS).status_code == 200
    assert calls["n"] == 1  # second request served from the cloud cache
