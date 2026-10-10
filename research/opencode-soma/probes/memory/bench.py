#!/usr/bin/env python3
"""Load one generated corpus into three memory backends and benchmark Q1-Q8 + W1 on each.

    PYTHONHASHSEED=0 $VENV/bin/python bench.py --data DIR --n-facts 600 [--runs 30] [--backends files,sqlite,neo4j]

Backends
  files   Claude Code auto-memory layout: <root>/<cwd-slug>/memory/MEMORY.md + one fact per .md
          with YAML frontmatter; queried statelessly with ripgrep + reading files, as an agent would.
  naive   Same files, but Q1 is a plain `rg -i -w <entity name>` over the text (what an agent does
          today with Claude Code memory that has no entity metadata). Only Q1 is run.
  sqlite  stdlib sqlite3, WAL: facts / entities / fact_entity / sources / fact_derivation tables,
          FTS5 over fact text, recursive CTEs for provenance.
  neo4j   Neo4j Community in docker (ocsoma-me-neo4j, bolt on 127.0.0.1:47287), python driver.

Timing: first_ms = first execution of the query shape on a fresh client (sqlite: new connection;
neo4j: new driver + db.clearQueryCaches(); files are stateless). p50/p95 over --runs warm runs that
cycle through the truth instances. correct = fraction of all runs whose answer equals the oracle's.
"""
from __future__ import annotations

import argparse
import gc
import inspect
import json
import os
import re
import secrets
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parent.parent
EVID = RESEARCH / "evidence" / "memory"
SCRATCH = Path(os.environ.get("OCSOMA_SCRATCH", Path(tempfile.gettempdir()) / "ocsoma-me"))
RG = shutil.which("rg") or "/opt/homebrew/bin/rg"
CONTAINER = os.environ.get("OCSOMA_NEO4J_NAME", "ocsoma-me-neo4j")
VOLUME = os.environ.get("OCSOMA_NEO4J_VOLUME", f"{CONTAINER}-data")
IMAGE = os.environ.get("NEO4J_IMAGE", "neo4j:2026.09.0-community")
BOLT_PORT = int(os.environ.get("OCSOMA_NEO4J_BOLT_PORT", "47287"))
HTTP_PORT = int(os.environ.get("OCSOMA_NEO4J_HTTP_PORT", "47274"))
QUERIES = ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7a", "Q7b", "Q8", "W1"]
HALF = {"user": 180.0, "feedback": 120.0, "project": 30.0, "reference": 90.0}


def sh(cmd: list[str], check: bool = True, timeout: int = 300) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=check)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"{cmd[0]} failed: {e.stderr[-500:]}") from e
    return r.stdout


def loc(fn) -> int:
    lines = inspect.getsource(fn).splitlines()[1:]
    n, in_doc = 0, False
    for ln in lines:
        t = ln.strip()
        if t.startswith('"""'):
            if not (t.endswith('"""') and len(t) > 3):
                in_doc = not in_doc
            continue
        if in_doc or not t or t.startswith("#"):
            continue
        n += 1
    return n


def du_kb(path: Path) -> int:
    return int(sh(["du", "-sk", str(path)]).split()[0])


