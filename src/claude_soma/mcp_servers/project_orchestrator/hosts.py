"""Host-registry (`hosts.json`) tooling: load / validate / atomic upsert / remove.

This is the *tooling-side* authority for the multi-VPS host registry consumed by
`soma-install enroll-host|validate-hosts|remove-host` and `scripts/enroll_vps_host.sh`.

The live runtime (`spawner.load_hosts` / `_host_cfg`) reads the SAME file but keeps
its own tiny reader so the running orchestrator is never coupled to this tooling
module (no restart needed when this changes). Both honour `HERMES_HOSTS_JSON`.

Schema (one entry per host alias):

    {
      "local": { "tailnet_ip": null, "ssh_user": null, "ssh_identity": null },
      "<alias>": {
        "tailnet_ip": "100.x.y.z",
        "ssh_user": "ubuntu",
        "ssh_identity": "/home/ubuntu/.ssh/soma-orchestrator",  # forced-command key
        "max_concurrent": 3,
        "ram_mb": 11000,
        "headroom_mb": 2000,
        "tier_caps": { "critical": {"max_mb":6000,"high_mb":5000},
                       "standard": {"max_mb":3000,"high_mb":2500} },
        "status": "verified" | "unverified"
      }
    }
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from typing import Any

HOSTS_JSON = os.environ.get(
    "HERMES_HOSTS_JSON", "/opt/claude-soma/config/claude/hosts.json"
)

# host alias: lowercase, starts with a letter, <=32 chars (matches the guard NAME_RX family)
ALIAS_RX = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
TIERS = ("critical", "standard")
VALID_STATUS = ("verified", "unverified")

# sane defaults used when the operator does not override on enroll
DEFAULT_TIER_CAPS = {
    "critical": {"max_mb": 6000, "high_mb": 5000},
    "standard": {"max_mb": 3000, "high_mb": 2500},
}


def load_hosts(path: str = HOSTS_JSON) -> dict:
    """Read hosts.json; fall back to a local-only registry if missing/corrupt."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"local": {"tailnet_ip": None, "ssh_user": None, "ssh_identity": None}}


def _validate_remote(alias: str, cfg: dict, *, check_identity_files: bool) -> list[str]:
    errs: list[str] = []

    def err(msg: str) -> None:
        errs.append(f"{alias}: {msg}")

    ip = cfg.get("tailnet_ip")
    if not isinstance(ip, str) or not ip:
        err("tailnet_ip must be a non-empty string for a remote host")
    if not isinstance(cfg.get("ssh_user") or "", str) or not cfg.get("ssh_user"):
        err("ssh_user must be a non-empty string")
    ident = cfg.get("ssh_identity")
    if not isinstance(ident, str) or not ident:
        err("ssh_identity must be a path to the forced-command key")
    elif check_identity_files:
        if not os.path.exists(ident):
            err(f"ssh_identity {ident!r} does not exist")
        else:
            mode = os.stat(ident).st_mode & 0o777
            if mode & 0o077:
                err(f"ssh_identity {ident!r} perms {oct(mode)} too open (want 0600)")

    mc = cfg.get("max_concurrent")
    if not isinstance(mc, int) or mc <= 0:
        err("max_concurrent must be a positive int")

    ram = cfg.get("ram_mb")
    head = cfg.get("headroom_mb")
    if not isinstance(ram, int) or ram <= 0:
        err("ram_mb must be a positive int")
    if not isinstance(head, int) or head < 0:
        err("headroom_mb must be a non-negative int")
    if isinstance(ram, int) and isinstance(head, int) and head >= ram:
        err(f"headroom_mb ({head}) must be < ram_mb ({ram})")

    caps = cfg.get("tier_caps")
    if not isinstance(caps, dict):
        err("tier_caps must be an object with 'critical' and 'standard'")
    else:
        for tier in TIERS:
            tc = caps.get(tier)
            if not isinstance(tc, dict):
                err(f"tier_caps.{tier} missing")
                continue
            mx, hi = tc.get("max_mb"), tc.get("high_mb")
            if not isinstance(mx, int) or not isinstance(hi, int):
                err(f"tier_caps.{tier} max_mb/high_mb must be ints")
            elif hi >= mx:
                err(f"tier_caps.{tier}: high_mb ({hi}) must be < max_mb ({mx})")
        extra = set(caps) - set(TIERS)
        if extra:
            err(f"tier_caps has unknown tiers {sorted(extra)}")

    status = cfg.get("status")
    if status is not None and status not in VALID_STATUS:
        err(f"status {status!r} not in {VALID_STATUS}")

    xp = cfg.get("extra_paths")
    if xp is not None and (not isinstance(xp, list) or not all(isinstance(p, str) for p in xp)):
        err("extra_paths must be a list of absolute-path strings")

    return errs


