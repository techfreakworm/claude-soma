from __future__ import annotations

import time

from claude_soma.mcp_servers.hermes_api.alarm_worker import _run_alarm_tick

_LEAD_NAME = "test-lead"
_ACTIVE_LEADS = [{"name": _LEAD_NAME}]
_THRESHOLD = 150000
_DEBOUNCE = 3600.0


def test_no_alarm_below_threshold() -> None:
    calls: list[str] = []
    last_alarm: dict[str, float] = {}
    _run_alarm_tick(
        active_leads=_ACTIVE_LEADS,
        estimate_fn=lambda n: 100000,
        send_fn=calls.append,
        last_alarm=last_alarm,
        threshold=_THRESHOLD,
        debounce=_DEBOUNCE,
    )
    assert len(calls) == 0


def test_alarm_fires_above_threshold() -> None:
    calls: list[str] = []
    last_alarm: dict[str, float] = {}
    _run_alarm_tick(
        active_leads=_ACTIVE_LEADS,
        estimate_fn=lambda n: 160000,
        send_fn=calls.append,
        last_alarm=last_alarm,
        threshold=_THRESHOLD,
        debounce=_DEBOUNCE,
    )
    assert len(calls) == 1
    assert "~160000" in calls[0]
    assert _LEAD_NAME in calls[0]


def test_alarm_debounced() -> None:
    calls: list[str] = []
    last_alarm: dict[str, float] = {}
    now = time.time()
    _run_alarm_tick(
        active_leads=_ACTIVE_LEADS,
        estimate_fn=lambda n: 160000,
        send_fn=calls.append,
        last_alarm=last_alarm,
        threshold=_THRESHOLD,
        debounce=_DEBOUNCE,
        now=now,
    )
    _run_alarm_tick(
        active_leads=_ACTIVE_LEADS,
        estimate_fn=lambda n: 160000,
        send_fn=calls.append,
        last_alarm=last_alarm,
        threshold=_THRESHOLD,
        debounce=_DEBOUNCE,
        now=now + 10,
    )
    assert len(calls) == 1


def test_alarm_refires_after_debounce() -> None:
    calls: list[str] = []
    last_alarm: dict[str, float] = {}
    now = time.time()
    _run_alarm_tick(
        active_leads=_ACTIVE_LEADS,
        estimate_fn=lambda n: 160000,
        send_fn=calls.append,
        last_alarm=last_alarm,
        threshold=_THRESHOLD,
        debounce=_DEBOUNCE,
        now=now,
    )
    _run_alarm_tick(
        active_leads=_ACTIVE_LEADS,
        estimate_fn=lambda n: 160000,
        send_fn=calls.append,
        last_alarm=last_alarm,
        threshold=_THRESHOLD,
        debounce=_DEBOUNCE,
        now=now + _DEBOUNCE + 1,
    )
    assert len(calls) == 2