# ============================================================================ flat files
class FilesBackend:
    name = "files"

    def __init__(self, root: Path) -> None:
        self.root = root

    # --- shared helpers (counted once as shared LOC)
    def _dir(self, scope: str) -> Path:
        return self.root / self.slugs[scope] / "memory"

    def _rg_fields(self, fields: str, paths: list[Path]) -> dict[str, dict]:
        out = sh([RG, "--no-heading", "-N", "-H", "--no-messages", "-g", "!MEMORY.md", "-e",
                  f"^({fields}): ", *[str(p) for p in paths]], check=False)
        recs: dict[str, dict] = {}
        for line in out.splitlines():
            path, _, kv = line.partition(":")
            k, _, v = kv.partition(": ")
            recs.setdefault(path, {})[k] = json.loads(v)
        return recs

    def _parse(self, path: Path) -> tuple[dict, str]:
        text = path.read_text()
        head, _, body = text[4:].partition("\n---\n")
        fm = {}
        for ln in head.split("\n"):
            k, _, v = ln.partition(": ")
            fm[k] = json.loads(v)
        return fm, body

    def _scope_of(self, path: str) -> str:
        return self.slug_to_scope[Path(path).parent.parent.name]

    # --- load
    def load(self, ds: dict) -> dict:
        t0 = time.perf_counter()
        if self.root.exists():
            shutil.rmtree(self.root)
        self.slugs = {s["id"]: s["slug"] for s in ds["scopes"]}
        self.slug_to_scope = {v: k for k, v in self.slugs.items()}
        src = {s["id"]: s for s in ds["sources"]}
        rel = {}
        for f in ds["facts"]:
            fname = f"{f['id']}-{re.sub(r'[^a-z0-9]+', '-', f['name'].lower()).strip('-')[:40]}.md"
            rel[f["id"]] = f"{self.slugs[f['scope']]}/memory/{fname}"
        index: dict[str, list[str]] = {s: [] for s in self.slugs}
        for s in self.slugs:
            self._dir(s).mkdir(parents=True, exist_ok=True)
        for f in ds["facts"]:
            chain, sid = [], f["source_id"]
            while sid:
                chain.append(sid)
                sid = src[sid]["parent_id"]
            self._write(f, rel, chain)
            if f["superseded_by"] is None:
                index[f["scope"]].append(f"- [{f['name']}]({Path(rel[f['id']]).name}) — {f['description']}")
        for s, lines in index.items():
            (self._dir(s) / "MEMORY.md").write_text("\n".join(lines) + "\n")
        self.rel = rel
        return {"load_s": time.perf_counter() - t0, "disk_kb": du_kb(self.root),
                "n_files": len(ds["facts"]) + len(self.slugs)}

    def _write(self, f: dict, rel: dict, chain: list[str]) -> None:
        fm = {"name": f["name"], "description": f["description"], "type": f["type"], "id": f["id"],
              "scope": f["scope"], "visibility": f["visibility"], "subject": f["subject"],
              "attribute": f["attribute"], "value": f["value"], "entities": f["entities"],
              "importance": f["importance"], "created": f["created_at"], "source_chain": chain,
              "derived_from": [rel[x] for x in f["derived_from"]], "supersedes": f["supersedes"],
              "superseded_by": f["superseded_by"]}
        head = "\n".join(f"{k}: {json.dumps(v)}" for k, v in fm.items())
        links = " ".join(f"[[{Path(rel[x]).stem}]]" for x in f["derived_from"])
        body = f["text"] + (f"\n\nRelated: {links}" if links else "")
        (self.root / rel[f["id"]]).write_text(f"---\n{head}\n---\n\n{body}\n")

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    # --- queries
    def q1(self, entity: str) -> list[str]:
        out = sh([RG, "-l", "--no-messages", "-g", "!MEMORY.md", "-e",
                  f'^entities: .*"{re.escape(entity)}"', str(self.root)], check=False)
        ids = []
        for p in out.split():
            fm, _ = self._parse(Path(p))
            if fm["superseded_by"] is None:
                ids.append(fm["id"])
        return sorted(ids)

    def q2(self) -> dict[str, list[str]]:
        leads = [self._dir(s) for s in self.slugs if s != "parent"]
        recs = self._rg_fields("id|entities|superseded_by", leads)
        by_e: dict[str, list[tuple[str, str]]] = {}
        for path, r in recs.items():
            if r["superseded_by"] is None:
                for e in r["entities"]:
                    by_e.setdefault(e, []).append((self._scope_of(path), r["id"]))
        return {e: sorted(i for _, i in v) for e, v in sorted(by_e.items()) if len({s for s, _ in v}) >= 2}

    def q3(self, lead: str, k: int, now: int) -> list[str]:
        recs = self._rg_fields("id|type|visibility|importance|created|superseded_by",
                               [self._dir(lead), self._dir("parent")])
        scored = []
        for path, r in recs.items():
            if r["superseded_by"] is not None:
                continue
            if self._scope_of(path) == "parent" and r["visibility"] not in ("global", lead):
                continue
            s = r["importance"] * 0.5 ** (((now - r["created"]) / 86400.0) / HALF[r["type"]])
            scored.append((-s, r["id"]))
        return [i for _, i in sorted(scored)[:k]]

    def q4(self, fact: str) -> dict[str, list[str]]:
        start = next(self.root.glob(f"*/memory/{fact}-*.md"))
        facts, sources, stack = set(), set(), [start]
        while stack:
            p = stack.pop()
            fm, _ = self._parse(p)
            if fm["id"] in facts:
                continue
            facts.add(fm["id"])
            sources.update(fm["source_chain"])
            stack.extend(self.root / x for x in fm["derived_from"])
        return {"facts": sorted(facts), "sources": sorted(sources)}

    def q5(self) -> list[list]:
        recs = self._rg_fields("id|subject|attribute|value|superseded_by", [self.root])
        groups: dict[tuple, list[tuple[str, str]]] = {}
        for path, r in recs.items():
            if r["superseded_by"] is None:
                key = (self._scope_of(path), r["subject"], r["attribute"])
                groups.setdefault(key, []).append((r["id"], r["value"]))
        return [[*k, sorted(i for i, _ in g)] for k, g in sorted(groups.items()) if len({v for _, v in g}) > 1]

    def q6(self, entity: str, hub: int) -> list[str]:
        recs = [r for r in self._rg_fields("id|entities|superseded_by", [self.root]).values()
                if r["superseded_by"] is None]
        deg: dict[str, int] = {}
        for r in recs:
            for e in r["entities"]:
                deg[e] = deg.get(e, 0) + 1
        rel = {e for r in recs if entity in r["entities"] for e in r["entities"] if e != entity and deg[e] <= hub}
        return sorted(r["id"] for r in recs if rel.intersection(r["entities"]))

    def q7a(self, lead: str) -> list[str]:
        recs = self._rg_fields("id|visibility|superseded_by", [self._dir(lead)])
        return sorted(r["id"] for r in recs.values() if r["superseded_by"] is None and r["visibility"] == "parent")

    def q7b(self, lead: str) -> list[str]:
        recs = self._rg_fields("id|visibility|superseded_by", [self._dir("parent")])
        return sorted(r["id"] for r in recs.values()
                      if r["superseded_by"] is None and r["visibility"] in ("global", lead))

    def q8(self, lead: str, terms: list[str]) -> list[str]:
        dirs = [str(self._dir(lead)), str(self._dir("parent"))]
        cand = None
        for t in terms:
            hits = set(sh([RG, "-l", "-i", "-w", "--no-messages", "-g", "!MEMORY.md", "-F", t, *dirs],
                          check=False).split())
            cand = hits if cand is None else cand & hits
        ids = []
        for p in sorted(cand or []):
            fm, body = self._parse(Path(p))
            text = body.split("\n\nRelated: ")[0]
            toks = set(re.split(r"[^a-z0-9]+", text.lower()))
            if fm["superseded_by"] is not None or not all(t in toks for t in terms):
                continue
            if fm["scope"] == lead or (fm["scope"] == "parent" and fm["visibility"] in ("global", lead)):
                ids.append(fm["id"])
        return sorted(ids)

    def w1(self, fact: dict) -> dict:
        d = self._dir(fact["scope"])
        out = sh([RG, "-l", "--no-messages", "-g", "!MEMORY.md", "-F", "-x",
                  f"attribute: {json.dumps(fact['attribute'])}", str(d)], check=False)
        live = []
        for p in out.split():
            fm, _ = self._parse(Path(p))
            if fm["subject"] == fact["subject"] and fm["superseded_by"] is None:
                live.append((Path(p), fm))
        same = sorted(fm["id"] for _, fm in live if fm["value"] == fact["value"])
        if same:
            return {"action": "skip_duplicate", "ids": same}
        fname = f"{fact['id']}-{re.sub(r'[^a-z0-9]+', '-', fact['name'].lower()).strip('-')[:40]}.md"
        self.rel[fact["id"]] = f"{self.slugs[fact['scope']]}/memory/{fname}"
        f = dict(fact, supersedes=sorted(fm["id"] for _, fm in live))
        self._write(f, self.rel, [fact["source_id"]])
        index = (d / "MEMORY.md").read_text().splitlines()
        for p, fm in live:
            p.write_text(p.read_text().replace("\nsuperseded_by: null\n", f"\nsuperseded_by: {json.dumps(fact['id'])}\n", 1))
            index = [ln for ln in index if f"]({p.name})" not in ln]
        index.append(f"- [{fact['name']}]({fname}) — {fact['description']}")
        (d / "MEMORY.md").write_text("\n".join(index) + "\n")
        if live:
            return {"action": "supersede", "ids": sorted(fm["id"] for _, fm in live)}
        return {"action": "insert", "ids": []}


