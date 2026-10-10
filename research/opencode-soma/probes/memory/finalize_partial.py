#!/usr/bin/env python3
"""Close out a curate_bench.py run that was stopped at the end of its ollama slot: summarize the
cases it completed (from its .jsonl and per-case log lines), compare them with the same cases in a
reference run, append the summary and a PARTIAL verdict to its log, and record it in
curation-results.json with "partial": true.

    python3.12 finalize_partial.py --tag qwen3_32b-think-true-subset --ref qwen3_32b-think-false \
        --planned D05,D08,D10,D12,D14,C02,C04,C10,C16,C22 --note "stopped at 07:09Z, slot end"
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path

EVID = Path(__file__).resolve().parents[2] / "evidence" / "memory"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--planned", required=True)
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    log = EVID / f"curation-{a.tag}.log"
    text = log.read_text()
    case_re = re.compile(r"^(\w+) (\w+): (?:label=\S+ )?(\{.*\}) wall=[\d.]+s compute=[\d.]+s$", re.M)
    rows = [{"task": m.group(1), "id": m.group(2), "score": json.loads(m.group(3))} for m in case_re.finditer(text)]
    ref_text = (EVID / f"curation-{a.ref}.log").read_text()
    ref = {m.group(2): {"score": json.loads(m.group(3))} for m in case_re.finditer(ref_text)}
    compute = {m.group(1): float(m.group(2)) for m in re.finditer(r"^\w+ (\w+): .*compute=([\d.]+)s", text, re.M)}
    ref_log = (EVID / f"curation-{a.ref}.log").read_text()
    ref_compute = {m.group(1): float(m.group(2)) for m in re.finditer(r"^\w+ (\w+): .*compute=([\d.]+)s", ref_log, re.M)}
    done = [r["id"] for r in rows]
    planned = a.planned.split(",")
    lines = ["", f"run stopped before completion ({a.note}); completed {len(done)} of {len(planned)} planned cases: {', '.join(done)};"
             f" not run: {', '.join(c for c in planned if c not in done) or 'none'}",
             "per case (this run vs the reference run on the same case):"]
    danger = danger_ref = correct = correct_ref = 0
    for r in rows:
        s, rr = r["score"], ref[r["id"]]["score"]
        bad = s.get("false_merge") or s.get("fp", 0) > 0
        bad_ref = rr.get("false_merge") or rr.get("fp", 0) > 0
        danger += bool(bad)
        danger_ref += bool(bad_ref)
        correct += bool(s.get("correct"))
        correct_ref += bool(rr.get("correct"))
        lines.append(f"  {r['id']}: {a.tag} correct={s.get('correct')} pred={s.get('pred')} compute={compute.get(r['id'])}s | "
                     f"{a.ref} correct={rr.get('correct')} pred={rr.get('pred')} compute={ref_compute.get(r['id'])}s")
    c_this = [compute[i] for i in done if i in compute]
    c_ref = [ref_compute[i] for i in done if i in ref_compute]
    summ = {"partial": True, "note": a.note, "completed_cases": done, "planned_cases": planned,
            "correct": correct, "correct_ref_same_cases": correct_ref,
            "destructive": danger, "destructive_ref_same_cases": danger_ref,
            "compute_median_s": round(statistics.median(c_this), 2) if c_this else None,
            "compute_median_ref_s": round(statistics.median(c_ref), 2) if c_ref else None}
    lines.append("summary: " + json.dumps(summ))
    lines.append(f"VERDICT: PARTIAL — stopped at slot end after {len(done)}/{len(planned)} cases: {correct}/{len(done)} correct "
                 f"with thinking vs {correct_ref}/{len(done)} without on the same cases; destructive calls {danger} vs "
                 f"{danger_ref}; median compute {summ['compute_median_s']} s vs {summ['compute_median_ref_s']} s")
    log.write_text(text.rstrip("\n") + "\n" + "\n".join(lines) + "\n")
    res = EVID / "curation-results.json"
    allres = json.loads(res.read_text())
    meta = re.search(r"^ollama: (\S+)\s+model: (\S+)", text, re.M)
    allres[a.tag] = {"model": meta.group(2) if meta else "?", "think": "true", "ollama": meta.group(1) if meta else "?",
                     "cases_filter": done, **summ}
    res.write_text(json.dumps(allres, indent=1, sort_keys=True))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
