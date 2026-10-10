"""Per-model LLM turn timing across every probe's logging-proxy log (tier-table evidence).

Scans evidence/opencode/**/proxy*.jsonl (one line per LLM request made by opencode) and prints, per
model: requests, wall-clock percentiles, time-to-first-byte (prefill), decode tok/s, prompt sizes.
decode tok/s = completion_tokens / (wall - ttfb); prefill tok/s = uncached prompt tokens / ttfb.
"""

from __future__ import annotations

import glob
import json
import os
import statistics
import sys


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p * (len(xs) - 1))))] if xs else float("nan")


def main() -> None:
    root = sys.argv[1]
    rows: dict[str, list[dict[str, float]]] = {}
    for path in sorted(glob.glob(os.path.join(root, "**", "proxy*.jsonl"), recursive=True)):
        for line in open(path, encoding="utf-8"):
            e = json.loads(line)
            r = e.get("request") or {}
            resp = e.get("response") or {}
            u = resp.get("usage") or {}
            if not r or not u or e.get("client_disconnected"):
                continue
            pt = int(u.get("prompt_tokens") or 0)
            cached = int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
            ct = int(u.get("completion_tokens") or 0)
            wall, ttfb = float(e.get("wall_s") or 0), float(e.get("ttfb_s") or 0)
            rows.setdefault(str(r.get("model")), []).append(
                {"wall": wall, "ttfb": ttfb, "pt": pt, "uncached": pt - cached, "ct": ct,
                 "tools": int(r.get("n_tools") or 0)}
            )
    print(f"{'model':18} {'n':>4} {'wall p50':>8} {'wall p90':>8} {'ttfb p50':>8} {'decode tok/s p50':>16} {'prefill tok/s p50':>17} {'prompt tok p50':>14} {'max prompt':>10}")
    for m, xs in sorted(rows.items()):
        dec = [x["ct"] / (x["wall"] - x["ttfb"]) for x in xs if x["ct"] >= 20 and x["wall"] - x["ttfb"] > 0.5]
        pre = [x["uncached"] / x["ttfb"] for x in xs if x["uncached"] >= 500 and x["ttfb"] > 0.2]
        print(f"{m:18} {len(xs):>4} {pct([x['wall'] for x in xs], .5):>8.1f} {pct([x['wall'] for x in xs], .9):>8.1f} "
              f"{pct([x['ttfb'] for x in xs], .5):>8.1f} {pct(dec, .5):>16.1f} {pct(pre, .5):>17.0f} "
              f"{statistics.median([x['pt'] for x in xs]):>14.0f} {max(x['pt'] for x in xs):>10}")
    print("(requests cut by a client kill are excluded; decode rate only from replies >= 20 tokens)")


if __name__ == "__main__":
    main()
