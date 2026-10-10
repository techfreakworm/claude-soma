#!/usr/bin/env python3
"""Neo4j Community operating cost on a laptop: image, cold start, memory, disk, minimal heap.

    PYTHONHASHSEED=0 $VENV/bin/python neo4j_container.py --data $OCSOMA_SCRATCH/data

Uses the same container/volume names as bench.py (ocsoma-me-neo4j / ocsoma-me-neo4j-data, bolt on
127.0.0.1:47287) and removes both at the end. The password lives in $OCSOMA_SCRATCH/neo4j.pass and
never reaches the evidence. Writes evidence/memory/neo4j-container.json and neo4j-container.log.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench as B  # noqa: E402

SCALE_FOR_LOAD = 50000
LADDER = [("512m", "256m"), ("256m", "128m"), ("256m", "64m"), ("128m", "32m"), ("96m", "16m"), ("64m", "8m")]
LADDER_QUERIES = ["Q1", "Q3", "Q4", "Q5", "Q6", "Q7a", "Q8", "W1"]


def log(fh, *a) -> None:
    print(*a, file=fh, flush=True)
    print(*a, flush=True)


def java_rss_mb() -> float | None:
    out = B.sh(["docker", "exec", B.CONTAINER, "sh", "-c",
                "for p in /proc/[0-9]*; do if grep -q java $p/cmdline 2>/dev/null; then grep VmRSS $p/status; fi; done"],
               check=False)
    kb = [int(x) for x in re.findall(r"VmRSS:\s+(\d+)", out)]
    return round(max(kb) / 1024, 1) if kb else None


def settle_mem(seconds: float = 15.0) -> dict:
    time.sleep(seconds)
    samples = [B.docker_mem_mb() for _ in range(3)]
    return {"docker_stats_mb": round(statistics.median(samples), 1), "java_vmrss_mb": java_rss_mb()}


def disk_breakdown() -> dict:
    out = B.sh(["docker", "exec", B.CONTAINER, "sh", "-c",
                "for d in /data /data/databases/neo4j /data/databases/system /data/transactions/neo4j "
                "/data/transactions/system; do du -sk $d 2>/dev/null; done"], check=False)
    return {Path(p).as_posix(): int(k) for k, p in (ln.split() for ln in out.splitlines() if ln.strip())}


def first_query_ms(entity: str) -> float:
    d = B.neo4j_driver()
    with d.session() as s:
        t0 = time.perf_counter()
        s.run("MATCH (:Entity {id: $e})<-[:MENTIONS]-(f:Fact) WHERE f.superseded_by IS NULL RETURN f.id", e=entity).values()
        ms = (time.perf_counter() - t0) * 1000
    d.close()
    return round(ms, 1)


def hub_compressed_size() -> int | None:
    tag = B.IMAGE.split(":", 1)[1]
    try:
        with urllib.request.urlopen(f"https://hub.docker.com/v2/repositories/library/neo4j/tags/{tag}", timeout=20) as r:
            d = json.load(r)
        return sum(i.get("size", 0) for i in d.get("images", []) if i.get("architecture") == "arm64") or None
    except Exception:  # noqa: BLE001 - network is optional for this field
        return None


def main() -> int:
    try:
        return run()
    finally:
        B.sh(["docker", "rm", "-f", B.CONTAINER], check=False)
        B.sh(["docker", "volume", "rm", B.VOLUME], check=False)


def run() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--cold-runs", type=int, default=3)
    a = ap.parse_args()
    ds = json.loads(Path(a.data, f"dataset-{SCALE_FOR_LOAD}.json").read_text())
    truth = json.loads(Path(a.data, f"truth-{SCALE_FOR_LOAD}.json").read_text())
    res: dict = {"image": B.IMAGE, "bolt": f"127.0.0.1:{B.BOLT_PORT}", "password": "random, kept in scratch, redacted"}
    B.EVID.mkdir(parents=True, exist_ok=True)
    with open(B.EVID / "neo4j-container.log", "w") as fh:
        log(fh, f"date_utc: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
        log(fh, "command: PYTHONHASHSEED=0 python neo4j_container.py " + " ".join(sys.argv[1:]))
        log(fh, f"docker: {B.sh(['docker', 'version', '--format', '{{.Server.Version}} ({{.Server.Os}}/{{.Server.Arch}})']).strip()}"
                f"  docker VM: {B.sh(['docker', 'info', '--format', '{{.NCPU}} cpus {{.MemTotal}} bytes']).strip()}")
        size = int(B.sh(["docker", "image", "inspect", B.IMAGE, "--format", "{{.Size}}"]).strip())
        res["image_size_mb"] = round(size / 1e6, 1)
        hub = hub_compressed_size()
        res["image_compressed_arm64_mb"] = round(hub / 1e6, 1) if hub else None
        pull = (B.EVID / "neo4j-pull.log").read_text() if (B.EVID / "neo4j-pull.log").exists() else ""
        m = re.search(r"pull_wall_seconds: ([\d.]+)", pull)
        res["pull_wall_seconds_first_pull"] = float(m.group(1)) if m else None
        log(fh, f"image: {json.dumps({k: res[k] for k in ('image_size_mb', 'image_compressed_arm64_mb', 'pull_wall_seconds_first_pull')})}")

        # 1. cold start of a brand-new container (empty volume) to bolt-ready
        fresh = []
        for i in range(a.cold_runs):
            s = B.neo4j_fresh("512m", "256m")
            fresh.append(round(s, 2))
            log(fh, f"fresh container {i + 1}: docker run -> bolt answers RETURN 1 in {s:.2f}s")
        res["cold_start_fresh_s"] = fresh
        info = B.sh(["docker", "exec", B.CONTAINER, "neo4j", "--version"], check=False).strip()
        jv = B.sh(["docker", "exec", B.CONTAINER, "sh", "-c", "java -version 2>&1 | head -1"], check=False).strip()
        res["neo4j_version"] = info
        res["java_in_image"] = jv
        log(fh, f"versions: {info} / {res['java_in_image']}")
        res["idle_empty_db_heap512_pc256"] = settle_mem()
        log(fh, f"idle, empty db (heap 512m, pagecache 256m): {json.dumps(res['idle_empty_db_heap512_pc256'])}")
        res["disk_empty_kb"] = disk_breakdown()
        log(fh, f"disk, empty db: {json.dumps(res['disk_empty_kb'])}")

        # 2. load 50k facts, measure memory + disk, then restart-with-data cold starts
        be = B.Neo4jBackend()
        t = be.load(ds)
        res["load_50k_s"] = round(t["load_s"], 2)
        res["after_load_50k"] = settle_mem(5)
        res["disk_50k_kb_before_checkpoint"] = disk_breakdown()
        log(fh, f"loaded {SCALE_FOR_LOAD} facts in {t['load_s']:.2f}s; mem {json.dumps(res['after_load_50k'])}; "
                f"disk before any checkpoint {json.dumps(res['disk_50k_kb_before_checkpoint'])} "
                f"(db.checkpoint() is not available in Community; the clean restarts below checkpoint the store)")
        restarts = []
        ent = truth["Q1"][0]["params"]["entity"]
        for i in range(a.cold_runs):
            t0 = time.perf_counter()
            B.sh(["docker", "restart", B.CONTAINER])
            B.neo4j_wait()
            ready = time.perf_counter() - t0
            fq = first_query_ms(ent)
            restarts.append({"restart_to_bolt_s": round(ready, 2), "first_query_ms": fq})
            log(fh, f"restart {i + 1} with 50k facts: docker restart -> bolt in {ready:.2f}s, first Q1 {fq} ms")
        res["restart_with_50k"] = restarts
        res["disk_50k_kb"] = disk_breakdown()
        log(fh, f"disk with 50k facts after clean restarts (checkpointed store): {json.dumps(res['disk_50k_kb'])}")
        res["idle_50k_after_restart"] = settle_mem()
        log(fh, f"idle after restart with 50k facts: {json.dumps(res['idle_50k_after_restart'])}")

        # 3. heap / pagecache ladder: does it start, load 50k and answer correctly?
        ladder = []
        for heap, pc in LADDER:
            row: dict = {"heap": heap, "pagecache": pc}
            try:
                row["start_s"] = round(B.neo4j_fresh(heap, pc), 2)
            except Exception as e:  # noqa: BLE001
                row["status"] = f"did not start: {str(e)[:200]}"
                row["container_log_tail"] = B.sh(["docker", "logs", "--tail", "15", B.CONTAINER], check=False)[-1500:]
                ladder.append(row)
                log(fh, f"ladder {heap}/{pc}: {row['status']}")
                continue
            sampler = B.MemSampler()
            sampler.start()
            try:
                be = B.Neo4jBackend()
                row["load_s"] = round(be.load(ds)["load_s"], 2)
                ok, n, times = 0, 0, {}
                for q in LADDER_QUERIES:
                    be.open()
                    ts = []
                    for inst in truth[q][:5]:
                        t0 = time.perf_counter()
                        got = B.call(be, q, inst["params"], ds["meta"]["hub_degree"])
                        ts.append((time.perf_counter() - t0) * 1000)
                        ok += B.normalize(q, got) == B.normalize(q, inst["expected"])
                        n += 1
                    be.close()
                    times[q] = round(statistics.median(ts), 2)
                row["correct"] = f"{ok}/{n}"
                row["median_ms"] = times
                row["status"] = "ok" if ok == n else "wrong answers"
            except Exception as e:  # noqa: BLE001
                row["status"] = f"failed: {type(e).__name__}: {str(e)[:300]}"
                row["container_log_tail"] = B.sh(["docker", "logs", "--tail", "15", B.CONTAINER], check=False)[-1500:]
            row["mem"] = sampler.stop()
            row["java_vmrss_mb_end"] = java_rss_mb()
            ladder.append(row)
            log(fh, f"ladder {heap}/{pc}: {json.dumps(row)}")
        res["heap_ladder_50k"] = ladder
        B.sh(["docker", "rm", "-f", B.CONTAINER], check=False)
        B.sh(["docker", "volume", "rm", B.VOLUME], check=False)
        left = B.sh(["docker", "ps", "-a", "--filter", "name=ocsoma-me-neo4j", "--format", "{{.Names}}"]).strip()
        log(fh, f"cleanup: container and volume removed (remaining matching containers: {left or 'none'})")
        (B.EVID / "neo4j-container.json").write_text(json.dumps(res, indent=1, sort_keys=True))
        working = [r for r in ladder if r.get("status") == "ok"]
        smallest = working[-1] if working else None
        verdict = "PASS" if smallest else "FAIL"
        log(fh, f"VERDICT: {verdict} — fresh start {min(fresh):.1f}-{max(fresh):.1f}s, idle "
                f"{res['idle_empty_db_heap512_pc256']['docker_stats_mb']} MB at heap 512m/pagecache 256m, "
                f"smallest config that loaded and answered 50k correctly: "
                f"{smallest['heap'] + '/' + smallest['pagecache'] if smallest else 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
