#!/usr/bin/env python3
"""Neo4j fairness check at 50k facts: are the benchmark's Neo4j recall numbers what a competent Neo4j
setup gets? Recall queries only (Q1, Q3, Q7a, Q7b, Q8), every answer checked against the oracle.

  A. as first benchmarked: unique Fact.id / Entity.id / Source.id, composite (scope, visibility)
     and (scope, subject, attribute), full-text on Fact.text (bench.py now also creates the three
     indexes of B, so A drops them first), and the bench.py queries
     (all parameterized; warm runs reuse the cached plan, first run follows db.clearQueryCaches()).
  B. A plus the single-property indexes a reviewer would ask for: Fact(scope), Fact(created_at),
     Entity(name).
  C. B plus the usual Neo4j modelling for a status filter: an :Active label on facts that are not
     superseded, and UNION instead of OR for the visibility predicate.

Each variant: PROFILE of one instance per query (plan operators + db hits), then first run and 30 warm
runs. Uses the same container/port/password conventions as bench.py (OCSOMA_NEO4J_NAME, *_PORT
overridable) and removes the container and volume at the end.

    PYTHONHASHSEED=0 $VENV/bin/python neo4j_fairness.py --data $OCSOMA_SCRATCH/data

Writes evidence/memory/neo4j-fairness-50000.log.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench as B  # noqa: E402

N = 50000
QS = ["Q1", "Q3", "Q7a", "Q7b", "Q8"]
SCORE = ("f.importance * 0.5 ^ ((($now - f.created_at) / 86400.0) / CASE f.type WHEN 'user' THEN 180.0 "
         "WHEN 'feedback' THEN 120.0 WHEN 'project' THEN 30.0 ELSE 90.0 END)")
TUNED = {
    "Q1": ("MATCH (:Entity {id: $e})<-[:MENTIONS]-(f:Fact:Active) RETURN f.id AS id ORDER BY id",
           lambda p: {"e": p["entity"]}),
    "Q3": ("CALL () { MATCH (f:Fact:Active) WHERE f.scope = $lead RETURN f "
           "UNION MATCH (f:Fact:Active) WHERE f.scope = 'parent' AND f.visibility IN ['global', $lead] RETURN f } "
           f"WITH f, {SCORE} AS score RETURN f.id AS id ORDER BY score DESC, id ASC LIMIT $k",
           lambda p: {"lead": p["lead"], "now": p["now"], "k": p["k"]}),
    "Q7a": ("MATCH (f:Fact:Active) WHERE f.scope = $lead AND f.visibility = 'parent' RETURN f.id AS id ORDER BY id",
            lambda p: {"lead": p["lead"]}),
    "Q7b": ("MATCH (f:Fact:Active) WHERE f.scope = 'parent' AND f.visibility IN ['global', $lead] "
            "RETURN f.id AS id ORDER BY id", lambda p: {"lead": p["lead"]}),
    "Q8": ("CALL db.index.fulltext.queryNodes('fact_text', $ftq) YIELD node AS f "
           "WITH f WHERE f:Active AND (f.scope = $lead OR (f.scope = 'parent' AND f.visibility IN ['global', $lead])) "
           "RETURN f.id AS id ORDER BY id", lambda p: {"ftq": " ".join(f"+{t}" for t in p["terms"]), "lead": p["lead"]}),
}
BASE_CYPHER = {  # the exact bench.py text, for PROFILE
    "Q1": ("MATCH (:Entity {id: $e})<-[:MENTIONS]-(f:Fact) WHERE f.superseded_by IS NULL RETURN f.id AS id ORDER BY id",
           lambda p: {"e": p["entity"]}),
    "Q3": ("MATCH (f:Fact) WHERE f.superseded_by IS NULL AND (f.scope = $lead OR (f.scope = 'parent' AND "
           f"f.visibility IN ['global', $lead])) WITH f, {SCORE} AS score RETURN f.id AS id ORDER BY score DESC, id ASC LIMIT $k",
           lambda p: {"lead": p["lead"], "now": p["now"], "k": p["k"]}),
    "Q7a": ("MATCH (f:Fact) WHERE f.scope = $lead AND f.visibility = 'parent' AND f.superseded_by IS NULL "
            "RETURN f.id AS id ORDER BY id", lambda p: {"lead": p["lead"]}),
    "Q7b": ("MATCH (f:Fact) WHERE f.scope = 'parent' AND f.visibility IN ['global', $lead] AND f.superseded_by IS NULL "
            "RETURN f.id AS id ORDER BY id", lambda p: {"lead": p["lead"]}),
    "Q8": ("CALL db.index.fulltext.queryNodes('fact_text', $ftq) YIELD node AS f WHERE f.superseded_by IS NULL AND "
           "(f.scope = $lead OR (f.scope = 'parent' AND f.visibility IN ['global', $lead])) RETURN f.id AS id ORDER BY id",
           lambda p: {"ftq": " ".join(f"+{t}" for t in p["terms"]), "lead": p["lead"]}),
}


def walk(plan, ops: list[str]) -> int:
    ops.append(plan.get("operatorType", "?").split("@")[0])
    hits = plan.get("dbHits", 0)
    for c in plan.get("children", []):
        hits += walk(c, ops)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--runs", type=int, default=30)
    a = ap.parse_args()
    ds = json.loads(Path(a.data, f"dataset-{N}.json").read_text())
    truth = json.loads(Path(a.data, f"truth-{N}.json").read_text())
    lines: list[str] = []

    def out(s: str) -> None:
        lines.append(s)
        print(s, flush=True)

    out(f"date_utc: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    out("command: PYTHONHASHSEED=0 python neo4j_fairness.py " + " ".join(sys.argv[1:]))
    import neo4j as neo4j_pkg
    out(f"image {B.IMAGE}, driver {neo4j_pkg.__version__}, container {B.CONTAINER} (bolt 127.0.0.1:{B.BOLT_PORT}), "
        f"heap 512m / pagecache 256m, {N} facts")
    ok_all = True
    try:
        B.neo4j_fresh("512m", "256m")
        be = B.Neo4jBackend()
        be.load(ds)
        d = B.neo4j_driver()
        s = d.session()

        def schema(tag: str) -> None:
            for q in ("SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties",
                      "SHOW INDEXES YIELD name, type, labelsOrTypes, properties, state"):
                for r in s.run(q).data():
                    out(f"  [{tag}] {json.dumps(r, sort_keys=True)}")

        def profile(tag: str, cyphers: dict) -> None:
            for q in QS:
                text, mk = cyphers[q]
                inst = truth[q][0]["params"]
                summ = s.run("PROFILE " + text, mk(inst)).consume()
                ops: list[str] = []
                hits = walk(summ.profile, ops)
                out(f"  [{tag}] PROFILE {q}: db hits {hits}; operators {' > '.join(dict.fromkeys(ops))}")

        def timing(tag: str, run_q) -> dict:
            res = {}
            for q in QS:
                s.run("CALL db.clearQueryCaches()").consume()
                inst = truth[q]
                ts, ok = [], 0
                for i in range(a.runs + 1):
                    it = inst[i % len(inst)]
                    t0 = time.perf_counter()
                    got = run_q(q, it["params"])
                    ts.append((time.perf_counter() - t0) * 1000)
                    ok += B.normalize(q, got) == B.normalize(q, it["expected"])
                warm = sorted(ts[1:])
                res[q] = {"first_ms": round(ts[0], 2), "p50_ms": round(statistics.median(warm), 2),
                          "p95_ms": round(B.pct(warm, 95), 2), "correct": round(ok / (a.runs + 1), 4)}
                out(f"  [{tag}] {q}: {json.dumps(res[q])}")
            return res

        be.driver, be.session = d, s

        def run_base(q, p):
            return B.call(be, q, p, ds["meta"]["hub_degree"])

        def run_tuned(q, p):
            text, mk = TUNED[q]
            return [r[0] for r in s.run(text, mk(p)).values()]

        for idx in ("fact_scope_only", "fact_created", "entity_name"):
            s.run(f"DROP INDEX {idx} IF EXISTS").consume()
        out("== A. as first benchmarked (bench.py schema before the fairness fix: no single-property Fact(scope), "
            "Fact(created_at) or Entity(name) index)")
        schema("A")
        profile("A", BASE_CYPHER)
        ra = timing("A", run_base)
        out("== B. plus Fact(scope), Fact(created_at), Entity(name) indexes")
        for stmt in ("CREATE INDEX fact_scope_only IF NOT EXISTS FOR (f:Fact) ON (f.scope)",
                     "CREATE INDEX fact_created IF NOT EXISTS FOR (f:Fact) ON (f.created_at)",
                     "CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name)"):
            s.run(stmt).consume()
        s.run("CALL db.awaitIndexes(300)").consume()
        schema("B")
        profile("B", BASE_CYPHER)
        rb = timing("B", run_base)
        out("== C. plus :Active label on non-superseded facts and UNION for the visibility OR")
        n_active = s.run("MATCH (f:Fact) WHERE f.superseded_by IS NULL SET f:Active RETURN count(f)").single()[0]
        out(f"  [C] labelled {n_active} facts :Active (a real system would add/remove the label in the upsert)")
        profile("C", TUNED)
        rc = timing("C", run_tuned)
        s.close()
        d.close()
        ok_all = all(r["correct"] == 1.0 for res in (ra, rb, rc) for r in res.values())
        out("summary p50 ms (A as benchmarked / B +indexes / C +Active label & UNION): " + "; ".join(
            f"{q} {ra[q]['p50_ms']} / {rb[q]['p50_ms']} / {rc[q]['p50_ms']}" for q in QS))
        best = {q: min(ra[q]["p50_ms"], rb[q]["p50_ms"], rc[q]["p50_ms"]) for q in QS}
        out(f"VERDICT: {'PASS' if ok_all else 'FAIL'} — every variant matched the oracle on every run: "
            f"{ok_all}; best Neo4j p50 at 50k: " + ", ".join(f"{q} {best[q]} ms" for q in QS)
            + " (bench SQLite: Q1 2.75, Q3 1.86, Q7a 0.39, Q7b 1.12, Q8 1.34 ms)")
    finally:
        B.sh(["docker", "rm", "-f", B.CONTAINER], check=False)
        B.sh(["docker", "volume", "rm", B.VOLUME], check=False)
        p = B.EVID / f"neo4j-fairness-{N}.log"
        p.write_text("\n".join(lines) + "\n")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
