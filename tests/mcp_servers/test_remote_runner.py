"""Pure unit tests for the multi-VPS RemoteRunner contract + tri-state liveness
+ per-host admission. No live ssh: the guard-contract builder, the ssh argv
builder, the returncode->liveness classifier, the local-argv-unchanged invariant,
and the memory admission rule are all pure functions."""
from __future__ import annotations

import base64

import pytest

from claude_soma.mcp_servers.project_orchestrator import spawner as sp
from claude_soma.mcp_servers.project_orchestrator import server as srv


def test_build_spawn_contract_base64_no_whitespace():
    line = sp.build_guard_command(
        "spawn", "guardtest", mode="acceptEdits",
        uuid_="11111111-1111-1111-1111-111111111111",
        tier="standard", brief="hi there; rm -rf / `id`",
    )
    parts = line.split(" ")
    assert parts[:5] == [
        "spawn", "guardtest", "acceptEdits",
        "11111111-1111-1111-1111-111111111111", "standard",
    ]
    assert "\n" not in line and len(parts) == 6  # brief is ONE base64 token
    assert base64.b64decode(parts[5]).decode() == "hi there; rm -rf / `id`"


def test_build_resume_contract():
    line = sp.build_guard_command(
        "resume", "x", mode="default",
        uuid_="22222222-2222-2222-2222-222222222222", tier="critical", brief="go",
    )
    parts = line.split(" ")
    assert parts[:5] == ["resume", "x", "default",
                         "22222222-2222-2222-2222-222222222222", "critical"]
    assert base64.b64decode(parts[5]).decode() == "go"


def test_simple_verbs():
    assert sp.build_guard_command("has-session", "x") == "has-session x"
    assert sp.build_guard_command("kill", "x") == "kill x"
    assert sp.build_guard_command("capture", "x") == "capture x"
    with pytest.raises(ValueError):
        sp.build_guard_command("bogus", "x")


def test_ssh_argv_uses_identity_batch_and_ip(monkeypatch):
    monkeypatch.setattr(sp, "load_hosts", lambda: {"vps-b": {
        "tailnet_ip": "100.102.145.110", "ssh_user": "ubuntu",
        "ssh_identity": "/k", "tier_caps": {}}})
    rr = sp.RemoteRunner("vps-b")
    argv = rr._argv("has-session x")
    assert "-i" in argv and "/k" in argv
    assert "IdentitiesOnly=yes" in argv and "BatchMode=yes" in argv
    assert "ubuntu@100.102.145.110" in argv and argv[-1] == "has-session x"


def test_remoterunner_rejects_non_remote_host(monkeypatch):
    monkeypatch.setattr(sp, "load_hosts", lambda: {"local": {"tailnet_ip": None}})
    with pytest.raises(RuntimeError):
        sp.RemoteRunner("local")


def test_tristate_classifier():
    assert sp._classify_remote_liveness(0) == "alive"
    assert sp._classify_remote_liveness(1) == "dead"
    assert sp._classify_remote_liveness(255) == "unreachable"  # ssh transport
    assert sp._classify_remote_liveness(99) == "unreachable"   # guard DENY
    assert sp._classify_remote_liveness(2) == "unreachable"    # ambiguous -> never revive


def test_local_spawn_argv_unchanged_when_no_caps(monkeypatch):
    # host=local has no tier_caps -> mem_props empty -> argv byte-identical.
    monkeypatch.setattr(sp, "load_hosts", lambda: {"local": {"tailnet_ip": None}})
    assert sp._local_mem_props("local", "standard") == []
    base = sp._wrap_in_transient_unit("x", ["tmux"])
    withp = sp._wrap_in_transient_unit("x", ["tmux"], mem_props=[])
    assert base == withp


def test_remote_mem_props_present(monkeypatch):
    monkeypatch.setattr(sp, "load_hosts", lambda: {"vps-b": {
        "tailnet_ip": "1.2.3.4", "ssh_identity": "/k",
        "tier_caps": {"critical": {"max_mb": 6000, "high_mb": 5000}}}})
    props = sp._local_mem_props("vps-b", "critical")
    assert "--property=MemoryMax=6000M" in props
    assert "--property=MemoryHigh=5000M" in props


def test_admission_refuses_over_ram(monkeypatch):
    monkeypatch.setattr(srv, "_host_cfg", lambda h: {
        "max_concurrent": 9, "ram_mb": 11000, "headroom_mb": 2000,
        "tier_caps": {"critical": {"max_mb": 6000}, "standard": {"max_mb": 3000}}})
    active = [{"name": "a", "host": "vps-b", "tier": "critical"}]  # 6000 reserved
    with pytest.raises(RuntimeError):  # 6000 + new 6000 + 2000 > 11000
        srv._admit_or_raise("vps-b", "critical", active)
    srv._admit_or_raise("vps-b", "standard", active)  # 6000 + 3000 + 2000 = 11000 OK


def test_admission_refuses_over_concurrency(monkeypatch):
    monkeypatch.setattr(srv, "_host_cfg", lambda h: {"max_concurrent": 1})
    active = [{"name": "a", "host": "vps-b", "tier": "standard"}]
    with pytest.raises(RuntimeError):
        srv._admit_or_raise("vps-b", "standard", active)


def test_is_lead_alive_bool_wrapper(monkeypatch):
    monkeypatch.setattr(sp, "lead_liveness", lambda n, h="local": "alive")
    assert sp.is_lead_alive("x", "vps-b") is True
    monkeypatch.setattr(sp, "lead_liveness", lambda n, h="local": "unreachable")
    assert sp.is_lead_alive("x", "vps-b") is False
    monkeypatch.setattr(sp, "lead_liveness", lambda n, h="local": "dead")
    assert sp.is_lead_alive("x", "vps-b") is False
