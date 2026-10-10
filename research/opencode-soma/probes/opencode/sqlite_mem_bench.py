"""Compact SQLite-only memory recall bench over a gen_memory.py corpus (stdlib only, in-memory).

Reproduces the SQLite backend's entity-recall query (the Q1 shape in
research/opencode-soma/probes/memory/bench.py:366) without the neo4j driver or any write under
evidence/ -- it reads --data/dataset-N.json + truth-N.json, builds an in-process SQLite db
(facts + fact_entity + FTS5 over fact text), runs the Q1 entity-recall questions, and reports
build time, per-query latency and recall/precision vs the generated oracle. The full multi-backend
matrix is memory-engineer's bench.py; this is the cheap START-HERE reproduction.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import time
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--n-facts", type=int, required=True)
    a = ap.parse_args()
    data = Path(a.data)
    ds = json.loads((data / f"dataset-{a.n_facts}.json").read_text())
    truth = json.loads((data / f"truth-{a.n_facts}.json").read_text())

    t0 = time.perf_counter()
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE facts(id TEXT PRIMARY KEY, text TEXT, scope TEXT)")
    db.execute("CREATE TABLE fact_entity(fact_id TEXT, entity_id TEXT)")
    db.execute("CREATE TABLE fact_supersedes(fact_id TEXT, old_id TEXT)")
    db.execute("CREATE VIRTUAL TABLE facts_fts USING fts5(text, content='facts', content_rowid='rowid')")
    db.executemany(
        "INSERT INTO facts(id, text, scope) VALUES (?,?,?)",
        [(f["id"], f.get("description", ""), f.get("scope", "")) for f in ds["facts"]],
    )
    db.executemany(
        "INSERT INTO fact_entity VALUES (?,?)",
        [(f["id"], e) for f in ds["facts"] for e in f.get("entities", [])],
    )
    db.executemany(
        "INSERT INTO fact_supersedes VALUES (?,?)",
        [(f["id"], o) for f in ds["facts"] for o in f.get("supersedes", [])],
    )
    db.execute("INSERT INTO facts_fts(rowid, text) SELECT rowid, text FROM facts")
    db.commit()
    build_ms = (time.perf_counter() - t0) * 1000

    # Q1: recall every fact tied to an entity (bench.py's SqliteBackend entity query).
    q1 = truth.get("Q1", [])
    lat, recalls, precisions = [], [], []
    for q in q1:
        ent = q["params"]["entity"]
        expected = set(q["expected"])
        tq = time.perf_counter()
        got = {r[0] for r in db.execute(
            "SELECT f.id FROM fact_entity fe JOIN facts f ON f.id = fe.fact_id "
            "WHERE fe.entity_id = ? AND f.id NOT IN (SELECT old_id FROM fact_supersedes)",
            (ent,),
        )}
        lat.append((time.perf_counter() - tq) * 1000)
        if expected:
            recalls.append(len(got & expected) / len(expected))
        if got:
            precisions.append(len(got & expected) / len(got))

    n = len(q1)
    print(f"sqlite-only memory bench: n_facts={len(ds['facts'])} entities={len(ds['entities'])}")
    print(f"  build (facts + fact_entity + FTS5, in-memory): {build_ms:.0f} ms")
    print(f"  Q1 entity-recall queries: {n}")
    if lat:
        lat.sort()
        print(f"  query latency ms: p50={lat[len(lat)//2]:.3f} p95={lat[int(len(lat)*0.95)]:.3f} max={lat[-1]:.3f}")
    rec = statistics.mean(recalls) if recalls else float("nan")
    prec = statistics.mean(precisions) if precisions else float("nan")
    print(f"  recall={rec:.3f} precision={prec:.3f} vs the generated oracle")
    ok = n >= 1 and rec >= 0.999 and prec >= 0.999
    print(f"VERDICT: {'PASS' if ok else 'FAIL'} - SQLite entity recall at n={len(ds['facts'])} "
          f"is exact (recall={rec:.3f} precision={prec:.3f}); full multi-backend matrix is in evidence/memory/bench-results.json")


if __name__ == "__main__":
    main()
