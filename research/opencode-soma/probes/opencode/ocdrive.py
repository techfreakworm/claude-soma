"""Tiny stdlib driver for the opencode 1.18.8 HTTP API (v1 routes), used by the probes.

  ocdrive.py --base URL new [--title T] [--agent A] [--parent SID]
  ocdrive.py --base URL turn --session SID --text "..." [--agent A] [--model prov/model]
             [--perm once|always|reject|none] [--timeout S]
  ocdrive.py --base URL msgs --session SID
  ocdrive.py --base URL children --session SID
Basic auth password (if any) comes from env OCDRIVE_PASSWORD (user OCDRIVE_USER, default opencode).
`turn` posts prompt_async, answers pending permission requests of the session tree per --perm,
waits until the session is idle with a completed assistant reply, and prints one JSON summary.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        pw = os.environ.get("OCDRIVE_PASSWORD")
        self.auth = None
        if pw:
            user = os.environ.get("OCDRIVE_USER", "opencode")
            self.auth = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()

    def req(self, method: str, path: str, body: Any = None, timeout: float = 30) -> Any:
        data = None if body is None else json.dumps(body).encode()
        r = urllib.request.Request(self.base + path, data=data, method=method)
        r.add_header("content-type", "application/json")
        if self.auth:
            r.add_header("authorization", self.auth)
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {e.read()[:500]!r}") from e


def model_obj(m: str | None) -> dict[str, str] | None:
    if not m:
        return None
    prov, _, mid = m.partition("/")
    return {"providerID": prov, "modelID": mid}


def tree(api: Api, sid: str) -> list[str]:
    out, todo = [sid], [sid]
    while todo:
        cur = todo.pop()
        for ch in api.req("GET", f"/session/{cur}/children") or []:
            out.append(ch["id"])
            todo.append(ch["id"])
    return out


def compact_msgs(api: Api, sid: str) -> list[dict[str, Any]]:
    res = []
    for m in api.req("GET", f"/session/{sid}/message") or []:
        info = m["info"]
        tools, texts = [], []
        for p in m.get("parts") or []:
            if p.get("type") == "text" and not p.get("synthetic"):
                texts.append(p.get("text", ""))
            elif p.get("type") == "text" and p.get("synthetic"):
                texts.append("[synthetic] " + p.get("text", ""))
            elif p.get("type") == "tool":
                st = p.get("state") or {}
                tools.append(
                    {
                        "tool": p.get("tool"),
                        "callID": p.get("callID"),
                        "status": st.get("status"),
                        "input": st.get("input"),
                        "output_head": (st.get("output") or st.get("error") or "")[:300],
                        "child_session": (st.get("metadata") or {}).get("sessionId")
                        or (st.get("metadata") or {}).get("sessionID"),
                        "ms": (st.get("time") or {}).get("end", 0)
                        - (st.get("time") or {}).get("start", 0)
                        if (st.get("time") or {}).get("end")
                        else None,
                    }
                )
        res.append(
            {
                "id": info["id"],
                "role": info["role"],
                "agent": info.get("agent") or info.get("mode"),
                "model": info.get("modelID") or (info.get("model") or {}).get("modelID"),
                "created": info["time"]["created"],
                "completed": info["time"].get("completed"),
                "tokens": info.get("tokens"),
                "finish": info.get("finish"),
                "error": info.get("error"),
                "text": "\n".join(texts)[:1500],
                "tools": tools,
            }
        )
    return res


def turn(api: Api, a: argparse.Namespace) -> dict[str, Any]:
    body: dict[str, Any] = {"parts": [{"type": "text", "text": a.text}]}
    if a.agent:
        body["agent"] = a.agent
    if model_obj(a.model):
        body["model"] = model_obj(a.model)
    t0 = time.time()
    api.req("POST", f"/session/{a.session}/prompt_async", body)
    perms: list[dict[str, Any]] = []
    seen_busy = False
    last_status = None
    while True:
        if time.time() - t0 > a.timeout:
            pending = api.req("GET", "/permission") or []
            return {
                "session": a.session,
                "timeout": True,
                "wall_s": round(time.time() - t0, 1),
                "last_status": last_status,
                "pending_permissions": pending,
                "permissions": perms,
                "messages": compact_msgs(api, a.session)[-4:],
            }
        sids = set(tree(api, a.session))
        for p in api.req("GET", "/permission") or []:
            if p["sessionID"] in sids and a.perm != "none" and p["id"] not in {x["id"] for x in perms}:
                reply = {"reply": a.perm}
                if a.perm == "reject":
                    reply["message"] = "rejected by the probe driver"
                api.req("POST", f"/permission/{p['id']}/reply", reply)
                perms.append({**p, "driver_reply": a.perm, "t_s": round(time.time() - t0, 1)})
            elif p["sessionID"] in sids and a.perm == "none" and p["id"] not in {x["id"] for x in perms}:
                perms.append({**p, "driver_reply": "left-pending", "t_s": round(time.time() - t0, 1)})
        st = (api.req("GET", "/session/status") or {}).get(a.session)
        last_status = st
        if st and st.get("type") in ("busy", "retry"):
            seen_busy = True
        msgs = api.req("GET", f"/session/{a.session}/message") or []
        last = msgs[-1]["info"] if msgs else None
        done = (
            (st is None or st.get("type") == "idle")
            and last is not None
            and last["role"] == "assistant"
            and last["time"].get("completed")
            and last["time"]["created"] >= int(t0 * 1000) - 2000
        )
        if done:
            break
        time.sleep(0.5)
    cm = compact_msgs(api, a.session)
    new = [m for m in cm if m["created"] >= int(t0 * 1000) - 2000]
    final = [m for m in new if m["role"] == "assistant"]
    return {
        "session": a.session,
        "timeout": False,
        "seen_busy": seen_busy,
        "wall_s": round(time.time() - t0, 1),
        "final_text": final[-1]["text"] if final else None,
        "new_messages": new,
        "permissions": perms,
        "children": [s for s in tree(api, a.session) if s != a.session],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("--title")
    n.add_argument("--agent")
    n.add_argument("--parent")
    t = sub.add_parser("turn")
    t.add_argument("--session", required=True)
    t.add_argument("--text", required=True)
    t.add_argument("--agent")
    t.add_argument("--model")
    t.add_argument("--perm", default="none", choices=["once", "always", "reject", "none"])
    t.add_argument("--timeout", type=float, default=600)
    m = sub.add_parser("msgs")
    m.add_argument("--session", required=True)
    c = sub.add_parser("children")
    c.add_argument("--session", required=True)
    a = ap.parse_args()
    api = Api(a.base)
    if a.cmd == "new":
        body: dict[str, Any] = {}
        if a.title:
            body["title"] = a.title
        if a.agent:
            body["agent"] = a.agent
        if a.parent:
            body["parentID"] = a.parent
        print(api.req("POST", "/session", body)["id"])
    elif a.cmd == "turn":
        print(json.dumps(turn(api, a), indent=1))
    elif a.cmd == "msgs":
        print(json.dumps(compact_msgs(api, a.session), indent=1))
    elif a.cmd == "children":
        print(json.dumps(api.req("GET", f"/session/{a.session}/children"), indent=1))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
