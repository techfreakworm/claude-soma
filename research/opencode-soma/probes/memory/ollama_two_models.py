#!/usr/bin/env python3
"""Can a small curator model run on the same ollama while the agent's big model is busy?

Keeps qwen3:32b busy with one long generation (num_predict 700), and while it runs sends a tiny
request to lfm2.5-thinking, polling /api/ps every 5 s to see whether a second runner appears and
whether the small request is answered before the big one finishes. Records the server's
OLLAMA_NUM_PARALLEL / OLLAMA_MAX_LOADED_MODELS from ~/.ollama/logs/server.log (read-only).
Never pulls, creates, removes or restarts anything.

    python3.12 ollama_two_models.py [--big qwen3:32b] [--small lfm2.5-thinking]

Writes evidence/memory/ollama-two-models.log.
"""
from __future__ import annotations

import argparse
import json
import re
import threading
import time
import urllib.request
from pathlib import Path

OL = "http://127.0.0.1:11434"
LOG = Path(__file__).resolve().parents[2] / "evidence" / "memory" / "ollama-two-models.log"


def ps() -> list[tuple[str, int | None]]:
    return [(m["name"], m.get("context_length")) for m in json.load(urllib.request.urlopen(OL + "/api/ps", timeout=5))["models"]]


def chat(model: str, prompt: str, opts: dict, timeout: float, box: dict) -> None:
    body = {"model": model, "stream": False, "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0, **opts}, "keep_alive": "5m"}
    if "qwen3" in model:
        body["think"] = False
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(OL + "/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
        box.update(ok=True, wall=time.perf_counter() - t0, done_at=time.time(), load=d.get("load_duration", 0) / 1e9,
                   eval_count=d.get("eval_count"), text=(d["message"].get("content") or "")[:60])
    except Exception as e:  # noqa: BLE001 - the timeout is the finding
        box.update(ok=False, wall=time.perf_counter() - t0, done_at=time.time(), error=str(e)[:160])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--big", default="qwen3:32b")
    ap.add_argument("--small", default="lfm2.5-thinking")
    ap.add_argument("--small-timeout", type=float, default=150)
    a = ap.parse_args()
    lines: list[str] = []

    def out(s: str) -> None:
        lines.append(s)
        print(s, flush=True)

    out(f"date_utc: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    out(f"command: python3.12 ollama_two_models.py --big {a.big} --small {a.small} --small-timeout {a.small_timeout}")
    ver = json.load(urllib.request.urlopen(OL + "/api/version", timeout=5))["version"]
    cfg = Path.home() / ".ollama" / "logs" / "server.log"
    env = {}
    if cfg.exists():
        first = cfg.open("rb").readline().decode(errors="replace")
        env = dict(re.findall(r"(OLLAMA_(?:NUM_PARALLEL|MAX_LOADED_MODELS|CONTEXT_LENGTH|KEEP_ALIVE|MAX_QUEUE)):(\S+?)(?=\s|\])", first))
    out(f"ollama {ver}; server config from server.log: {env}")
    out(f"resident at start: {ps()}")
    big: dict = {}
    small: dict = {}
    tb = threading.Thread(target=chat, args=(a.big, "Write the numbers from 1 to 400 separated by spaces.",
                                             {"num_predict": 700}, 900, big))
    t0 = time.time()
    tb.start()
    time.sleep(8)
    out(f"t+8s: big request in flight; resident: {ps()}")
    ts = threading.Thread(target=chat, args=(a.small, "Say OK.", {"num_predict": 64}, a.small_timeout, small))
    ts_start = time.time()
    ts.start()
    while ts.is_alive() or tb.is_alive():
        time.sleep(5)
        out(f"t+{time.time() - t0:.0f}s: small {'done' if not ts.is_alive() else 'waiting'}, big "
            f"{'done' if not tb.is_alive() else 'running'}; resident: {ps()}")
        if not ts.is_alive() and not tb.is_alive():
            break
        if time.time() - t0 > 900:
            break
    out(f"big {a.big}: {json.dumps({k: (round(v, 2) if isinstance(v, float) else v) for k, v in big.items() if k != 'done_at'})}")
    out(f"small {a.small}: {json.dumps({k: (round(v, 2) if isinstance(v, float) else v) for k, v in small.items() if k != 'done_at'})}")
    served_concurrently = small.get("ok") and big.get("done_at") and small["done_at"] < big["done_at"]
    if served_concurrently:
        verdict = (f"PASS — {a.small} was answered in {small['wall']:.1f}s while {a.big} was still generating: "
                   "a small curator model can run beside the busy agent model")
    elif small.get("ok"):
        waited = small["done_at"] - ts_start
        verdict = (f"FAIL — {a.small} was only answered after {a.big} finished ({waited:.0f}s after it was sent): "
                   "requests for a second model serialize behind the busy one on this ollama config")
    else:
        verdict = (f"FAIL — {a.small} got no answer within {a.small_timeout:.0f}s while {a.big} was busy "
                   f"({small.get('error')}); a curator model cannot run beside the busy agent model on this ollama config")
    out(f"VERDICT: {verdict}")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
