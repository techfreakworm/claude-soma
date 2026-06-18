#!/usr/bin/env python3
"""Cross-host lead AUTH-health monitor (runs on A; covers remote leads on B/C/...).

Closes the gap where a remote lead is tmux-ALIVE but its Claude session is 401'd
("Please run /login · API Error: 401") — tri-state liveness reports it alive, so
the watchdog never acts, and the lead silently stops doing useful work.

For each registry lead with host != 'local' and status 'active', this captures the
lead's pane via the forced-command guard and looks for the auth-failure signature.
On a hit it (default) RE-SHIPS A's current fresh claudeAiOauth to that host and
restarts the lead with --resume, then emits a notify — all rate-limited by a
per-lead cooldown so it can't thrash. With --alert-only it just notifies.

This is a STOPGAP. The durable fix is a long-lived CLAUDE_CODE_OAUTH_TOKEN per host
(`enroll-host --claude-oauth-token`), which removes the 401 root cause entirely.

Usage (timer): /opt/claude-soma/.venv/bin/python scripts/lead_auth_health.py
Flags: --alert-only  --cooldown-min N  --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/opt/claude-soma/src")
from claude_soma.mcp_servers.project_orchestrator.registry import Registry  # noqa: E402
from claude_soma.mcp_servers.project_orchestrator.spawner import (  # noqa: E402
    RemoteRunner,
    resume_background_lead,
)

DB = os.environ.get("HERMES_ORCH_DB", "/opt/claude-soma/registry.sqlite")
CREDS_A = os.path.expanduser("~/.claude/.credentials.json")
STATE = Path(os.environ.get("HERMES_AUTH_HEALTH_STATE",
                            "/var/lib/claude-soma/lead-auth-health.json"))
AUTH_RX = ("401 Invalid authentication", "Please run /login",
           "Invalid authentication credentials")
A_TAILNET = os.environ.get("SOMA_A_TAILNET_IP", "100.103.37.115")
NOTIFY_URL = f"http://{A_TAILNET}:9100/notify"


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} auth-health: {msg}", flush=True)


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save_state(d: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(d))
    except Exception as e:  # noqa: BLE001
        log(f"state save failed: {e}")


def _notify(lead: str, text: str) -> None:
    tok = os.environ.get("HERMES_NOTIFY_TOKEN", "")
    body = json.dumps({"lead": lead, "type": "MILESTONE",
                       "payload_json": json.dumps({"progress": text[:300]})}).encode()
    import urllib.request
    req = urllib.request.Request(
        NOTIFY_URL, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {tok}"})
    try:
        urllib.request.urlopen(req, timeout=8).close()
    except Exception:  # noqa: BLE001
        pass


def _reship_creds(host_cfg: dict) -> bool:
    """Re-ship A's fresh claudeAiOauth (with refreshToken) to the host. No values logged."""
    d = json.load(open(CREDS_A))
    o = d.get("claudeAiOauth") or {}
    if not (o.get("accessToken") and o.get("refreshToken")):
        log("A creds missing access/refresh — cannot re-ship")
        return False
    payload = json.dumps({"claudeAiOauth": o})
    ip, user = host_cfg["tailnet_ip"], host_cfg.get("ssh_user", "ubuntu")
    admin_key = os.path.expanduser("~/.ssh/id_ed25519")
    ssh = ["ssh", "-i", admin_key, "-o", "IdentitiesOnly=yes",
           "-o", "BatchMode=yes", "-o", "ConnectTimeout=12", f"{user}@{ip}"]
    helper = (
        "import json,os,sys;h=os.path.expanduser('~/.claude/.credentials.json');"
        "os.makedirs(os.path.dirname(h),exist_ok=True);n=json.load(sys.stdin);"
        "c=json.load(open(h)) if os.path.exists(h) else {};c['claudeAiOauth']=n['claudeAiOauth'];"
        "fd=os.open(h,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600);os.write(fd,json.dumps(c).encode());os.close(fd)"
    )
    try:
        p = subprocess.run(ssh + [f"python3 -c \"{helper}\""], input=payload,
                           text=True, capture_output=True, timeout=30)
        return p.returncode == 0
    except subprocess.SubprocessError as e:
        log(f"re-ship failed: {e}")
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--alert-only", action="store_true")
    ap.add_argument("--cooldown-min", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    from claude_soma.mcp_servers.project_orchestrator.hosts import load_hosts
    reg = Registry(DB)
    hosts = load_hosts()
    state = _load_state()
    now = time.time()
    acted = 0

    for row in reg.list_active():
        host = row.get("host", "local")
        if host == "local":
            continue
        name = row["name"]
        try:
            cp = RemoteRunner(host).capture(name)
            pane = (cp.stdout or "")
        except Exception as e:  # noqa: BLE001
            log(f"{name}@{host}: capture failed ({e}); skipping")
            continue
        if not any(sig in pane for sig in AUTH_RX):
            continue  # healthy
        last = state.get(name, 0)
        if now - last < a.cooldown_min * 60:
            log(f"{name}@{host}: 401 detected but in cooldown ({int((now-last)/60)}m ago); skip")
            continue
        log(f"{name}@{host}: AUTH-FAILED (401) detected")
        state[name] = now
        if a.dry_run:
            log(f"{name}@{host}: DRY — would re-ship creds + restart")
            continue
        if a.alert_only:
            _notify(name, f"[auth-health] {name}@{host} is 401/auth-failed — overseer needs re-auth (durable fix: setup-token)")
            acted += 1
            continue
        # auto-remediate: re-ship fresh creds + restart the lead
        cfg = hosts.get(host, {})
        ok = _reship_creds(cfg) if cfg.get("tailnet_ip") else False
        if not ok:
            _notify(name, f"[auth-health] {name}@{host} 401 and auto re-ship FAILED — manual re-auth needed")
            continue
        try:
            sp = resume_background_lead(
                name=name, cwd=Path(row["cwd"]), permission_mode=row.get("permission_mode", "acceptEdits"),
                session_uuid=row["session_uuid"], host=host, tier=row.get("tier", "standard"), force=True)
            reg.register(name, agent_id=sp["agent_id"], type_=row["type"], cwd=row["cwd"],
                         rc_url=sp.get("rc_url"), permission_mode=row.get("permission_mode", "acceptEdits"),
                         brief=row.get("brief"), host=host, tier=row.get("tier", "standard"))
            log(f"{name}@{host}: re-shipped creds + restarted (--resume)")
            _notify(name, f"[auth-health] {name}@{host} was 401 — auto-recovered (re-ship + restart). Durable fix: setup-token.")
            acted += 1
        except Exception as e:  # noqa: BLE001
            log(f"{name}@{host}: restart failed: {e}")
            _notify(name, f"[auth-health] {name}@{host} 401, re-ship ok but RESTART FAILED: {e}")

    _save_state(state)
    log(f"done; acted on {acted} lead(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