class NaiveFilesBackend(FilesBackend):
    """Q1 only: grep the entity's canonical name in the text, no entity metadata."""
    name = "naive"

    def load(self, ds: dict) -> dict:
        self.slugs = {s["id"]: s["slug"] for s in ds["scopes"]}
        self.slug_to_scope = {v: k for k, v in self.slugs.items()}
        self.names = {e["id"]: e["name"] for e in ds["entities"]}
        return {"load_s": 0.0, "disk_kb": 0, "n_files": 0, "note": "reuses the files backend tree"}

    def q1(self, entity: str) -> list[str]:
        out = sh([RG, "-l", "-i", "-w", "--no-messages", "-g", "!MEMORY.md", "-F", self.names[entity],
                  str(self.root)], check=False)
        ids = []
        for p in out.split():
            fm, _ = self._parse(Path(p))
            if fm["superseded_by"] is None:
                ids.append(fm["id"])
        return sorted(ids)


# ============================================================================ sqlite
SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE sources(id TEXT PRIMARY KEY, kind TEXT NOT NULL, ref TEXT, parent_id TEXT REFERENCES sources(id), created_at INTEGER);
CREATE TABLE entities(id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, aliases TEXT, home_scope TEXT);
CREATE TABLE facts(
  id TEXT PRIMARY KEY, scope TEXT NOT NULL, type TEXT NOT NULL, visibility TEXT NOT NULL,
  subject TEXT NOT NULL REFERENCES entities(id), attribute TEXT NOT NULL, value TEXT NOT NULL,
  text TEXT NOT NULL, name TEXT, description TEXT, importance INTEGER NOT NULL, created_at INTEGER NOT NULL,
  source_id TEXT REFERENCES sources(id), superseded_by TEXT);
