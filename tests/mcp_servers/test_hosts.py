"""Unit tests for the multi-VPS host-registry tooling (hosts.json)."""
from __future__ import annotations

import json

import pytest

from claude_soma.mcp_servers.project_orchestrator import hosts as H


def _good_remote():
    return {
        "tailnet_ip": "100.102.145.110",
        "ssh_user": "ubuntu",
        "ssh_identity": "/home/ubuntu/.ssh/soma-orchestrator",
        "max_concurrent": 3,
        "ram_mb": 11000,
        "headroom_mb": 2000,
        "tier_caps": {
            "critical": {"max_mb": 6000, "high_mb": 5000},
            "standard": {"max_mb": 3000, "high_mb": 2500},
        },
        "status": "verified",
    }


def _registry():
    return {
        "local": {"tailnet_ip": None, "ssh_user": None, "ssh_identity": None},
        "vps-b": _good_remote(),
    }


def test_validate_canonical_ok():
    assert H.validate_hosts(_registry()) == []


def test_validate_requires_local():
    errs = H.validate_hosts({"vps-b": _good_remote()})
    assert any("local" in e for e in errs)


def test_validate_bad_alias():
    reg = _registry()
    reg["Bad_Alias"] = _good_remote()
    errs = H.validate_hosts(reg)
    assert any("alias must match" in e for e in errs)


def test_validate_missing_tailnet_ip():
    reg = _registry()
    reg["vps-b"]["tailnet_ip"] = None
    errs = H.validate_hosts(reg)
    assert any("tailnet_ip" in e for e in errs)


def test_validate_headroom_ge_ram():
    reg = _registry()
    reg["vps-b"]["headroom_mb"] = 12000  # >= ram_mb 11000
    errs = H.validate_hosts(reg)
    assert any("headroom_mb" in e for e in errs)


def test_validate_high_ge_max():
    reg = _registry()
    reg["vps-b"]["tier_caps"]["critical"]["high_mb"] = 9999
    errs = H.validate_hosts(reg)
    assert any("high_mb" in e for e in errs)


def test_validate_unknown_tier():
    reg = _registry()
    reg["vps-b"]["tier_caps"]["gpu"] = {"max_mb": 1, "high_mb": 0}
    errs = H.validate_hosts(reg)
    assert any("unknown tiers" in e for e in errs)


def test_validate_bad_status():
    reg = _registry()
    reg["vps-b"]["status"] = "maybe"
    errs = H.validate_hosts(reg)
    assert any("status" in e for e in errs)


def test_validate_identity_perms(tmp_path):
    key = tmp_path / "id"
    key.write_text("x")
    key.chmod(0o644)  # too open
    reg = _registry()
    reg["vps-b"]["ssh_identity"] = str(key)
    errs = H.validate_hosts(reg, check_identity_files=True)
    assert any("too open" in e for e in errs)
    key.chmod(0o600)
    assert H.validate_hosts(reg, check_identity_files=True) == []


def test_upsert_and_load(tmp_path):
    p = str(tmp_path / "hosts.json")
    H._atomic_write(p, {"local": {"tailnet_ip": None}})
    H.upsert_host("vps-c", _good_remote(), path=p)
    got = H.load_hosts(p)
    assert got["vps-c"]["tailnet_ip"] == "100.102.145.110"
    assert "local" in got  # preserved


def test_upsert_rejects_invalid(tmp_path):
    p = str(tmp_path / "hosts.json")
    H._atomic_write(p, {"local": {"tailnet_ip": None}})
    bad = _good_remote()
    bad["max_concurrent"] = 0
    with pytest.raises(ValueError):
        H.upsert_host("vps-c", bad, path=p)
    assert "vps-c" not in H.load_hosts(p)  # registry untouched on failure


def test_upsert_refuses_local(tmp_path):
    p = str(tmp_path / "hosts.json")
    H._atomic_write(p, {"local": {"tailnet_ip": None}})
    with pytest.raises(ValueError):
        H.upsert_host("local", _good_remote(), path=p)


def test_remove_host(tmp_path):
    p = str(tmp_path / "hosts.json")
    H._atomic_write(p, _registry())
    H.remove_host("vps-b", path=p)
    assert "vps-b" not in H.load_hosts(p)
    H.remove_host("vps-b", path=p)  # idempotent
    with pytest.raises(ValueError):
        H.remove_host("local", path=p)


def test_set_host_status(tmp_path):
    p = str(tmp_path / "hosts.json")
    H._atomic_write(p, _registry())
    H.set_host_status("vps-b", "unverified", path=p)
    assert H.load_hosts(p)["vps-b"]["status"] == "unverified"
    with pytest.raises(ValueError):
        H.set_host_status("vps-b", "bogus", path=p)


def test_build_host_cfg_is_valid():
    cfg = H.build_host_cfg(
        tailnet_ip="100.64.0.9", ssh_user="ubuntu",
        ssh_identity="/home/ubuntu/.ssh/soma-orchestrator",
        ram_mb=12000, max_concurrent=3,
    )
    reg = {"local": {"tailnet_ip": None}, "vps-x": cfg}
    assert H.validate_hosts(reg) == []
    assert cfg["status"] == "unverified"
    assert cfg["headroom_mb"] < cfg["ram_mb"]
