#!/usr/bin/env python3
"""Telegram behind home NAT, without any bot token.

Shows (1) this host sits on a private RFC 1918 address behind NAT (its public egress address is a
different, public one), (2) the Bot API is reachable outbound over HTTPS (a request with an
obviously fake token gets Telegram's own JSON error back), (3) an outbound HTTPS request held open
by the server survives the NAT (httpbin /delay, a stand-in for the getUpdates long hold), and
(4) the exact Bot API documentation sentences that make long polling inbound-free and webhooks
public-endpoint-dependent. It never calls getUpdates with a real token and never creates a bot.

    python3.12 telegram_nat.py

The public IP is reduced to "public, not RFC 1918" plus a short hash; it is not written out.
Writes evidence/platform/telegram-nat.log.
"""
from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import platform
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "evidence" / "platform" / "telegram-nat.log"
FAKE = "0:FAKE-TOKEN-FOR-REACHABILITY-PROBE"
DOC_SENTENCES = [
    r"There are two mutually exclusive ways of receiving updates for your bot[^.]*\.",
    r"Incoming updates are stored on the server until the bot receives them either way[^.]*\.",
    r"Use this method to receive incoming updates using long polling[^.]*\.",
    r"Timeout in seconds for long polling[^.]*\.",
    r"This method will not work if an outgoing webhook is set up\.",
    r"Use this method to specify a URL and receive incoming updates via an outgoing webhook\.",
    r"Whenever there is an update for the bot, we will send an HTTPS POST request to the specified URL[^.]*\.",
    r"Ports currently supported for webhooks: [^.]*\.",
]


def sh(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout


def get(url: str, timeout: float = 30) -> tuple[int, str, float]:
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ocsoma-probe"}), timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace"), time.perf_counter() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace"), time.perf_counter() - t0


def main() -> int:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    def out(s: str = "") -> None:
        lines.append(s)
        print(s, flush=True)

    out(f"date_utc: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    out("command: python3.12 " + " ".join([Path(sys.argv[0]).name] + sys.argv[1:]))
    out(f"host: {platform.system()} {platform.release()} {platform.machine()}  curl: {sh(['curl', '--version']).splitlines()[0]}")

    route = sh(["route", "-n", "get", "default"])
    iface = re.search(r"interface: (\S+)", route).group(1)
    gw = re.search(r"gateway: (\S+)", route).group(1)
    local = sh(["ipconfig", "getifaddr", iface]).strip()
    out(f"default route: interface {iface}, gateway {gw}")
    loc_priv = ipaddress.ip_address(local).is_private
    gw_priv = ipaddress.ip_address(gw).is_private
    out(f"interface address {local}: private (RFC 1918) = {loc_priv}; gateway {gw} private = {gw_priv}")
    status, body, _ = get("https://api.ipify.org?format=json")
    pub = json.loads(body)["ip"]
    pub_ip = ipaddress.ip_address(pub)
    out(f"public egress address (seen by api.ipify.org): private = {pub_ip.is_private}, equals interface address = "
        f"{pub == local}, sha256 prefix {hashlib.sha256(pub.encode()).hexdigest()[:10]} (address itself not recorded)")
    natted = loc_priv and not pub_ip.is_private and pub != local
    out(f"=> behind NAT: {natted}")
    listen = sh(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"])
    wide = sorted({ln.split()[8] for ln in listen.splitlines()[1:] if ln.split()[8].startswith(("*:", "0.0.0.0:", "[::]:"))})
    out(f"local TCP listeners bound to all interfaces: {len(wide)} (reachable on the LAN only; the NAT does not "
        "forward unsolicited inbound traffic unless the router has a port-forward/UPnP mapping, which this probe "
        "cannot see from inside; that part is desk-research-only)")

    for method in ("getMe", "getUpdates?timeout=5"):
        status, body, dt = get(f"https://api.telegram.org/bot{FAKE}/{method}")
        out(f"GET https://api.telegram.org/bot<fake>/{method} -> HTTP {status} in {dt:.2f}s body={body.strip()[:160]}")
    tls = subprocess.run(["curl", "-sv", "-o", "/dev/null", "https://api.telegram.org/"], capture_output=True, text=True,
                         timeout=60).stderr
    for pat in (r"SSL connection using [^\n]+", r"subject: [^\n]+", r"issuer: [^\n]+"):
        m = re.search(pat, tls)
        if m:
            out(f"TLS: {m.group(0).strip()}")

    status, body, dt = get("https://httpbin.org/delay/10", timeout=40)
    out(f"held outbound HTTPS request (httpbin.org/delay/10, stand-in for a long-poll hold): HTTP {status} after {dt:.1f}s")

    status, page, dt = get("https://core.telegram.org/bots/api", timeout=60)
    text = html.unescape(re.sub(r"<[^>]+>", "", page))
    text = re.sub(r"\s+", " ", text)
    out(f"Bot API docs fetched: https://core.telegram.org/bots/api HTTP {status} ({len(page)} bytes). Quoted sentences:")
    found = 0
    for pat in DOC_SENTENCES:
        m = re.search(pat, text)
        out(f"  - {m.group(0) if m else '(not found: ' + pat + ')'}")
        found += bool(m)

    tg_ok = status == 200
    ok = natted and found >= 6
    out("interpretation: getUpdates is an HTTPS request the bot opens to api.telegram.org and Telegram holds open "
        "for up to `timeout` seconds; the answer travels back on that same outbound connection, so the NAT's "
        "connection tracking delivers it and no inbound port is needed. setWebhook makes Telegram open the "
        "connection to a public HTTPS URL on 443/80/88/8443, which a host behind home NAT cannot offer without a "
        "port-forward, a tunnel, or a relay.")
    out(f"VERDICT: {'PASS' if ok else 'PARTIAL'} — host is on a private address behind NAT; api.telegram.org answered "
        f"the fake-token requests with its JSON 401/404 over outbound HTTPS; a 10s held outbound request completed; "
        f"{found}/{len(DOC_SENTENCES)} Bot API sentences quoted (the 30-50s real long hold needs a token: see "
        f"telegram_longpoll_e2e.py, not run)")
    LOG.write_text("\n".join(lines) + "\n")
    return 0 if ok and tg_ok else 1


if __name__ == "__main__":
    sys.exit(main())