CREATE TABLE fact_entity(fact_id TEXT NOT NULL, entity_id TEXT NOT NULL, PRIMARY KEY(fact_id, entity_id)) WITHOUT ROWID;
CREATE TABLE fact_derivation(fact_id TEXT NOT NULL, parent_id TEXT NOT NULL, PRIMARY KEY(fact_id, parent_id)) WITHOUT ROWID;
CREATE TABLE fact_supersedes(new_id TEXT NOT NULL, old_id TEXT NOT NULL, PRIMARY KEY(new_id, old_id)) WITHOUT ROWID;
CREATE INDEX fe_entity ON fact_entity(entity_id, fact_id);
CREATE INDEX facts_scope ON facts(scope, visibility, superseded_by);
CREATE INDEX facts_key ON facts(scope, subject, attribute);
CREATE VIRTUAL TABLE facts_fts USING fts5(text, content='facts', content_rowid='rowid');
"""


class SqliteBackend:
    name = "sqlite"

    def __init__(self, path: Path) -> None:
        self.path = path
        self.db: sqlite3.Connection | None = None

    def load(self, ds: dict) -> dict:
        t0 = time.perf_counter()
        for suffix in ("", "-wal", "-shm"):
            Path(str(self.path) + suffix).unlink(missing_ok=True)
        db = sqlite3.connect(self.path)
        db.executescript(SCHEMA)
        with db:
            db.executemany("INSERT INTO sources VALUES (?,?,?,?,?)",
                           [(s["id"], s["kind"], s["ref"], s["parent_id"], s["created_at"]) for s in ds["sources"]])
            db.executemany("INSERT INTO entities VALUES (?,?,?,?,?)",
                           [(e["id"], e["name"], e["kind"], json.dumps(e["aliases"]), e["home_scope"]) for e in ds["entities"]])
            db.executemany("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           [(f["id"], f["scope"], f["type"], f["visibility"], f["subject"], f["attribute"], f["value"],
                             f["text"], f["name"], f["description"], f["importance"], f["created_at"], f["source_id"],
                             f["superseded_by"]) for f in ds["facts"]])
            db.executemany("INSERT INTO fact_entity VALUES (?,?)", [(f["id"], e) for f in ds["facts"] for e in f["entities"]])
            db.executemany("INSERT INTO fact_derivation VALUES (?,?)", [(f["id"], p) for f in ds["facts"] for p in f["derived_from"]])
            db.executemany("INSERT INTO fact_supersedes VALUES (?,?)", [(f["id"], o) for f in ds["facts"] for o in f["supersedes"]])
            db.execute("INSERT INTO facts_fts(rowid, text) SELECT rowid, text FROM facts")
        db.execute("ANALYZE")
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.close()
        size = sum(Path(str(self.path) + s).stat().st_size for s in ("", "-wal") if Path(str(self.path) + s).exists())
        return {"load_s": time.perf_counter() - t0, "disk_kb": size // 1024}

    def open(self) -> None:
        if self.db:
            self.db.close()
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA journal_mode=WAL")

    def close(self) -> None:
        if self.db:
            self.db.close()
            self.db = None

    def q1(self, entity: str) -> list[str]:
        return [r[0] for r in self.db.execute(
            "SELECT f.id FROM fact_entity fe JOIN facts f ON f.id = fe.fact_id "
            "WHERE fe.entity_id = ? AND f.superseded_by IS NULL ORDER BY f.id", (entity,))]

    def q2(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for e, fid in self.db.execute("""
            WITH m AS (SELECT fe.entity_id AS e, f.id AS fid, f.scope AS scope
                       FROM fact_entity fe JOIN facts f ON f.id = fe.fact_id
                       WHERE f.superseded_by IS NULL AND f.scope <> 'parent'),
                 x AS (SELECT e FROM m GROUP BY e HAVING COUNT(DISTINCT scope) >= 2)
            SELECT m.e, m.fid FROM m JOIN x ON x.e = m.e ORDER BY m.e, m.fid"""):
            out.setdefault(e, []).append(fid)
        return out

    def q3(self, lead: str, k: int, now: int) -> list[str]:
        return [r[0] for r in self.db.execute("""
            SELECT id FROM facts
            WHERE superseded_by IS NULL AND (scope = :lead OR (scope = 'parent' AND visibility IN ('global', :lead)))
            ORDER BY importance * pow(0.5, ((:now - created_at) / 86400.0) /
                     CASE type WHEN 'user' THEN 180.0 WHEN 'feedback' THEN 120.0
                               WHEN 'project' THEN 30.0 ELSE 90.0 END) DESC, id
            LIMIT :k""", {"lead": lead, "now": now, "k": k})]

    def q4(self, fact: str) -> dict[str, list[str]]:
        rows = self.db.execute("""
            WITH RECURSIVE up(fid) AS (
                SELECT ? UNION SELECT d.parent_id FROM fact_derivation d JOIN up ON d.fact_id = up.fid),
              src(sid) AS (
                SELECT f.source_id FROM facts f JOIN up ON f.id = up.fid
                UNION SELECT s.parent_id FROM sources s JOIN src ON s.id = src.sid WHERE s.parent_id IS NOT NULL)
            SELECT 'f', fid FROM up UNION ALL SELECT 's', sid FROM src""", (fact,)).fetchall()
        return {"facts": sorted(x for t, x in rows if t == "f"), "sources": sorted(x for t, x in rows if t == "s")}

    def q5(self) -> list[list]:
        rows = self.db.execute("""
            SELECT scope, subject, attribute, group_concat(id) FROM facts WHERE superseded_by IS NULL
            GROUP BY scope, subject, attribute HAVING COUNT(DISTINCT value) > 1 ORDER BY 1, 2, 3""")
        return [[s, su, a, sorted(ids.split(","))] for s, su, a, ids in rows]

    def q6(self, entity: str, hub: int) -> list[str]:
        return [r[0] for r in self.db.execute("""
            WITH cand AS (
                SELECT DISTINCT fe2.entity_id AS e
                FROM fact_entity fe1 JOIN facts f ON f.id = fe1.fact_id AND f.superseded_by IS NULL
                JOIN fact_entity fe2 ON fe2.fact_id = fe1.fact_id
                WHERE fe1.entity_id = :e AND fe2.entity_id <> :e),
              rel AS (
                SELECT e FROM cand WHERE (SELECT COUNT(*) FROM fact_entity x JOIN facts g
                                          ON g.id = x.fact_id AND g.superseded_by IS NULL
                                          WHERE x.entity_id = cand.e) <= :hub)
            SELECT DISTINCT fe.fact_id FROM fact_entity fe JOIN rel ON fe.entity_id = rel.e
            JOIN facts f ON f.id = fe.fact_id AND f.superseded_by IS NULL ORDER BY 1""",
            {"e": entity, "hub": hub})]

    def q7a(self, lead: str) -> list[str]:
        return [r[0] for r in self.db.execute(
            "SELECT id FROM facts WHERE scope = ? AND visibility = 'parent' AND superseded_by IS NULL ORDER BY id", (lead,))]

    def q7b(self, lead: str) -> list[str]:
        return [r[0] for r in self.db.execute(
            "SELECT id FROM facts WHERE scope = 'parent' AND visibility IN ('global', ?) "
            "AND superseded_by IS NULL ORDER BY id", (lead,))]

    def q8(self, lead: str, terms: list[str]) -> list[str]:
        match = " AND ".join(f'"{t}"' for t in terms)
        return [r[0] for r in self.db.execute("""
            SELECT f.id FROM facts_fts JOIN facts f ON f.rowid = facts_fts.rowid
            WHERE facts_fts MATCH :m AND f.superseded_by IS NULL
              AND (f.scope = :lead OR (f.scope = 'parent' AND f.visibility IN ('global', :lead)))
            ORDER BY f.id""", {"m": match, "lead": lead})]

    def w1(self, fact: dict) -> dict:
        db = self.db
        with db:
            live = db.execute("SELECT id, value FROM facts WHERE scope = ? AND subject = ? AND attribute = ? "
                              "AND superseded_by IS NULL", (fact["scope"], fact["subject"], fact["attribute"])).fetchall()
            same = sorted(i for i, v in live if v == fact["value"])
            if same:
                return {"action": "skip_duplicate", "ids": same}
            cur = db.execute("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                             (fact["id"], fact["scope"], fact["type"], fact["visibility"], fact["subject"],
                              fact["attribute"], fact["value"], fact["text"], fact["name"], fact["description"],
                              fact["importance"], fact["created_at"], fact["source_id"]))
            db.execute("INSERT INTO facts_fts(rowid, text) VALUES (?, ?)", (cur.lastrowid, fact["text"]))
            db.executemany("INSERT INTO fact_entity VALUES (?,?)", [(fact["id"], e) for e in fact["entities"]])
            old = sorted(i for i, _ in live)
            db.executemany("UPDATE facts SET superseded_by = ? WHERE id = ?", [(fact["id"], o) for o in old])
            db.executemany("INSERT INTO fact_supersedes VALUES (?,?)", [(fact["id"], o) for o in old])
        return {"action": "supersede" if old else "insert", "ids": old}


# ============================================================================ neo4j
def neo4j_password() -> str:
    p = SCRATCH / "neo4j.pass"
    if not p.exists():
        SCRATCH.mkdir(parents=True, exist_ok=True)
        p.write_text(secrets.token_urlsafe(18))
        p.chmod(0o600)
    return p.read_text().strip()


def neo4j_envfile(heap: str, pagecache: str) -> Path:
    p = SCRATCH / "neo4j.env"
    p.write_text(f"NEO4J_AUTH=neo4j/{neo4j_password()}\n"
                 f"NEO4J_server_memory_heap_initial__size={heap}\n"
                 f"NEO4J_server_memory_heap_max__size={heap}\n"
                 f"NEO4J_server_memory_pagecache_size={pagecache}\n")
    p.chmod(0o600)
    return p


def neo4j_driver():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(f"bolt://127.0.0.1:{BOLT_PORT}", auth=("neo4j", neo4j_password()),
                                notifications_min_severity="OFF")


def neo4j_wait(timeout: float = 180.0) -> float:
    t0 = time.perf_counter()
    last = None
    while time.perf_counter() - t0 < timeout:
        try:
            d = neo4j_driver()
            d.verify_connectivity()
            with d.session() as s:
                s.run("RETURN 1").consume()
            d.close()
            return time.perf_counter() - t0
        except Exception as e:  # noqa: BLE001 - polling until bolt answers
            last = e
            state = sh(["docker", "inspect", "-f", "{{.State.Status}}", CONTAINER], check=False).strip()
            if state in ("exited", "dead"):
                raise RuntimeError(f"container {state} before bolt was ready") from e
            time.sleep(0.1)
    raise RuntimeError(f"neo4j not ready after {timeout}s: {last}")


def neo4j_fresh(heap: str = "512m", pagecache: str = "256m") -> float:
    sh(["docker", "rm", "-f", CONTAINER], check=False)
    sh(["docker", "volume", "rm", VOLUME], check=False)
    env = neo4j_envfile(heap, pagecache)
    t0 = time.perf_counter()
    sh(["docker", "run", "-d", "--name", CONTAINER, "--env-file", str(env),
        "-p", f"127.0.0.1:{BOLT_PORT}:7687", "-p", f"127.0.0.1:{HTTP_PORT}:7474",
        "-v", f"{VOLUME}:/data", IMAGE])
    neo4j_wait()
    return time.perf_counter() - t0


def docker_mem_mb(name: str = CONTAINER) -> float:
    out = sh(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", name]).strip()
    used = out.split("/")[0].strip()
    num = float(re.match(r"[\d.]+", used).group(0))
    unit = used[len(re.match(r"[\d.]+", used).group(0)):].strip()
    return num * {"B": 1 / 1e6, "KiB": 1024 / 1e6, "MiB": 1.048576, "GiB": 1073.741824}[unit]


class MemSampler(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.stop_ev = threading.Event()
        self.samples: list[float] = []

    def run(self) -> None:
        while not self.stop_ev.is_set():
            try:
                self.samples.append(docker_mem_mb())
            except Exception:  # noqa: BLE001
                pass
            self.stop_ev.wait(0.5)

    def stop(self) -> dict:
        self.stop_ev.set()
        self.join(timeout=10)
        return {"max_mb": round(max(self.samples), 1) if self.samples else None,
                "median_mb": round(statistics.median(self.samples), 1) if self.samples else None,
                "n_samples": len(self.samples)}


class Neo4jBackend:
    name = "neo4j"

    def __init__(self) -> None:
        self.driver = None
        self.session = None

    def _run(self, q: str, **params) -> list:
        return self.session.run(q, params).values()

    def load(self, ds: dict) -> dict:
        t0 = time.perf_counter()
        d = neo4j_driver()
        with d.session() as s:
            for stmt in [
                "CREATE CONSTRAINT fact_id IF NOT EXISTS FOR (f:Fact) REQUIRE f.id IS UNIQUE",
                "CREATE CONSTRAINT ent_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE",
                "CREATE CONSTRAINT src_id IF NOT EXISTS FOR (s:Source) REQUIRE s.id IS UNIQUE",
                "CREATE INDEX fact_scope IF NOT EXISTS FOR (f:Fact) ON (f.scope, f.visibility)",
                "CREATE INDEX fact_scope_only IF NOT EXISTS FOR (f:Fact) ON (f.scope)",
                "CREATE INDEX fact_created IF NOT EXISTS FOR (f:Fact) ON (f.created_at)",
                "CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name)",
                "CREATE INDEX fact_key IF NOT EXISTS FOR (f:Fact) ON (f.scope, f.subject, f.attribute)",
                "CREATE FULLTEXT INDEX fact_text IF NOT EXISTS FOR (f:Fact) ON EACH [f.text] "
                "OPTIONS {indexConfig: {`fulltext.analyzer`: 'standard-no-stop-words'}}",
            ]:
                s.run(stmt).consume()
            s.run("CALL db.awaitIndexes(300)").consume()

            def batched(rows, q, size=2000):
                for i in range(0, len(rows), size):
                    s.run(q, rows=rows[i:i + size]).consume()

            batched([{k: e[k] for k in ("id", "name", "kind")} for e in ds["entities"]],
                    "UNWIND $rows AS r CREATE (:Entity {id: r.id, name: r.name, kind: r.kind})")
            batched([{k: x[k] for k in ("id", "kind", "ref", "created_at")} for x in ds["sources"]],
                    "UNWIND $rows AS r CREATE (:Source {id: r.id, kind: r.kind, ref: r.ref, created_at: r.created_at})")
            batched([{"id": x["id"], "p": x["parent_id"]} for x in ds["sources"] if x["parent_id"]],
                    "UNWIND $rows AS r MATCH (s:Source {id: r.id}), (p:Source {id: r.p}) CREATE (s)-[:CHILD_OF]->(p)")
            batched([{k: f[k] for k in ("id", "scope", "type", "visibility", "subject", "attribute", "value", "text",
                                        "name", "importance", "created_at", "superseded_by")} for f in ds["facts"]],
                    "UNWIND $rows AS r CREATE (f:Fact) SET f = r")
            batched([{"id": f["id"], "es": f["entities"], "src": f["source_id"], "der": f["derived_from"],
                      "sup": f["supersedes"]} for f in ds["facts"]],
                    """UNWIND $rows AS r MATCH (f:Fact {id: r.id})
                       CALL (f, r) { UNWIND r.es AS eid MATCH (e:Entity {id: eid}) CREATE (f)-[:MENTIONS]->(e) }
                       CALL (f, r) { MATCH (s:Source {id: r.src}) CREATE (f)-[:FROM]->(s) }
                       CALL (f, r) { UNWIND r.der AS pid MATCH (p:Fact {id: pid}) CREATE (f)-[:DERIVED_FROM]->(p) }
                       CALL (f, r) { UNWIND r.sup AS oid MATCH (o:Fact {id: oid}) CREATE (f)-[:SUPERSEDES]->(o) }""")
            s.run("CALL db.awaitIndexes(300)").consume()
            n = s.run("MATCH (f:Fact) RETURN count(f)").single()[0]
            assert n == len(ds["facts"]), (n, len(ds["facts"]))
        d.close()
        load_s = time.perf_counter() - t0
        return {"load_s": load_s, "disk_kb": int(sh(["docker", "exec", CONTAINER, "du", "-sk", "/data"]).split()[0])}

    def open(self) -> None:
        self.close()
        self.driver = neo4j_driver()
        self.driver.verify_connectivity()
        self.session = self.driver.session()
        self.session.run("CALL db.clearQueryCaches()").consume()

    def close(self) -> None:
        if self.session:
            self.session.close()
        if self.driver:
            self.driver.close()
        self.session = self.driver = None

    def q1(self, entity: str) -> list[str]:
        return [r[0] for r in self._run(
            "MATCH (:Entity {id: $e})<-[:MENTIONS]-(f:Fact) WHERE f.superseded_by IS NULL "
            "RETURN f.id AS id ORDER BY id", e=entity)]

    def q2(self) -> dict[str, list[str]]:
        rows = self._run("""
            MATCH (e:Entity)<-[:MENTIONS]-(f:Fact)
            WHERE f.superseded_by IS NULL AND f.scope <> 'parent'
            WITH e, collect(f.id) AS ids, count(DISTINCT f.scope) AS ns
            WHERE ns >= 2
            RETURN e.id, ids""")
        return {e: sorted(ids) for e, ids in sorted(rows)}

    def q3(self, lead: str, k: int, now: int) -> list[str]:
        return [r[0] for r in self._run("""
            MATCH (f:Fact)
            WHERE f.superseded_by IS NULL AND (f.scope = $lead OR (f.scope = 'parent' AND f.visibility IN ['global', $lead]))
            WITH f, f.importance * 0.5 ^ ((($now - f.created_at) / 86400.0) /
                 CASE f.type WHEN 'user' THEN 180.0 WHEN 'feedback' THEN 120.0
                             WHEN 'project' THEN 30.0 ELSE 90.0 END) AS score
            RETURN f.id AS id ORDER BY score DESC, id ASC LIMIT $k""", lead=lead, now=now, k=k)]

    def q4(self, fact: str) -> dict[str, list[str]]:
        rows = self._run("""
            MATCH (:Fact {id: $id})-[:DERIVED_FROM*0..]->(g:Fact)
            WITH collect(DISTINCT g) AS gs
            UNWIND gs AS g
            MATCH (g)-[:FROM]->(:Source)-[:CHILD_OF*0..]->(s:Source)
            RETURN [x IN gs | x.id] AS facts, collect(DISTINCT s.id) AS sources""", id=fact)
        return {"facts": sorted(rows[0][0]), "sources": sorted(rows[0][1])}

    def q5(self) -> list[list]:
        rows = self._run("""
            MATCH (f:Fact) WHERE f.superseded_by IS NULL
            WITH f.scope AS scope, f.subject AS subject, f.attribute AS attribute,
                 collect(f.id) AS ids, count(DISTINCT f.value) AS nv
            WHERE nv > 1
            RETURN scope, subject, attribute, ids""")
        return [[s, su, a, sorted(ids)] for s, su, a, ids in sorted(rows)]

    def q6(self, entity: str, hub: int) -> list[str]:
        return [r[0] for r in self._run("""
            MATCH (e:Entity {id: $e})<-[:MENTIONS]-(f1:Fact)-[:MENTIONS]->(r:Entity)
            WHERE f1.superseded_by IS NULL AND r <> e
            WITH DISTINCT r
            WHERE COUNT { (r)<-[:MENTIONS]-(x:Fact) WHERE x.superseded_by IS NULL } <= $hub
            MATCH (r)<-[:MENTIONS]-(f2:Fact) WHERE f2.superseded_by IS NULL
            RETURN DISTINCT f2.id AS id ORDER BY id""", e=entity, hub=hub)]

    def q7a(self, lead: str) -> list[str]:
        return [r[0] for r in self._run(
            "MATCH (f:Fact) WHERE f.scope = $lead AND f.visibility = 'parent' AND f.superseded_by IS NULL "
            "RETURN f.id AS id ORDER BY id", lead=lead)]

    def q7b(self, lead: str) -> list[str]:
        return [r[0] for r in self._run(
            "MATCH (f:Fact) WHERE f.scope = 'parent' AND f.visibility IN ['global', $lead] "
            "AND f.superseded_by IS NULL RETURN f.id AS id ORDER BY id", lead=lead)]

    def q8(self, lead: str, terms: list[str]) -> list[str]:
        return [r[0] for r in self._run("""
            CALL db.index.fulltext.queryNodes('fact_text', $ftq) YIELD node AS f
            WHERE f.superseded_by IS NULL
              AND (f.scope = $lead OR (f.scope = 'parent' AND f.visibility IN ['global', $lead]))
            RETURN f.id AS id ORDER BY id""", ftq=" ".join(f"+{t}" for t in terms), lead=lead)]

    def w1(self, fact: dict) -> dict:
        def tx(t):
            live = t.run("MATCH (f:Fact {scope: $s, subject: $su, attribute: $a}) WHERE f.superseded_by IS NULL "
                         "RETURN f.id, f.value", s=fact["scope"], su=fact["subject"], a=fact["attribute"]).values()
            same = sorted(i for i, v in live if v == fact["value"])
            if same:
                return {"action": "skip_duplicate", "ids": same}
            old = sorted(i for i, _ in live)
            props = {k: fact[k] for k in ("id", "scope", "type", "visibility", "subject", "attribute", "value",
                                          "text", "name", "importance", "created_at")}
            t.run("""CREATE (n:Fact) SET n = $p
                     WITH n UNWIND $es AS eid MATCH (e:Entity {id: eid}) CREATE (n)-[:MENTIONS]->(e)
                     WITH DISTINCT n MATCH (s:Source {id: $src}) CREATE (n)-[:FROM]->(s)
                     WITH n UNWIND $old AS oid MATCH (o:Fact {id: oid})
                     SET o.superseded_by = n.id CREATE (n)-[:SUPERSEDES]->(o)""",
                  p=props, es=fact["entities"], src=fact["source_id"], old=old).consume()
            return {"action": "supersede" if old else "insert", "ids": old}
        return self.session.execute_write(tx)


# ============================================================================ harness
def normalize(q: str, v):
    if q == "Q2":
        return {k: sorted(x) for k, x in v.items()}
    if q == "Q5":
        return sorted([a, b, c, sorted(d)] for a, b, c, d in v)
    if q == "Q4":
        return {"facts": sorted(v["facts"]), "sources": sorted(v["sources"])}
    if q in ("Q3", "W1"):
        return v
    return sorted(v)


def call(be, q: str, params: dict, hub: int):
    p = params
    return {"Q1": lambda: be.q1(p["entity"]), "Q2": lambda: be.q2(),
            "Q3": lambda: be.q3(p["lead"], p["k"], p["now"]), "Q4": lambda: be.q4(p["fact"]),
            "Q5": lambda: be.q5(), "Q6": lambda: be.q6(p["entity"], hub),
            "Q7a": lambda: be.q7a(p["lead"]), "Q7b": lambda: be.q7b(p["lead"]),
            "Q8": lambda: be.q8(p["lead"], p["terms"]), "W1": lambda: be.w1(p["fact"])}[q]()


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    i = min(len(xs) - 1, max(0, int(round(p / 100.0 * (len(xs) - 1)))))
    return xs[i]


def prf(got: list[str], exp: list[str]) -> tuple[float, float]:
    g, e = set(got), set(exp)
    tp = len(g & e)
    return (tp / len(g) if g else 1.0, tp / len(e) if e else 1.0)


def bench_backend(be, truth: dict, n_facts: int, runs: int, hub: int, log, queries: list[str],
                  warmup: int = 30, mem_probe=None) -> list[dict]:
    rows = []
    for q in queries:
        inst = truth[q]
        gc.collect()
        t0 = time.perf_counter()
        be.open()
        connect_ms = (time.perf_counter() - t0) * 1000
        times, ok = [], 0
        precs, recs = [], []
        n_total = runs + 1 if q != "W1" else len(inst)
        first_ms = None
        for i in range(n_total):
            item = inst[i % len(inst)]
            t0 = time.perf_counter()
            got = call(be, q, item["params"], hub)
            dt = (time.perf_counter() - t0) * 1000
            good = normalize(q, got) == normalize(q, item["expected"])
            ok += good
            if be.name == "naive":
                p, r = prf(got, item["expected"])
                precs.append(p)
                recs.append(r)
            if i == 0:
                first_ms = dt
                if q != "W1":
                    for j in range(warmup):
                        call(be, q, inst[(j + 1) % len(inst)]["params"], hub)
            else:
                times.append(dt)
            if not good and be.name != "naive":
                print(f"  MISMATCH {be.name} {q} instance {i % len(inst)} params={json.dumps(item['params'])[:160]}",
                      file=log, flush=True)
        be.close()
        mem_after = mem_probe() if mem_probe else None
        row = {"backend": be.name, "query": q, "n_facts": n_facts, "p50_ms": round(statistics.median(times), 3),
               "p95_ms": round(pct(times, 95), 3), "first_ms": round(first_ms, 3), "correct": round(ok / n_total, 4),
               "runs": n_total, "warm_runs": len(times), "instances": len(inst), "connect_ms": round(connect_ms, 3)}
        if mem_after is not None:
            row["mem_mb_after_batch"] = mem_after
        row["untimed_warmup_runs"] = warmup if q != "W1" else 0
        if precs:
            row["mean_precision"] = round(statistics.mean(precs), 4)
            row["mean_recall"] = round(statistics.mean(recs), 4)
        rows.append(row)
        print(json.dumps(row), file=log, flush=True)
    return rows


def loc_table() -> dict:
    out = {}
    for cls in (FilesBackend, SqliteBackend, Neo4jBackend, NaiveFilesBackend):
        per = {}
        shared = 0
        for nm, fn in inspect.getmembers(cls, inspect.isfunction):
            if nm not in cls.__dict__:
                continue
            key = {"q7a": "Q7a", "q7b": "Q7b"}.get(nm, nm.upper())
            if key in QUERIES:
                per[key] = loc(fn)
            elif nm in ("load", "_write") or nm.startswith("_") or nm in ("open", "close"):
                shared += loc(fn)
        out[cls.name] = {"per_query": per, "load_and_helpers": shared}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--n-facts", type=int, required=True)
    ap.add_argument("--runs", type=int, default=30)
    ap.add_argument("--backends", default="files,naive,sqlite,neo4j")
    ap.add_argument("--heap", default="512m")
    ap.add_argument("--pagecache", default="256m")
    ap.add_argument("--keep-neo4j", action="store_true")
    ap.add_argument("--warmup", type=int, default=30, help="untimed runs after the first run, before the timed warm runs")
    ap.add_argument("--queries", default=",".join(QUERIES), help="comma-separated subset of " + ",".join(QUERIES))
    ap.add_argument("--out-tag", default="", help="write bench-<n>-<tag>.log and bench-results-<tag>.json instead of "
                                                   "the committed evidence files (use for verification reruns)")
    a = ap.parse_args()
    wanted = [q for q in QUERIES if q in a.queries.split(",")]
    sfx = f"-{a.out_tag}" if a.out_tag else ""
    ds = json.loads(Path(a.data, f"dataset-{a.n_facts}.json").read_text())
    truth = json.loads(Path(a.data, f"truth-{a.n_facts}.json").read_text())
    hub = ds["meta"]["hub_degree"]
    EVID.mkdir(parents=True, exist_ok=True)
    log_path = EVID / f"bench-{a.n_facts}{sfx}.log"
    work = SCRATCH / f"bench-{a.n_facts}"
    work.mkdir(parents=True, exist_ok=True)
    backends = a.backends.split(",")
    import neo4j as neo4j_pkg
    with open(log_path, "w") as log:
        print(f"date_utc: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}", file=log)
        print(f"command: PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED')} python bench.py " + " ".join(sys.argv[1:]), file=log)
        print(f"python: {sys.version.split()[0]}  sqlite: {sqlite3.sqlite_version}  ripgrep: "
              f"{sh([RG, '--version']).splitlines()[0]}  neo4j-driver: {neo4j_pkg.__version__}  image: {IMAGE}", file=log)
        print(f"host: {os.uname().sysname} {os.uname().release} {os.uname().machine}  neo4j container: {CONTAINER} "
              f"(volume {VOLUME}, bolt 127.0.0.1:{BOLT_PORT}, http 127.0.0.1:{HTTP_PORT})", file=log)
        print(f"dataset: n_facts={len(ds['facts'])} entities={len(ds['entities'])} sources={len(ds['sources'])} "
              f"scopes={len(ds['scopes'])} planted={json.dumps(ds['meta']['planted'])}", file=log)
        loc_now = loc_table()
        print(f"loc: {json.dumps(loc_now)}", file=log, flush=True)
        all_rows: list[dict] = []
        loads: dict[str, dict] = {}
        files_root = work / "files"
        for b in backends:
            print(f"== backend {b}", file=log, flush=True)
            extra: dict = {}
            if b == "files":
                be = FilesBackend(files_root)
            elif b == "naive":
                be = NaiveFilesBackend(files_root)
            elif b == "sqlite":
                be = SqliteBackend(work / "memory.sqlite")
            else:
                extra["fresh_start_to_bolt_s"] = round(neo4j_fresh(a.heap, a.pagecache), 2)
                extra["idle_mem_mb_before_load"] = round(docker_mem_mb(), 1)
                be = Neo4jBackend()
            info = be.load(ds)
            info.update(extra)
            if b == "neo4j":
                info["mem_mb_after_load"] = round(docker_mem_mb(), 1)
            loads[b] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in info.items()}
            print(f"load: {json.dumps(loads[b])}", file=log, flush=True)
            qs = [q for q in (["Q1"] if b == "naive" else QUERIES) if q in wanted]
            rows = bench_backend(be, truth, a.n_facts, a.runs, hub, log, qs, a.warmup,
                                 (lambda: round(docker_mem_mb(), 1)) if b == "neo4j" else None)
            if b == "neo4j":
                mems = [r["mem_mb_after_batch"] for r in rows if "mem_mb_after_batch" in r]
                mem = {"max_mb_between_batches": max(mems) if mems else None,
                       "note": "docker stats sampled after each query batch, never during timed runs"}
                loads[b]["mem_during_bench"] = mem
                loads[b]["disk_kb_after_bench"] = int(sh(["docker", "exec", CONTAINER, "du", "-sk", "/data"]).split()[0])
                print(f"neo4j mem during bench: {json.dumps(mem)}", file=log, flush=True)
                if not a.keep_neo4j:
                    sh(["docker", "rm", "-f", CONTAINER], check=False)
                    sh(["docker", "volume", "rm", VOLUME], check=False)
            if b == "files":
                loads[b]["disk_kb_after_bench"] = du_kb(files_root)
            all_rows += rows
        res_path = EVID / f"bench-results{sfx}.json"
        existing = json.loads(res_path.read_text()) if res_path.exists() else {"rows": [], "loads": {}, "loc": {}}
        existing["rows"] = [r for r in existing["rows"] if not (r["n_facts"] == a.n_facts and r["backend"] in backends
                                                                and r["query"] in wanted)] + all_rows
        existing["rows"].sort(key=lambda r: (r["n_facts"], r["backend"], QUERIES.index(r["query"])))
        existing.setdefault("loads", {}).setdefault(str(a.n_facts), {}).update(loads)
        existing["loc"] = loc_now
        existing["notes"] = ("first_ms = first execution on a fresh client; then --warmup untimed runs (JIT/plan/page "
                             "caches), then p50/p95 over --runs timed runs cycling the truth instances; correct = share "
                             "of all timed runs equal to the oracle answer; W1 mutates the store and runs last (no "
                             "warm-up); naive = files without entity metadata (Q1 only, precision/recall given); Neo4j "
                             "memory is sampled between query batches, never during timed runs")
        res_path.write_text(json.dumps(existing, indent=1, sort_keys=True))
        bad = [r for r in all_rows if r["correct"] < 1.0 and r["backend"] != "naive"]
        print("summary (backend query p50_ms p95_ms first_ms correct):", file=log)
        for r in all_rows:
            print(f"  {r['backend']:7} {r['query']:4} {r['p50_ms']:10.3f} {r['p95_ms']:10.3f} {r['first_ms']:10.3f} {r['correct']}",
                  file=log)
        verdict = "PASS" if not bad else "FAIL"
        msg = (f"{len(all_rows)} backend x query rows at n={a.n_facts}; all non-naive backends match the oracle on every run"
               if not bad else f"{len(bad)} rows disagree with the oracle: " +
               ", ".join(f"{r['backend']}/{r['query']}" for r in bad))
        print(f"VERDICT: {verdict} — {msg}", file=log)
    print(log_path.read_text())
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
