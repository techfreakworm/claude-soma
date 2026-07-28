#!/usr/bin/env python3
"""B-local AUTH-health monitor for the algo-trader lead.

Mirrors A's cross-host monitor (scripts/lead_auth_health.py) but runs ON B and
covers the local algo-trader lead, which A's monitor currently skips (A's
registry marks it host=vps-b status=dead, and A only acts on list_active rows).

Logic: ping Claude auth using ONLY the env CLAUDE_CODE_OAUTH_TOKEN in a throwaway
HOME (no creds cache). If it PONGs -> healthy, exit. If it 401s/auth-fails:
  (a) move any expired short-lived ~/.claude/.credentials.json aside so the lead
      falls back to the durable env token,
  (b) alert the operator via A's cross-host notify endpoint,
  (c) ALERT-ONLY by default. It does NOT restart the trading lead (conservative;
      flag for manual action). Pass --auto-resume to opt into a --resume restart.

NEVER logs the token value.
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

SECRETS = "/etc/claude-soma/secrets.env"
CREDS = os.path.expanduser("~/.claude/.credentials.json")
CLAUDE_BIN = "/home/ubuntu/.local/bin/claude"
LEAD = "algo-trader"
A_TAILNET = os.environ.get("SOMA_A_TAILNET_IP", "100.103.37.115")
NOTIFY_URL = f"http://{A_TAILNET}:9100/notify"
STATE = Path(os.environ.get("HERMES_AUTH_HEALTH_STATE",
                            "/var/lib/claude-soma/algo-trader-auth-health.json"))
AUTH_RX = ("401 Invalid authentication", "Please run /login",
           "Invalid authentication credentials", "OAuth token has expired",
           "Invalid bearer token", "authentication_error")
QUOTA_RX = ("weekly limit", "usage limit", "rate limit", "resets",
            "out of quota", "quota exceeded", "hit your limit")


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    print(f"{ts} algo-auth-health: {msg}", flush=True)


def _read_token() -> str:
    try:
        for ln in open(SECRETS):
            if ln.startswith("CLAUDE_CODE_OAUTH_TOKEN="):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception as e:  # noqa: BLE001
        log(f"cannot read secrets: {e}")
    return ""


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


def _notify(text: str) -> None:
    tok = os.environ.get("HERMES_NOTIFY_TOKEN", "")
    body = json.dumps({"lead": LEAD, "type": "MILESTONE",
                       "payload_json": json.dumps({"progress": text[:300]})}).encode()
    req = urllib.request.Request(
        NOTIFY_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
    try:
        urllib.request.urlopen(req, timeout=8).close()
        log("operator alerted via notify")
    except Exception as e:  # noqa: BLE001
        log(f"notify failed: {e}")


def _ping(token: str) -> tuple[bool, str]:
    """True if env token PONGs. Uses a throwaway HOME so no creds cache interferes."""
    import tempfile
    th = tempfile.mkdtemp(prefix="algo-authchk.")
    env = dict(os.environ)
    env["HOME"] = th
    env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    env.pop("CLAUDE_CONFIG_DIR", None)
    try:
        p = subprocess.run(
            [CLAUDE_BIN, "--setting-sources", "project,local", "-p",
             "reply with exactly: PONG"],
            env=env, text=True, capture_output=True, timeout=120)
        out = (p.stdout or "") + (p.stderr or "")
        ok = "PONG" in (p.stdout or "") and p.returncode == 0
        return ok, out[-400:]
    except subprocess.TimeoutExpired:
        return False, "ping timeout"
    except Exception as e:  # noqa: BLE001
        return False, f"ping error: {e}"
    finally:
        shutil.rmtree(th, ignore_errors=True)


def _move_creds_aside() -> bool:
    if not os.path.exists(CREDS):
        return False
    dest = CREDS + f".expired-{int(time.time())}"
    try:
        shutil.move(CREDS, dest)
        log(f"moved short-lived creds aside -> {dest} (lead now uses env token)")
        return True
    except Exception as e:  # noqa: BLE001
        log(f"move-aside failed: {e}")
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto-resume", action="store_true",
                    help="restart the lead with --resume if auth is dead (default: alert-only)")
    ap.add_argument("--cooldown-min", type=int, default=60)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    token = _read_token()
    if not token:
        log("no env token in secrets — cannot self-check; alerting")
        _notify("[algo-auth-health] CLAUDE_CODE_OAUTH_TOKEN missing on B — operator must re-auth")
        return 2

    ok, tail = _ping(token)
    if ok:
        log("env token healthy (PONG)")
        return 0
    if not any(sig.lower() in tail.lower() for sig in AUTH_RX) and "timeout" in tail.lower():
        log("ping timed out (not a clear 401) — treating as transient, no action")
        return 0
    if not any(sig.lower() in tail.lower() for sig in AUTH_RX) and \
            any(q in tail.lower() for q in QUOTA_RX):
        log("QUOTA-LIMITED: over weekly/usage quota, will retry after reset "
            "-- not an auth failure")
        return 0
    log(f"AUTH-FAILED: env token did not PONG. tail: {tail}")
    state = _load_state()
    now = time.time()
    if now - state.get("last_alert", 0) < a.cooldown_min * 60:
        log("in cooldown; skipping alert/action")
        return 1
    if a.dry_run:
        log("DRY: would move-aside creds + alert" + (" + resume" if a.auto_resume else ""))
        return 1
    moved = _move_creds_aside()
    msg = (f"[algo-auth-health] B's env CLAUDE_CODE_OAUTH_TOKEN FAILED a PONG ping "
           f"(401/auth). algo-trader auth is DEAD. creds-cache-moved-aside={moved}. "
           f"Operator: re-mint the setup-token on B (claude setup-token) ASAP.")
    if a.auto_resume:
        try:
            subprocess.run(["systemctl", "restart", f"claude-soma-lead-{LEAD}.service"],
                           timeout=30, check=False)
            msg += " Auto-resume restart attempted."
        except Exception as e:  # noqa: BLE001
            msg += f" Auto-resume FAILED: {e}"
    else:
        msg += " (alert-only; lead NOT restarted to avoid disrupting trading.)"
    _notify(msg)
    state["last_alert"] = now
    _save_state(state)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
