#!/usr/bin/env python3
"""The two queries where Neo4j beat SQLite at 500k facts (Q4 provenance, Q6 2-hop) re-run with the
fixes a SQLite implementation would ship, checked against the same oracle answers:

  Q4: same recursive CTEs, but CROSS JOIN pins the join order so SQLite walks the chain from the
      fact upward instead of scanning `facts` for the join with the recursive table.
  Q6: the hub filter reads a materialized entity_degree table (maintained on write) instead of
      COUNT(*)-ing every candidate's mentions per query.

    python3.12 sqlite_tuning.py --db $OCSOMA_SCRATCH/bench-500000/memory.sqlite \
        --truth $OCSOMA_SCRATCH/data/truth-500000.json --dataset $OCSOMA_SCRATCH/data/dataset-500000.json

Runs on a copy of the bench DB (rebuilt from the dataset if the copy is missing, so the W1 writes
the bench made never matter). Writes evidence/memory/sqlite-tuning-<n>.log.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench as B  # noqa: E402

Q4_BASE = """WITH RECURSIVE up(fid) AS (
    SELECT ? UNION SELECT d.parent_id FROM fact_derivation d JOIN up ON d.fact_id = up.fid),
  src(sid) AS (
    SELECT f.source_id FROM facts f JOIN up ON f.id = up.fid
    UNION SELECT s.parent_id FROM sources s JOIN src ON s.id = src.sid WHERE s.parent_id IS NOT NULL)
SELECT 'f', fid FROM up UNION ALL SELECT 's', sid FROM src"""
Q4_TUNED = """WITH RECURSIVE up(fid) AS (
    SELECT ? UNION SELECT d.parent_id FROM up CROSS JOIN fact_derivation d ON d.fact_id = up.fid),
  src(sid) AS (
    SELECT f.source_id FROM up CROSS JOIN facts f ON f.id = up.fid
    UNION SELECT s.parent_id FROM src CROSS JOIN sources s ON s.id = src.sid WHERE s.parent_id IS NOT NULL)
SELECT 'f', fid FROM up UNION ALL SELECT 's', sid FROM src"""
DEGREE = """CREATE TABLE entity_degree AS
  SELECT fe.entity_id AS entity_id, COUNT(*) AS d FROM fact_entity fe
  JOIN facts f ON f.id = fe.fact_id AND f.superseded_by IS NULL GROUP BY fe.entity_id;
CREATE UNIQUE INDEX entity_degree_pk ON entity_degree(entity_id);"""
Q6_TUNED = """WITH cand AS (
    SELECT DISTINCT fe2.entity_id AS e
    FROM fact_entity fe1 JOIN facts f ON f.id = fe1.fact_id AND f.superseded_by IS NULL
    JOIN fact_entity fe2 ON fe2.fact_id = fe1.fact_id
    WHERE fe1.entity_id = :e AND fe2.entity_id <> :e),
  rel AS (SELECT c.e FROM cand c JOIN entity_degree g ON g.entity_id = c.e WHERE g.d <= :hub)
SELECT DISTINCT fe.fact_id FROM fact_entity fe JOIN rel ON fe.entity_id = rel.e
JOIN facts f ON f.id = fe.fact_id AND f.superseded_by IS NULL ORDER BY 1"""


def timed(fn, inst, runs=31):
    ts, ok = [], 0
    for i in range(runs):
        it = inst[i % len(inst)]
        t0 = time.perf_counter()
        got = fn(it["params"])
        ts.append((time.perf_counter() - t0) * 1000)
        ok += got == it["expected"]
    warm = sorted(ts[1:])
    return {"first_ms": round(ts[0], 3), "p50_ms": round(statistics.median(warm), 3),
            "p95_ms": round(B.pct(warm, 95), 3), "correct": round(ok / runs, 4)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--truth", required=True)
    ap.add_argument("--dataset", required=True)
    a = ap.parse_args()
    truth = json.loads(Path(a.truth).read_text())
    ds_meta_n = Path(a.truth).stem.split("-")[1]
    work = Path(a.db).parent / "tuning-copy.sqlite"
    for sfx in ("", "-wal", "-shm"):
        Path(str(work) + sfx).unlink(missing_ok=True)
    ds = json.loads(Path(a.dataset).read_text())
    t0 = time.perf_counter()
    B.SqliteBackend(work).load(ds)
    build_s = time.perf_counter() - t0
    hub = ds["meta"]["hub_degree"]
    db = sqlite3.connect(work)
    lines = []

    def out(s):
        lines.append(s)
        print(s, flush=True)

    out(f"date_utc: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    out("command: python3.12 sqlite_tuning.py --db <bench db dir> --truth truth-" + ds_meta_n + ".json --dataset dataset-"
        + ds_meta_n + ".json")
    out(f"sqlite {sqlite3.sqlite_version}; fresh copy of the bench schema loaded from the dataset in {build_s:.1f}s "
        f"({len(ds['facts'])} facts)")

    def q4(sql):
        def run(p):
            rows = db.execute(sql, (p["fact"],)).fetchall()
            return {"facts": sorted(x for t, x in rows if t == "f"), "sources": sorted(x for t, x in rows if t == "s")}
        return run

    for name, sql in (("Q4 baseline (bench.py query)", Q4_BASE), ("Q4 tuned (CROSS JOIN order)", Q4_TUNED)):
        plan = [r[3] for r in db.execute("EXPLAIN QUERY PLAN " + sql, (truth["Q4"][0]["params"]["fact"],))]
        out(f"{name}: {json.dumps(timed(q4(sql), truth['Q4']))}")
        out(f"  plan: {plan}")
    base = B.SqliteBackend(work)
    base.db = db
    out(f"Q6 baseline (bench.py query, per-candidate COUNT): {json.dumps(timed(lambda p: base.q6(p['entity'], hub), truth['Q6']))}")
    t0 = time.perf_counter()
    db.executescript(DEGREE)
    out(f"entity_degree materialized in {(time.perf_counter() - t0) * 1000:.0f} ms "
        f"({db.execute('select count(*) from entity_degree').fetchone()[0]} entities); on write it is +1/-1 per mention")
    out(f"Q6 tuned (entity_degree table): "
        f"{json.dumps(timed(lambda p: [r[0] for r in db.execute(Q6_TUNED, {'e': p['entity'], 'hub': hub})], truth['Q6']))}")
    db.close()
    ok = all('"correct": 1.0' in ln for ln in lines if ln.startswith("Q"))
    out(f"VERDICT: {'PASS' if ok else 'FAIL'} — at {int(ds_meta_n):,} facts the tuned SQLite Q4/Q6 return the oracle "
        f"answers on every run (timings above)")
    p = Path(__file__).resolve().parents[2] / "evidence" / "memory" / f"sqlite-tuning-{ds_meta_n}.log"
    p.write_text("\n".join(lines) + "\n")
    for sfx in ("", "-wal", "-shm"):
        Path(str(work) + sfx).unlink(missing_ok=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
