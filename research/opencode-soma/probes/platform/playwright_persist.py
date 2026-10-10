#!/usr/bin/env python3
"""Playwright persistent-profile probe: a desktop browser profile replaces the VNC'd server browser.

Creates a throwaway venv with playwright, downloads Playwright's own Chromium into
$OCSOMA_SCRATCH/pw/browsers (PLAYWRIGHT_BROWSERS_PATH), and serves a tiny page on 127.0.0.1:47250.
Launch 1 (headless) logs in: the server sets a persistent cookie and the page writes localStorage.
Launch 2 (headless, same user-data-dir) and launch 3 (headed, same dir) must still see both, while a
control launch with a different empty dir must see neither. Never touches the operator's real
browser profiles. Writes evidence/platform/playwright-persist.log.

    python3.12 playwright_persist.py            # re-execs itself inside the venv
"""
from __future__ import annotations

import http.server
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "evidence" / "platform" / "playwright-persist.log"
SCRATCH = Path(os.environ.get("OCSOMA_SCRATCH", Path(tempfile.gettempdir()) / "ocsoma-me"))
WORK = SCRATCH / "pw"
VENV = WORK / "venv"
PW_VERSION = os.environ.get("PW_VERSION", "1.63.0")
PORT = 47250


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = b"<html><body><h1>probe</h1></body></html>"
        self.send_response(200)
        if self.path.startswith("/login"):
            self.send_header("Set-Cookie", "session=probe-session-123; Max-Age=2592000; Path=/; HttpOnly; SameSite=Lax")
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def inner() -> int:
    from playwright.sync_api import sync_playwright
    lines: list[str] = []

    def out(s: str = "") -> None:
        lines.append(s)
        print(s, flush=True)

    out(f"date_utc: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    out("command: python3.12 playwright_persist.py   (re-executed in the scratch venv)")
    out(f"host: {platform.system()} {platform.release()} {platform.machine()}  python {platform.python_version()}")
    out(f"playwright: {PW_VERSION}  PLAYWRIGHT_BROWSERS_PATH={os.environ['PLAYWRIGHT_BROWSERS_PATH']}")
    out(os.environ.get("OCSOMA_PW_INSTALL_NOTE", ""))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{PORT}"
    profile = WORK / "profile-a"
    control = WORK / "profile-control"
    for d in (profile, control):
        shutil.rmtree(d, ignore_errors=True)
    results = {}
    with sync_playwright() as p:
        out(f"chromium executable: {p.chromium.executable_path}")
        t0 = time.perf_counter()
        ctx = p.chromium.launch_persistent_context(str(profile), headless=True)
        out(f"launch 1 (headless) persistent context in {time.perf_counter() - t0:.2f}s, browser {ctx.browser.version if ctx.browser else 'n/a'}")
        page = ctx.new_page()
        page.goto(base + "/login")
        page.evaluate("localStorage.setItem('soma_token_hint', 'logged-in-as-probe')")
        results["launch1"] = {"cookies": [c["name"] for c in ctx.cookies(base)],
                              "localStorage": page.evaluate("localStorage.getItem('soma_token_hint')")}
        out(f"launch 1 state after login: {json.dumps(results['launch1'])}")
        ctx.close()
        out(f"profile dir after close: {sum(1 for _ in profile.rglob('*'))} files, "
            f"{sum(f.stat().st_size for f in profile.rglob('*') if f.is_file()) / 1e6:.1f} MB")
        for label, headless in (("launch 2 (headless, same dir)", True), ("launch 3 (headed, same dir)", False)):
            t0 = time.perf_counter()
            ctx = p.chromium.launch_persistent_context(str(profile), headless=headless)
            page = ctx.new_page()
            page.goto(base + "/check")
            st = {"cookies": [c["name"] for c in ctx.cookies(base)],
                  "localStorage": page.evaluate("localStorage.getItem('soma_token_hint')")}
            results[label] = st
            out(f"{label}: {json.dumps(st)} ({time.perf_counter() - t0:.2f}s)")
            ctx.close()
        ctx = p.chromium.launch_persistent_context(str(control), headless=True)
        page = ctx.new_page()
        page.goto(base + "/check")
        results["control"] = {"cookies": [c["name"] for c in ctx.cookies(base)],
                              "localStorage": page.evaluate("localStorage.getItem('soma_token_hint')")}
        out(f"control (different empty dir): {json.dumps(results['control'])}")
        ctx.close()
    srv.shutdown()
    want = {"cookies": ["session"], "localStorage": "logged-in-as-probe"}
    ok = (results["launch1"] == want and results["launch 2 (headless, same dir)"] == want
          and results["launch 3 (headed, same dir)"] == want
          and results["control"] == {"cookies": [], "localStorage": None})
    out(f"VERDICT: {'PASS' if ok else 'FAIL'} — a Playwright persistent context kept its cookie and localStorage "
        f"across a headless relaunch and a headed relaunch of the same user-data-dir; an empty control dir had neither")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(lines) + "\n")
    return 0 if ok else 1


def outer() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    py = VENV / "bin" / "python"
    notes = []
    if not py.exists():
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True, timeout=120)
    t0 = time.perf_counter()
    r = subprocess.run([str(VENV / "bin" / "pip"), "install", "-q", "--disable-pip-version-check", f"playwright=={PW_VERSION}"],
                       capture_output=True, text=True, timeout=900)
    notes.append(f"pip install playwright=={PW_VERSION}: rc={r.returncode} {time.perf_counter() - t0:.1f}s")
    env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(WORK / "browsers"))
    t0 = time.perf_counter()
    r = subprocess.run([str(py), "-m", "playwright", "install", "chromium"], capture_output=True, text=True,
                       timeout=1800, env=env)
    size = subprocess.run(["du", "-sk", str(WORK / "browsers")], capture_output=True, text=True).stdout.split()[0]
    notes.append(f"playwright install chromium: rc={r.returncode} {time.perf_counter() - t0:.1f}s, "
                 f"browsers dir {int(size) / 1024:.0f} MB; {r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ''}")
    env["OCSOMA_PW_INSTALL_NOTE"] = " | ".join(notes)
    return subprocess.run([str(py), str(Path(__file__).resolve()), "--inner"], env=env, timeout=600).returncode


if __name__ == "__main__":
    sys.exit(inner() if "--inner" in sys.argv else outer())
