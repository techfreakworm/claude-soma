#!/usr/bin/env python3
"""How fast does opencode move? Lists GitHub releases of anomalyco/opencode published after the
probed version (v1.18.8) and groups them by version line. Unauthenticated GitHub API.

    python3.12 opencode_releases.py [--base v1.18.8]

Writes evidence/platform/opencode-releases.log.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

LOG = Path(__file__).resolve().parents[2] / "evidence" / "platform" / "opencode-releases.log"


def get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "ocsoma-probe", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="v1.18.8")
    a = ap.parse_args()
    rels = []
    for page in range(1, 11):
        batch = get(f"https://api.github.com/repos/anomalyco/opencode/releases?per_page=100&page={page}")
        if not batch:
            break
        rels += batch
    lines = [f"date_utc: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
             f"command: python3.12 opencode_releases.py --base {a.base}",
             f"source: https://api.github.com/repos/anomalyco/opencode/releases ({len(rels)} releases fetched)"]
    base = next((r for r in rels if r["tag_name"] == a.base), None)
    if base is None:
        lines.append(f"VERDICT: FAIL — {a.base} not found among fetched releases")
        LOG.write_text("\n".join(lines) + "\n")
        return 1
    after = [r for r in rels if r["published_at"] > base["published_at"]]
    lines.append(f"{a.base} published {base['published_at']}")
    for r in sorted(after, key=lambda r: r["published_at"]):
        lines.append(f"  {r['tag_name']:12} {r['published_at'][:10]}  prerelease={r['prerelease']}")
    lines_by = {}
    for r in after:
        lines_by.setdefault(".".join(r["tag_name"].lstrip("v").split(".")[:2]), []).append(r["tag_name"])
    weeks = (time.mktime(time.strptime(max(r["published_at"] for r in after)[:10], "%Y-%m-%d"))
             - time.mktime(time.strptime(base["published_at"][:10], "%Y-%m-%d"))) / (7 * 86400) if after else 0
    lines.append(f"releases after {a.base}: {len(after)} over {weeks:.1f} weeks; by line: "
                 + ", ".join(f"{k}.x: {len(v)}" for k, v in sorted(lines_by.items())))
    lines.append(f"VERDICT: PASS — {len(after)} releases followed {a.base} within {weeks:.1f} weeks "
                 f"(newest {max(after, key=lambda r: r['published_at'])['tag_name'] if after else 'none'})")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(lines) + "\n")
    print("\n".join(lines[-3:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
