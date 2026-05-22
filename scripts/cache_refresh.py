#!/usr/bin/env python3
"""Prime hot paths in the dashboard API so the first user click is fast."""
from __future__ import annotations

import sys
import urllib.request


PATHS = [
    "/api/healthz",
    "/api/public/stats",
]


def main() -> int:
    code = 0
    for p in PATHS:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:9000{p}", timeout=5) as r:
                r.read()
        except Exception as e:  # noqa: BLE001
            print(f"cache_refresh: {p} failed: {e}", file=sys.stderr)
            code = 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
