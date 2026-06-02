from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from claude_soma.api.main import create_app
from claude_soma.api.routes import routines as routines_route


HEADERS = {"X-GitHub-Handle": "techfreakworm"}

@pytest.fixture(autouse=True)
def _isolate_routines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Clear cache and set short TTL for testing
    routines_route._clear_routines_cache()
    monkeypatch.setenv("HERMES_ROUTINES_CLOUD_TTL", "300")
    # Enable cloud query so locking tests can exercise it
    monkeypatch.setenv("HERMES_ROUTINES_CLOUD", "on")
    # Mock other dependencies to be fast
    monkeypatch.setattr(routines_route, "_query_registry_routines", lambda: [])
    monkeypatch.setattr(routines_route, "_query_local_timers", lambda: [])
    monkeypatch.setattr(routines_route, "_query_cron_routines", lambda: [])

def test_cloud_routines_thundering_herd_protection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that multiple concurrent requests only trigger ONE cloud query."""
    call_count = {"n": 0}

    def slow_cloud_query(*args, **kwargs) -> dict[str, Any]:
        call_count["n"] += 1
        time.sleep(1)  # Simulate slow query
        return {"triggers": [{"name": f"cloud-{call_count['n']}", "schedule": "0 0 * * *"}]}

    monkeypatch.setattr(routines_route, "_call_claude_routines", slow_cloud_query)

    app = create_app()
    client = TestClient(app)

    def make_request():
        return client.get("/api/routines", headers=HEADERS)

    # Trigger 5 concurrent requests
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(make_request) for _ in range(5)]
        responses = [f.result() for f in futures]

    for r in responses:
        assert r.status_code == 200

    # Without locking, this would be 5 because they all see cache as invalid/empty at the same time.
    # With locking, it should be 1.
    assert call_count["n"] == 1


def test_run_routine_thundering_herd_protection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that concurrent run_routine calls serialize via the file lock.

    Calls run_routine directly from threads (bypassing HTTP) to achieve real
    concurrency, matching how multiple ASGI worker threads would execute it.
    """
    active: dict[str, int] = {"count": 0, "max_count": 0}
    counter_lock = threading.Lock()

    def slow_run(action: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        with counter_lock:
            active["count"] += 1
            active["max_count"] = max(active["max_count"], active["count"])
        time.sleep(0.3)
        with counter_lock:
            active["count"] -= 1
        return {"status": "ok"}

    monkeypatch.setattr(routines_route, "_call_claude_routines", slow_run)

    results: list[Any] = []
    errors: list[BaseException] = []
    results_lock = threading.Lock()

    def call_run() -> None:
        try:
            result = routines_route.run_routine("test-trigger")
            with results_lock:
                results.append(result)
        except Exception as exc:
            with results_lock:
                errors.append(exc)

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(call_run) for _ in range(5)]
        for f in futures:
            f.result()

    assert not errors, f"run_routine raised: {errors}"
    assert len(results) == 5
    assert active["max_count"] == 1
