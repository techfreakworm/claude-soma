from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claude_soma.mcp_servers.hermes_api import server as ha_server


@pytest.fixture(autouse=True)
def _redirect_prewarm_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect the module-level marker path to a tmp location for test isolation."""
    monkeypatch.setattr(ha_server, "_PREWARM_MARKER", tmp_path / "hermes-prewarm-last.ts")


def _make_routines_mock() -> tuple[MagicMock, dict]:
    """Return (mock_query_fn, sys_modules_patch) for injecting the routines module."""
    mock_query = MagicMock()
    mock_module = MagicMock()
    mock_module._query_cloud_routines_cached = mock_query
    sys_patch = {
        "claude_soma.api": MagicMock(),
        "claude_soma.api.routes": MagicMock(),
        "claude_soma.api.routes.routines": mock_module,
    }
    return mock_query, sys_patch


def test_prewarm_debounced_when_marker_is_recent(tmp_path: Path) -> None:
    """If the marker file records a timestamp < 300s ago, skip the warm call."""
    marker = ha_server._PREWARM_MARKER
    marker.write_text(str(time.time() - 10))  # 10 seconds ago

    mock_query, sys_patch = _make_routines_mock()

    with patch.dict(sys.modules, sys_patch):
        ha_server._prewarm_routines_cache()

    mock_query.assert_not_called()


def test_prewarm_fires_when_marker_is_stale(tmp_path: Path) -> None:
    """If the marker file records a timestamp > 300s ago, the warm call must execute."""
    marker = ha_server._PREWARM_MARKER
    marker.write_text(str(time.time() - 400))  # 400 seconds ago (stale)

    mock_query, sys_patch = _make_routines_mock()

    with patch.dict(sys.modules, sys_patch):
        ha_server._prewarm_routines_cache()

    mock_query.assert_called_once()


def test_prewarm_debounce_then_fire_after_marker_erased(tmp_path: Path) -> None:
    """Two-phase: debounced with recent marker, then fires after marker is erased."""
    marker = ha_server._PREWARM_MARKER
    mock_query, sys_patch = _make_routines_mock()

    # Phase 1: write a recent marker → debounced, call count == 0
    marker.write_text(str(time.time() - 10))

    with patch.dict(sys.modules, sys_patch):
        ha_server._prewarm_routines_cache()

    assert mock_query.call_count == 0, "warm should be suppressed by recent marker"

    # Phase 2: erase the marker → fires, call count == 1
    marker.unlink()

    with patch.dict(sys.modules, sys_patch):
        ha_server._prewarm_routines_cache()

    assert mock_query.call_count == 1, "warm should execute once after marker erased"


def test_prewarm_fires_when_no_marker(tmp_path: Path) -> None:
    """No marker file → warm executes immediately."""
    marker = ha_server._PREWARM_MARKER
    assert not marker.exists(), "marker must not exist for this test"

    mock_query, sys_patch = _make_routines_mock()

    with patch.dict(sys.modules, sys_patch):
        ha_server._prewarm_routines_cache()

    mock_query.assert_called_once()


def test_prewarm_writes_marker_after_successful_warm(tmp_path: Path) -> None:
    """After a successful warm call, the marker file is written with current timestamp."""
    marker = ha_server._PREWARM_MARKER
    assert not marker.exists()

    mock_query, sys_patch = _make_routines_mock()
    before = time.time()

    with patch.dict(sys.modules, sys_patch):
        ha_server._prewarm_routines_cache()

    assert marker.exists(), "marker file should be created after warm"
    written_ts = float(marker.read_text().strip())
    assert written_ts >= before
    assert written_ts <= time.time() + 1
