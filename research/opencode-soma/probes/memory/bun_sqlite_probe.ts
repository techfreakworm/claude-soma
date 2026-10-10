// Does bun:sqlite (the SQLite an opencode plugin gets, since opencode is a compiled Bun binary)
// have what the memory addon needs? FTS5, recursive CTEs, WAL, math functions (pow), JSON1,
// UPSERT, and acceptable speed on the 50k corpus's core queries.
//
//   bun run bun_sqlite_probe.ts [dataset-50000.json]
//
// Prints a transcript; the caller (run_bun_sqlite.sh) adds the header and writes the evidence log.
import { Database } from "bun:sqlite";
import { readFileSync } from "node:fs";

const out = (s: string) => console.log(s);
const db = new Database(":memory:");
out(`bun ${Bun.version} (${process.platform}/${process.arch}); sqlite_version() = ${(db.query("select sqlite_version() v").get() as any).v}`);

function check(name: string, fn: () => string) {
  try {
    out(`${name}: OK ${fn()}`);
    return true;
  } catch (e: any) {
    out(`${name}: FAIL ${String(e?.message ?? e).slice(0, 160)}`);
    return false;
  }
}

const results: Record<string, boolean> = {};
results.fts5 = check("FTS5", () => {
  db.run("create virtual table t using fts5(x)");
  db.run("insert into t(x) values ('nightly backup failed because of an expired token'), ('deploy ok')");
  const r = db.query("select count(*) n from t where t match '\"backup\" AND \"token\"'").get() as any;
  return `match rows=${r.n}`;
});
results.recursive_cte = check("recursive CTE", () => {
  const r = db.query("with recursive c(n) as (select 1 union all select n+1 from c where n<10) select sum(n) s from c").get() as any;
  return `sum=${r.s}`;
});
results.math_pow = check("math functions (pow)", () => {
  const r = db.query("select pow(0.5, 2.0) p").get() as any;
  return `pow(0.5,2)=${r.p}`;
});
results.json1 = check("JSON1", () => {
  const r = db.query("select json_extract('{\"a\":[1,2,3]}', '$.a[2]') v").get() as any;
  return `json_extract=${r.v}`;
});
results.upsert = check("UPSERT", () => {
  db.run("create table u(k text primary key, v int)");
  db.run("insert into u values ('a',1) on conflict(k) do update set v=v+1");
  db.run("insert into u values ('a',1) on conflict(k) do update set v=v+1");
  return `v=${(db.query("select v from u").get() as any).v}`;
});
results.wal = check("WAL on a file db", () => {
  const p = `${process.env.TMPDIR ?? "/tmp"}/bun-wal-probe-${process.pid}.sqlite`;
  const f = new Database(p, { create: true });
  const m = (f.query("pragma journal_mode=WAL").get() as any).journal_mode;
  f.close();
  return `journal_mode=${m}`;
});
results.custom_fn = check("user-defined SQL function", () => {
  const anyDb = db as any;
  if (typeof anyDb.function !== "function") throw new Error("bun:sqlite Database has no function() API");
  return "available";
});

const path = process.argv[2];
if (path) {
  const ds = JSON.parse(readFileSync(path, "utf8"));
  const f = new Database(":memory:");
  f.run(`create table facts(id text primary key, scope text, type text, visibility text, subject text, attribute text,
         value text, text text, importance int, created_at int, superseded_by text)`);
  f.run("create table fact_entity(fact_id text, entity_id text, primary key(fact_id, entity_id)) without rowid");
  f.run("create index fe_entity on fact_entity(entity_id, fact_id)");
  f.run("create index facts_scope on facts(scope, visibility, superseded_by)");
  f.run("create virtual table facts_fts using fts5(text, content='facts', content_rowid='rowid')");
  const t0 = performance.now();
  const ins = f.prepare("insert into facts values (?,?,?,?,?,?,?,?,?,?,?)");
  const insE = f.prepare("insert into fact_entity values (?,?)");
  f.transaction(() => {
    for (const x of ds.facts) {
      ins.run(x.id, x.scope, x.type, x.visibility, x.subject, x.attribute, x.value, x.text, x.importance, x.created_at, x.superseded_by);
      for (const e of x.entities) insE.run(x.id, e);
    }
    f.run("insert into facts_fts(rowid, text) select rowid, text from facts");
  })();
  f.run("analyze");
  out(`loaded ${ds.facts.length} facts (+ANALYZE) in ${(performance.now() - t0).toFixed(0)} ms`);
  const time = (label: string, fn: () => number) => {
    const ts: number[] = [];
    let n = 0;
    for (let i = 0; i < 31; i++) {
      const s = performance.now();
      n = fn();
      ts.push(performance.now() - s);
    }
    const warm = ts.slice(1).sort((a, b) => a - b);
    out(`${label}: rows=${n} first=${ts[0].toFixed(3)}ms p50=${warm[15].toFixed(3)}ms p95=${warm[28].toFixed(3)}ms`);
  };
  const lead = ds.scopes.find((s: any) => s.kind === "lead").id;
  time("Q1 entity recall (e-postgres)", () =>
    f.query("select f.id from fact_entity fe join facts f on f.id = fe.fact_id where fe.entity_id = ? and f.superseded_by is null").all("e-postgres").length);
  time("Q8 FTS recall (backup)", () =>
    f.query(`select f.id from facts_fts join facts f on f.rowid = facts_fts.rowid where facts_fts match '"backup"'
             and f.superseded_by is null and (f.scope = ?1 or (f.scope = 'parent' and f.visibility in ('global', ?1)))`).all(lead).length);
  const hl: Record<string, number> = { user: 180, feedback: 120, project: 30, reference: 90 };
  time("Q3 decay top-10, ranked in JS (no pow needed)", () => {
    const rows = f.query(`select id, type, importance, created_at from facts where superseded_by is null
                          and (scope = ?1 or (scope = 'parent' and visibility in ('global', ?1)))`).all(lead) as any[];
    const now = ds.meta.now_ts;
    rows.sort((a, b) => b.importance * 0.5 ** ((now - b.created_at) / 86400 / hl[b.type]) -
                        a.importance * 0.5 ** ((now - a.created_at) / 86400 / hl[a.type]) || (a.id < b.id ? -1 : 1));
    return rows.slice(0, 10).length;
  });
}
const ok = results.fts5 && results.recursive_cte && results.json1 && results.upsert && results.wal;
out(`SUMMARY: ${JSON.stringify(results)}`);
out(`RESULT: ${ok ? "PASS" : "FAIL"}`);
