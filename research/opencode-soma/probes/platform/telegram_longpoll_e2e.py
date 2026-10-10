#!/usr/bin/env python3
"""Operator-run end-to-end check that a bot behind home NAT receives Telegram updates by long
polling, with no inbound port, no webhook and no public URL. NOT run by the research team.

Use a THROWAWAY bot made for this test (BotFather /newbot), never the live claude-soma bot:
only one getUpdates consumer may hold a token, so running this with the live token would steal
its updates (409 Conflict for the real poller, see docs/KNOWN_BUGS.md #1).

    TELEGRAM_THROWAWAY_TOKEN=123:abc python3.12 telegram_longpoll_e2e.py \
        --expect-username my_throwaway_test_bot --minutes 5

What it does:
  1. getMe and refuses to continue unless the bot username equals --expect-username.
  2. getWebhookInfo and refuses to continue if a webhook URL is set (it never deletes one).
  3. Loops getUpdates(timeout=50) for --minutes: logs how long each request was held open, every
     update received, and replies "pong <n>" to each text message (send /ping from your phone).
  4. Prints a summary and a VERDICT line. Pass --log PATH to also write the transcript to a file.

Expected on a NAT'd laptop: idle requests are held ~50 s and return [] (that is the long hold
surviving the NAT), a message sent from the phone arrives within ~1 s of sending, and the pong
reply arrives on the phone. Standard library only; the token is never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.telegram.org/bot{token}/{method}"


def call(token: str, method: str, params: dict | None = None, timeout: float = 70) -> dict:
    data = urllib.parse.urlencode(params or {}).encode()
    req = urllib.request.Request(API.format(token=token, method=method), data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode() or "{}") | {"http_status": e.code}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect-username", required=True, help="username of the throwaway bot (without @)")
    ap.add_argument("--minutes", type=float, default=5.0)
    ap.add_argument("--poll-timeout", type=int, default=50)
    ap.add_argument("--log")
    a = ap.parse_args()
    token = os.environ.get("TELEGRAM_THROWAWAY_TOKEN", "")
    if not token:
        print("set TELEGRAM_THROWAWAY_TOKEN to a throwaway bot token", file=sys.stderr)
        return 2
    lines: list[str] = []

    def out(s: str) -> None:
        s = s.replace(token, "<token>")
        lines.append(s)
        print(s, flush=True)

    out(f"date_utc: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    out(f"command: python3.12 telegram_longpoll_e2e.py --expect-username {a.expect_username} --minutes {a.minutes}")
    me = call(token, "getMe")
    if not me.get("ok"):
        out(f"getMe failed: {me}")
        return 1
    if me["result"].get("username") != a.expect_username:
        out(f"refusing: token belongs to @{me['result'].get('username')}, expected @{a.expect_username}")
        return 1
    hook = call(token, "getWebhookInfo")
    if hook.get("result", {}).get("url"):
        out("refusing: this bot has a webhook set; long polling would fail and this script never deletes webhooks")
        return 1
    out(f"bot @{a.expect_username} ok, no webhook; polling for {a.minutes} min with timeout={a.poll_timeout}s")
    offset, holds, received, replied, errors = 0, [], 0, 0, 0
    deadline = time.time() + a.minutes * 60
    while time.time() < deadline:
        t0 = time.perf_counter()
        r = call(token, "getUpdates", {"offset": offset, "timeout": a.poll_timeout,
                                        "allowed_updates": json.dumps(["message"])}, timeout=a.poll_timeout + 20)
        held = time.perf_counter() - t0
        if not r.get("ok"):
            errors += 1
            out(f"getUpdates error after {held:.1f}s: {r}")
            time.sleep(2)
            continue
        holds.append(held)
        out(f"getUpdates returned {len(r['result'])} update(s) after being held {held:.1f}s")
        for u in r["result"]:
            offset = u["update_id"] + 1
            msg = u.get("message") or {}
            received += 1
            lag = time.time() - msg.get("date", time.time())
            out(f"  update {u['update_id']}: text={msg.get('text', '')[:60]!r} (sent {lag:.1f}s ago by Telegram's clock)")
            if msg.get("text"):
                s = call(token, "sendMessage", {"chat_id": msg["chat"]["id"], "text": f"pong {received}"})
                replied += bool(s.get("ok"))
    idle = [h for h in holds if h > a.poll_timeout - 5]
    out(f"summary: {len(holds)} polls, {len(idle)} held >= {a.poll_timeout - 5}s, max hold {max(holds or [0]):.1f}s, "
        f"{received} updates received, {replied} replies sent, {errors} errors")
    ok = bool(idle) and errors == 0 and (received == 0 or replied == received)
    out(f"VERDICT: {'PASS' if ok and received else ('PARTIAL' if ok else 'FAIL')} — long polling from behind NAT: "
        f"{len(idle)} idle holds of ~{a.poll_timeout}s survived, {received} updates received, {replied} replies")
    if a.log:
        with open(a.log, "w") as fh:
            fh.write("\n".join(lines) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
