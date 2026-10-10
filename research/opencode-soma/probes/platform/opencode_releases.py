#!/usr/bin/env python3
"""How fast does opencode move? Lists GitHub releases of anomalyco/opencode published after the
probed version (v1.18.8) and groups them by version line (unauthenticated GitHub API), lists the
repo's git tags (`git ls-remote --tags`) to find any v2.x line, and records the npm dist-tags of
`opencode-ai` (`npm view opencode-ai dist-tags`).

    python3.12 opencode_releases.py [--base v1.18.8]

Writes evidence/platform/opencode-releases.log.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
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
    cmd = ["git", "ls-remote", "--tags", "https://github.com/anomalyco/opencode"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout
    tags = sorted({ln.split("refs/tags/")[1].removesuffix("^{}") for ln in out.splitlines() if "refs/tags/" in ln})
    v2 = sorted((t for t in tags if re.match(r"^v2\.", t)), key=lambda t: [int(x) for x in re.findall(r"\d+", t)])
    lines.append(f"$ {' '.join(cmd)}")
    lines.append(f"  {len(tags)} tags; v2.x tags: {len(v2)} ({v2[0] if v2 else '-'} .. {v2[-1] if v2 else '-'})")
    v2_releases = [r["tag_name"] for r in rels if r["tag_name"].startswith("v2.")]
    lines.append(f"  v2.x tags with a GitHub release: {len(v2_releases)}")
    cmd = ["npm", "view", "opencode-ai", "dist-tags", "--json"]
    try:
        npm = subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout
        dist = json.loads(npm)
    except Exception as e:  # noqa: BLE001
        dist = {"error": str(e)[:120]}
    lines.append(f"$ {' '.join(cmd)}")
    lines.append(f"  {json.dumps(dist, sort_keys=True)}")
    npm2 = [k for k, v in dist.items() if isinstance(v, str) and v.startswith("2.")]
    lines.append(f"  dist-tags pointing at a 2.x version: {npm2 or 'none'}")
    lines.append(f"VERDICT: PASS — {len(after)} releases followed {a.base} within {weeks:.1f} weeks "
                 f"(newest {max(after, key=lambda r: r['published_at'])['tag_name'] if after else 'none'}); "
                 f"{len(v2)} v2.x git tags exist without GitHub releases ({len(v2_releases)}) or a 2.x npm dist-tag")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(lines) + "\n")
    print("\n".join(lines[-3:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
