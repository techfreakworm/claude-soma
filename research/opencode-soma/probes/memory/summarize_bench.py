#!/usr/bin/env python3
"""Render evidence/memory/bench-results.json as the markdown tables used in MEMORY-DESIGN.md.

    python3.12 summarize_bench.py [--scales 600,5000,50000,500000]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

RES = Path(__file__).resolve().parents[2] / "evidence" / "memory" / "bench-results.json"
QUERIES = ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7a", "Q7b", "Q8", "W1"]
LABEL = {"Q1": "Q1 recall by entity", "Q2": "Q2 cross-project links", "Q3": "Q3 recency/decay top-10",
         "Q4": "Q4 provenance chain", "Q5": "Q5 unresolved contradictions", "Q6": "Q6 2-hop expansion",
         "Q7a": "Q7a parent view of a lead", "Q7b": "Q7b lead view of parent", "Q8": "Q8 keyword recall (FTS)",
         "W1": "W1 upsert with dedupe/supersede"}


def fmt(ms: float) -> str:
    if ms >= 100:
        return f"{ms:,.0f}"
    if ms >= 10:
        return f"{ms:.1f}"
    return f"{ms:.2f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scales", default="")
    a = ap.parse_args()
    d = json.loads(RES.read_text())
    rows = d["rows"]
    scales = [int(x) for x in a.scales.split(",")] if a.scales else sorted({r["n_facts"] for r in rows})
    for n in scales:
        bes = [b for b in ("files", "sqlite", "neo4j") if any(r["n_facts"] == n and r["backend"] == b for r in rows)]
        print(f"\n#### {n:,} facts: p50 / p95 ms (first run ms), correct\n")
        print("| query | " + " | ".join(bes) + " |")
        print("|---|" + "---|" * len(bes))
        for q in QUERIES:
            cells = []
            for b in bes:
                r = next((x for x in rows if x["n_facts"] == n and x["backend"] == b and x["query"] == q), None)
                cells.append("n/a" if r is None else
                             f"{fmt(r['p50_ms'])} / {fmt(r['p95_ms'])} ({fmt(r['first_ms'])}){'' if r['correct'] == 1.0 else ' WRONG ' + str(r['correct'])}")
            print(f"| {LABEL[q]} | " + " | ".join(cells) + " |")
        nv = next((x for x in rows if x["n_facts"] == n and x["backend"] == "naive"), None)
        if nv:
            print(f"\nnaive grep of the entity name (Claude Code layout without entity metadata), Q1: "
                  f"exact {nv['correct']:.0%}, precision {nv['mean_precision']:.2f}, recall {nv['mean_recall']:.2f}, "
                  f"p50 {fmt(nv['p50_ms'])} ms")
        ld = d.get("loads", {}).get(str(n), {})
        if ld:
            print("\nload: " + "; ".join(f"{b} {v.get('load_s')}s, {v.get('disk_kb', 0) / 1024:.1f} MB on disk"
                                         for b, v in ld.items() if b != "naive"))
    print("\nlines of code per query (non-blank, non-comment, from bench.py):\n")
    loc = d["loc"]
    print("| query | files | sqlite | neo4j |")
    print("|---|---|---|---|")
    for q in QUERIES:
        print(f"| {q} | " + " | ".join(str(loc[b]["per_query"].get(q, "")) for b in ("files", "sqlite", "neo4j")) + " |")
    print("| load + helpers | " + " | ".join(str(loc[b]["load_and_helpers"]) for b in ("files", "sqlite", "neo4j")) + " |")
    print("| total | " + " | ".join(str(sum(loc[b]["per_query"].values()) + loc[b]["load_and_helpers"])
                                   for b in ("files", "sqlite", "neo4j")) + " |")


if __name__ == "__main__":
    main()
