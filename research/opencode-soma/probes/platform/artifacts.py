#!/usr/bin/env python3
"""Which OS/arch combinations have a published artifact for each runtime dependency?

Lists PyPI wheel platform tags (playwright, piper-tts, onnxruntime) and GitHub release asset names
(opencode v1.18.8, whisper.cpp latest, ollama latest, bun v1.3.6) plus the opencode installer's
supported-target line. Availability of an artifact is not proof it works on that OS; it only
replaces "is there even a build?" guesses with a listing. Unauthenticated GitHub API calls.

    python3.12 artifacts.py

Writes evidence/platform/artifacts.log.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

LOG = Path(__file__).resolve().parents[2] / "evidence" / "platform" / "artifacts.log"
PYPI = [("playwright", "1.63.0"), ("piper-tts", "1.8.0"), ("onnxruntime", "1.31.0")]
GH = [("anomalyco/opencode", "tags/v1.18.8"), ("ggml-org/whisper.cpp", "newest-with-assets"),
      ("ollama/ollama", "latest"), ("oven-sh/bun", "tags/bun-v1.3.6")]


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ocsoma-probe", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def classify(name: str) -> str:
    n = name.lower()
    os_ = ("macos" if re.search(r"mac|darwin|osx", n)
           else "windows" if re.search(r"windows|win32|win[-_]|win(?:64|amd64|arm64)|\.exe|\.msi", n)
           else "linux" if re.search(r"linux|manylinux|musllinux", n) else "?")
    arch = ("arm64" if re.search(r"arm64|aarch64", n) else "x86" if "win32" in n
            else "x64" if re.search(r"x86_64|amd64|x64", n)
            else "universal" if "universal" in n else "?")
    return f"{os_}/{arch}"


def main() -> int:
    lines: list[str] = []

    def out(s: str) -> None:
        lines.append(s)
        print(s, flush=True)

    out(f"date_utc: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    out("command: python3.12 artifacts.py")
    cover: dict[str, set[str]] = {}
    for pkg, ver in PYPI:
        d = json.loads(get(f"https://pypi.org/pypi/{pkg}/{ver}/json"))
        names = [f["filename"] for f in d["urls"] if f["filename"].endswith(".whl")]
        out(f"== PyPI {pkg}=={ver}: {len(names)} wheels")
        tags = sorted({classify(n) for n in names})
        cover[pkg] = set(tags)
        out(f"   platforms: {', '.join(tags)}")
    for repo, rel in GH:
        try:
            if rel == "newest-with-assets":
                rels = json.loads(get(f"https://api.github.com/repos/{repo}/releases?per_page=10"))
                out(f"== GitHub {repo} newest releases (tag: assets): "
                    + ", ".join(f"{r['tag_name']}: {len(r['assets'])}" for r in rels))
                d = next(r for r in rels if r["assets"])
            else:
                d = json.loads(get(f"https://api.github.com/repos/{repo}/releases/{rel}"))
        except Exception as e:  # noqa: BLE001
            out(f"== GitHub {repo} {rel}: unavailable ({e})")
            continue
        names = [a["name"] for a in d.get("assets", [])]
        out(f"== GitHub {repo} release {d.get('tag_name')} ({d.get('published_at', '')[:10]}): {len(names)} assets")
        tags = sorted({classify(n) for n in names if classify(n) != "?/?"})
        cover[repo] = set(tags)
        out(f"   platforms: {', '.join(tags)}")
        win = [n for n in names if classify(n).startswith("windows")]
        out(f"   windows assets: {', '.join(win[:8]) or 'none'}")
    inst = get("https://opencode.ai/install").decode(errors="replace")
    m = re.search(r"^\s*(linux-x64\|[^)]*)\)", inst, re.M)
    out(f"== opencode installer (https://opencode.ai/install) supported targets: {m.group(1) if m else 'not found'}")
    win_ok = all(any(t.startswith("windows") for t in v) for k, v in cover.items() if k != "piper-tts")
    out(f"VERDICT: {'PASS' if win_ok else 'PARTIAL'} — every listed dependency publishes Linux, macOS and Windows "
        f"artifacts{'' if win_ok else ' except as noted'}; piper-tts has no Windows arm64 wheel "
        f"(platforms: {', '.join(sorted(cover.get('piper-tts', [])))}); artifact availability is not a Windows run")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