def validate_hosts(hosts: dict, *, check_identity_files: bool = False) -> list[str]:
    """Return a list of human-readable errors; empty list == valid.

    `local` is special: only its (nullable) tailnet_ip/ssh_* keys are allowed; it
    carries no caps (the orchestrator host uses env MAX_CONCURRENT). Pass
    check_identity_files=True (CLI path) to also assert the key file exists @0600.
    """
    if not isinstance(hosts, dict) or not hosts:
        return ["hosts.json must be a non-empty object"]
    errs: list[str] = []
    if "local" not in hosts:
        errs.append("hosts.json must contain a 'local' entry")
    for alias, cfg in hosts.items():
        if not ALIAS_RX.match(alias):
            errs.append(f"{alias!r}: alias must match {ALIAS_RX.pattern}")
        if not isinstance(cfg, dict):
            errs.append(f"{alias}: entry must be an object")
            continue
        if alias == "local":
            continue  # local needs no remote fields
        errs.extend(_validate_remote(alias, cfg, check_identity_files=check_identity_files))
    return errs


def _atomic_write(path: str, data: dict) -> None:
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".hosts.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def upsert_host(
    alias: str,
    cfg: dict[str, Any],
    *,
    path: str = HOSTS_JSON,
    check_identity_files: bool = False,
) -> dict:
    """Insert/replace a host entry, validating the WHOLE merged registry first.

    Raises ValueError if the result would be invalid (registry left untouched).
    Returns the new registry dict.
    """
    if not ALIAS_RX.match(alias):
        raise ValueError(f"alias must match {ALIAS_RX.pattern}, got {alias!r}")
    if alias == "local":
        raise ValueError("refusing to upsert the reserved 'local' host")
    hosts = load_hosts(path)
    merged = dict(hosts)
    merged[alias] = cfg
    errs = validate_hosts(merged, check_identity_files=check_identity_files)
    if errs:
        raise ValueError("invalid hosts.json after upsert:\n  " + "\n  ".join(errs))
    _atomic_write(path, merged)
    return merged


def set_host_status(alias: str, status: str, *, path: str = HOSTS_JSON) -> dict:
    if status not in VALID_STATUS:
        raise ValueError(f"status must be one of {VALID_STATUS}")
    hosts = load_hosts(path)
    if alias not in hosts:
        raise ValueError(f"unknown host {alias!r}")
    hosts[alias] = {**hosts[alias], "status": status}
    _atomic_write(path, hosts)
    return hosts


def remove_host(alias: str, *, path: str = HOSTS_JSON) -> dict:
    """Drop a host entry. Refuses to remove 'local'. Idempotent on absence."""
    if alias == "local":
        raise ValueError("refusing to remove the reserved 'local' host")
    hosts = load_hosts(path)
    hosts.pop(alias, None)
    _atomic_write(path, hosts)
    return hosts


def build_host_cfg(
    *,
    tailnet_ip: str,
    ssh_user: str,
    ssh_identity: str,
    ram_mb: int,
    max_concurrent: int,
    headroom_mb: int | None = None,
    tier_caps: dict | None = None,
    extra_paths: list[str] | None = None,
    status: str = "unverified",
) -> dict:
    """Assemble a well-formed host cfg from enroll inputs (defaults filled in)."""
    cfg = {
        "tailnet_ip": tailnet_ip,
        "ssh_user": ssh_user,
        "ssh_identity": ssh_identity,
        "max_concurrent": max_concurrent,
        "ram_mb": ram_mb,
        "headroom_mb": headroom_mb if headroom_mb is not None else max(1000, ram_mb // 6),
        "tier_caps": tier_caps or json.loads(json.dumps(DEFAULT_TIER_CAPS)),
        "status": status,
    }
    if extra_paths:
        cfg["extra_paths"] = list(extra_paths)
    return cfg
