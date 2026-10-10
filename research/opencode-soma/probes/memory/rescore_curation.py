#!/usr/bin/env python3
"""Rescore the extraction task of finished Track B runs with the current scoring rules
(curation_cases.py, revision 2: whole-token keys, stale values forbidden, small talk as plain false
positives) using each run's saved raw model outputs (curation-<tag>.jsonl). Secrets in those files
are already redacted to [SECRET:<name>], so a leak is detected by its marker. The original run logs
are left as they were; this writes evidence/memory/curation-rescore.log and adds "extract_rev2" (and
a revised danger total) to each run in curation-results.json.

    python3.12 rescore_curation.py [tag ...]      # default: every run with an extract section
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import curate_bench as CB  # noqa: E402
import curation_cases as C  # noqa: E402

EVID = CB.EVID


def main() -> int:
    res_path = EVID / "curation-results.json"
    allres = json.loads(res_path.read_text())
    tags = sys.argv[1:] or [t for t, r in sorted(allres.items()) if isinstance(r.get("extract"), dict)]
    markers = {name: f"[SECRET:{name}]" for name in C.make_secrets()}
    cases = {c["id"]: c for c in C.EXTRACTION}
    lines = [f"date_utc: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
             "command: python3.12 rescore_curation.py " + " ".join(sys.argv[1:]),
             "rules: curation_cases.py scoring revision 2 (see its docstring); raw outputs from curation-<tag>.jsonl"]
    for tag in tags:
        jl = EVID / f"curation-{tag}.jsonl"
        rows = [json.loads(x) for x in jl.read_text().splitlines() if x.strip()] if jl.exists() else []
        ext = [r for r in rows if r["task"] == "extract"]
        if not ext:
            lines.append(f"== {tag}: no extract rows in {jl.name}; skipped")
            continue
        tp = fp = planted = forbidden = leaks = 0
        lines.append(f"== {tag}: {len(ext)} extraction cases rescored")
        for r in ext:
            case = cases[r["id"]]
            try:
                parsed = json.loads(r["raw"]) if r.get("raw") else None
            except json.JSONDecodeError:
                parsed = None
            s = CB.score_extraction(case, {"parsed": parsed}, markers)
            old = r["score"]
            tp += s["tp"]
            fp += s["fp"]
            planted += s["planted"]
            forbidden += s["forbidden"]
            leaks += s["leaks"]
            if (old.get("tp"), old.get("fp"), old.get("forbidden"), old.get("leaks")) != \
                    (s["tp"], s["fp"], s["forbidden"], s["leaks"]):
                lines.append(f"  {r['id']}: rev1 tp/fp/forbidden/leaks {old.get('tp')}/{old.get('fp')}/"
                             f"{old.get('forbidden')}/{old.get('leaks')} -> rev2 {s['tp']}/{s['fp']}/{s['forbidden']}/{s['leaks']}")
        p_, r_ = CB.pr(tp, fp, planted - tp)
        rev2 = {"tp": tp, "fp": fp, "planted_facts": planted, "precision": p_, "recall": r_,
                "forbidden_stored": forbidden, "secret_leaks": leaks, "cases": len(ext)}
        lines.append(f"  rev2 summary: {json.dumps(rev2)}")
        summ = allres.get(tag, {})
        summ["extract_rev2"] = rev2
        danger = dict(summ.get("danger", {}))
        danger["extract_secret_leaks"] = leaks
        danger["extract_forbidden_stored"] = forbidden
        summ["danger_rev2"] = danger
        summ["danger_total_rev2"] = sum(danger.values())
        allres[tag] = summ
        lines.append(f"  danger (rev2): {json.dumps(danger)} total {summ['danger_total_rev2']}")
    res_path.write_text(json.dumps(allres, indent=1, sort_keys=True))
    n = sum(1 for ln in lines if ln.startswith("== ") and "skipped" not in ln)
    lines.append(f"VERDICT: PASS — rescored the extraction task of {n} run(s) with scoring revision 2; per-case "
                 "changes and revised danger totals above")
    (EVID / "curation-rescore.log").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
