#!/usr/bin/env python3
"""Scores secret_scrub.scrub() on every Track B input: each planted secret must be gone from the
scrubbed text (recall) and texts with no planted secret must come back unchanged (false positives).
The patterns were written knowing the planted formats, so this proves the mechanism, not coverage
of real-world credentials. Secrets are printed only as [SECRET:<name>].

    python3.12 scrub_eval.py

Writes evidence/memory/scrub-eval.log.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import curation_cases as C  # noqa: E402
from secret_scrub import scrub  # noqa: E402


def main() -> int:
    secrets = C.make_secrets()

    def fill(t: str) -> str:
        for k, v in secrets.items():
            t = t.replace("{" + k + "}", v)
        return t

    def red(t: str) -> str:
        for k, v in secrets.items():
            t = t.replace(v, f"[SECRET:{k}]")
        return t

    lines = [f"date_utc: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}", "command: python3.12 scrub_eval.py"]
    items = [(c["id"], fill(t), c.get("secrets", [])) for c in C.EXTRACTION for _, t in c["turns"]]
    items += [(c["id"], fill(c["msg"]), c.get("secrets", [])) for c in C.REMEMBER]
    items += [(c["id"], c["new"], []) for c in C.DUPLICATES + C.CONTRADICTIONS]
    caught = missed = fp = clean = 0
    for cid, raw, names in items:
        out, n = scrub(raw)
        present = [s for s in names if secrets[s] in raw]
        for s in present:
            if secrets[s] in out:
                missed += 1
                lines.append(f"MISSED {cid}: {red(raw)}")
            else:
                caught += 1
        if present:
            lines.append(f"scrubbed {cid}: {red(out)}")
        elif n:
            fp += 1
            lines.append(f"FALSE POSITIVE {cid}: {raw!r} -> {out!r}")
        else:
            clean += 1
    lines.append(f"planted secrets caught {caught}/{caught + missed}; texts without secrets left unchanged {clean}/{clean + fp}")
    corpus = Path(__import__("os").environ.get("OCSOMA_SCRATCH", Path(__import__("tempfile").gettempdir()) / "ocsoma-me")) \
        / "data" / "dataset-50000.json"
    if corpus.exists():
        import json
        texts = [f["text"] for f in json.loads(corpus.read_text())["facts"]]
        altered = [t for t in texts if scrub(t)[1]]
        lines.append(f"benchmark corpus (dataset-50000.json, no secrets planted): {len(altered)} of {len(texts)} fact "
                     f"sentences altered" + (f"; first: {altered[0]!r}" if altered else ""))
    else:
        texts, altered = [], []
    ok = missed == 0 and fp == 0 and not altered
    lines.append(f"VERDICT: {'PASS' if ok else 'FAIL'} — scrubber removed {caught}/{caught + missed} planted secrets, altered "
                 f"{fp} of {clean + fp} secret-free Track B inputs and {len(altered)} of {len(texts)} benchmark corpus "
                 f"sentences (patterns written with knowledge of the planted formats)")
    p = Path(__file__).resolve().parents[2] / "evidence" / "memory" / "scrub-eval.log"
    p.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
