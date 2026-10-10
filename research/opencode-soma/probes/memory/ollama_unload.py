#!/usr/bin/env python3
"""Unload one idle ollama model (POST /api/generate with keep_alive 0) and record the evidence:
/api/ps before and after, and the tail of ~/.ollama/logs/server.log, which must show the runner
idle ("all slots are idle") with no prompt started since. Refuses to unload if the log shows a
slot that started work and has not released it. Appends one run section to
evidence/memory/ollama-unload.log, which documents the model switches this research needed on a
shared ollama.

    python3.12 ollama_unload.py --model lfm2.5-thinking --reason "my curation run finished; free the GPU for P4"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

OL = "http://127.0.0.1:11434"
LOG = Path(__file__).resolve().parents[2] / "evidence" / "memory" / "ollama-unload.log"
SERVER_LOG = Path.home() / ".ollama" / "logs" / "server.log"


def ps() -> list[str]:
    return [m["name"] for m in json.load(urllib.request.urlopen(OL + "/api/ps", timeout=5))["models"]]


def slots_idle() -> tuple[bool, str]:
    tail = SERVER_LOG.read_bytes()[-20000:].decode(errors="replace").splitlines()
    last_new = max((i for i, ln in enumerate(tail) if "new prompt" in ln or "processing task" in ln), default=-1)
    last_idle = max((i for i, ln in enumerate(tail) if "all slots are idle" in ln), default=-1)
    last_line = next((ln for ln in reversed(tail) if "slot" in ln or "srv" in ln), "")
    return last_idle > last_new, re.sub(r"\s+", " ", last_line)[:160]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--reason", required=True)
    a = ap.parse_args()
    lines = [f"date_utc: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
             f"command: python3.12 ollama_unload.py --model {a.model} --reason {a.reason!r}"]
    before = ps()
    idle, last = slots_idle()
    lines.append(f"resident before: {before}")
    lines.append(f"server.log: runner idle since its last prompt = {idle}; last slot line: {last!r}")
    target = next((m for m in before if m in (a.model, a.model + ":latest")), None)
    if target is None:
        lines.append(f"VERDICT: PARTIAL — {a.model} was not resident; nothing to unload")
    elif not idle:
        lines.append(f"VERDICT: FAIL — refused: the server log shows a prompt in flight; {a.model} left loaded")
    else:
        req = urllib.request.Request(OL + "/api/generate", data=json.dumps({"model": target, "keep_alive": 0}).encode(),
                                     headers={"Content-Type": "application/json"})
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.load(r)
        dt = time.perf_counter() - t0
        time.sleep(2)
        after = ps()
        lines.append(f"unload response after {dt:.2f}s: done={resp.get('done')} done_reason={resp.get('done_reason')}")
        lines.append(f"resident after: {after}")
        ok = target not in after
        lines.append(f"VERDICT: {'PASS' if ok else 'FAIL'} — {target} {'unloaded' if ok else 'still resident'} "
                     f"({a.reason})")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    prev = LOG.read_text() if LOG.exists() else ""
    LOG.write_text(prev + ("\n" if prev else "") + "\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
