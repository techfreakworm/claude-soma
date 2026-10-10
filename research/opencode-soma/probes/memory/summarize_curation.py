#!/usr/bin/env python3
"""Render evidence/memory/curation-results.json as the model-tier table in MEMORY-DESIGN.md 6.5.

    python3.12 summarize_curation.py
"""
from __future__ import annotations

import json
from pathlib import Path

RES = Path(__file__).resolve().parents[2] / "evidence" / "memory" / "curation-results.json"


def cell(d: dict | None, fmt) -> str:
    return "n/a" if not d else fmt(d)


def main() -> None:
    allres = json.loads(RES.read_text())
    print("| run | extract P / R | extract leaks / forbidden | dup acc (false merges) | supersede P / R "
          "(false supersedes / keep decisions) | remember acc (secret leaks / flagged) | compute p50 s |")
    print("|---|---|---|---|---|---|---|")
    for tag in sorted(allres):
        r = allres[tag]
        ex, du, co, re_ = r.get("extract"), r.get("dup"), r.get("con"), r.get("rem")
        p50 = [x["compute_p50_s"] for x in (ex, du, co, re_) if x and "compute_p50_s" in x]
        sub = f" (cases: {', '.join(r['cases_filter'])})" if isinstance(r.get("cases_filter"), list) else ""
        print(f"| {r['model']} think={r['think']}{' +prescrub' if r.get('prescrub') else ''}{sub} | "
              + cell(ex, lambda d: f"{d['precision']} / {d['recall']} ({d['tp']}/{d['planted_facts']})") + " | "
              + cell(ex, lambda d: f"{d['secret_leaks']} / {d['forbidden_stored']}") + " | "
              + cell(du, lambda d: f"{d['accuracy']} ({d['false_merges']})") + " | "
              + cell(co, lambda d: f"{d['precision']} / {d['recall']} ({d['false_supersedes']} / {d['keep_decisions_total']})")
              + " | "
              + cell(re_, lambda d: f"{d['accuracy']} ({d['secret_leaks']} / {d['secret_flagged']} of {d['secret_cases']})")
              + f" | {', '.join(str(x) for x in p50) or 'n/a'} |")


if __name__ == "__main__":
    main()
