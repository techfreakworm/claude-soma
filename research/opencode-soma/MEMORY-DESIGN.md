# opencode-soma memory design

Research output of the `claude-soma-manager` soma task. Nothing here is being built. Every number
below comes from a script under `probes/memory/` whose transcript is in `evidence/memory/`. Anything
not measured is labelled **desk-research-only**. Hook names come from probe-engineer's catalogue of
opencode 1.18.8 (`evidence/opencode/P2-hooks/hook-catalog.log`) and are marked *verified* only where
a probe exercised them.

## 1. Decision

- **Default store: one SQLite file per user** (WAL, FTS5, recursive CTEs), opened in-process with
  `bun:sqlite` by the opencode plugin and by the opencode-soma daemon.
- **Flat files are an export, not the store.** opencode-soma writes a Claude Code-style mirror
  (`MEMORY.md` index plus one markdown file per fact) so people can read and diff memory and the
  existing dashboard view keeps working. Queries never read the mirror.
- **Neo4j is not shipped.** At most it becomes an opt-in adapter later, for someone who already runs it.

The measurement that decided it: on a 50,000-fact corpus, every per-prompt recall query (entity
recall, decay ranking, visibility views, keyword recall) answers within **3.7 ms p95 on SQLite**.
The same queries take 16–58 ms on Neo4j and 30–1,295 ms on flat files. SQLite needs no service,
JVM, Docker or open port. Neo4j was faster on three of the ten operations:

- provenance (Q4) and 2-hop expansion (Q6), which two standard SQLite fixes overturn even at
  500,000 facts;
- the cross-project analytics scan (Q2), which stays 1.5–1.8x faster on Neo4j. It is a dashboard
  query that SQLite still answers in 385 ms p95 at 50k (section 11).

## 2. What claude-soma does today, and what opencode lacks

claude-soma implements no memory of its own. It inherits Claude Code auto-memory: per working
directory, `~/.claude/projects/<cwd with / replaced by ->/memory/` holds `MEMORY.md` (one line per
memory: `- [Title](file.md) — hook`) and one markdown file per fact. Each fact file has YAML
frontmatter `name`, `description`, `type: user|feedback|project|reference`, a body (often
**Why:** / **How to apply:**) and `[[links]]` to other facts. The orchestrator bot (cwd
`/opt/claude-soma`) and each lead (its own cwd) get separate directories, so isolation comes free
but nothing is shared. The dashboard only ever reads `MEMORY.md`:
`src/claude_soma/mcp_servers/hermes_api/claude_state.py:65` (`read_memory`, which tries the
registry cwd slug, the project slug and two `/home/ubuntu` fallbacks) and
`src/claude_soma/api/routes/memory.py:15-24`.

What auto-memory does not do: link facts across projects, find contradictions, record provenance,
decay old facts, or let the orchestrator see a lead's memory in a structured way. It is also
grep-only. On the benchmark corpus laid out exactly this way, grepping an entity's name returns
the exact answer for only **10–29%** of entity queries (precision 0.62–0.82, recall 0.89–0.95;
naive rows in `bench-{600,5000,50000}.log`). Aliases ("Postgres", "pg"), same-named components in
different projects, and names inside other names all defeat it.

opencode 1.18.8 has no memory feature. It does have the hooks an addon needs (catalogue: 20 v1 hook
keys):

| need | opencode mechanism | status |
|---|---|---|
| inject recalled facts into every prompt | `chat.message` hook: push a synthetic text part | *verified*: the model quoted the freshly injected value each turn (`evidence/opencode/P2-hooks/inject.log`) |
| inject into the system prompt | `experimental.chat.system.transform` | *verified at the transport level*, but qwen3:32b kept repeating a stale answer (same log), so not the primary path |
| notice that a session went quiet (capture trigger) | `event` hook, `session.idle` bus event | *verified*: the spy plugin received 7 `session.idle` events (`evidence/opencode/P2-hooks/spy.jsonl`, `events.log`) |
| capture before context is compacted | `experimental.session.compacting` (`output.context`, `output.prompt`) | catalogued only, not exercised; experimental |
| explicit memory tools | plugin `tool` map, or an MCP server | `tool` catalogued only. MCP tools were exercised by probe-engineer's P2 activity probe |
| provenance of tool results | `tool.execute.after` (tool, sessionID, callID, args, output) | *verified* by the P2 activity probe (`activity.log`) |
| read a session's messages from outside | `opencode serve` HTTP+SSE API | *verified* (`evidence/opencode/P8-serve/serve.log`) |

## 3. Requirements and where each is met

| requirement | design | evidence |
|---|---|---|
| write path | explicit tools + auto-capture at `session.idle`; one upsert transaction | W1 rows; section 6 |
| recall path | top-k injected through `chat.message` each prompt; search tool for depth | Q1/Q3/Q7/Q8 rows; `bun-sqlite.log` |
| index | SQLite B-tree indexes, FTS5, materialized `entity_degree` | `bench.py` schema; `sqlite-tuning-*.log` |
| per-lead isolation | scope taken from the runtime (lead registry), never from the model; predicates in the store API | Q3/Q7/Q8 oracle-correct at every scale |
| parent and lead visibility | `local` / `parent` / `global` / `lead:<name>` rules (section 5) | Q7a/Q7b oracle-correct |
| dedupe / update / delete | key equality + model judgement on FTS candidates; supersede, never overwrite; soft delete | W1 rows; Track B |
| provenance | source tree (telegram → session → tool → file), derived-from links, recursive CTE | Q4 rows |
| recency / decay | importance × 0.5^(age / half-life by type) | Q3 rows |

## 4. Data model

A memory is an **atomic fact**: one subject entity, one attribute, one value, a human sentence, a
type, an importance from 1 to 5, a scope, a visibility, and the source it came from. Free-form
notes still fit: the attribute is then a unique `note-<n>` key, so they never conflict.

```sql
CREATE TABLE scopes   (id TEXT PRIMARY KEY,                    -- 'parent' | 'lead:<name>'
                       kind TEXT NOT NULL CHECK (kind IN ('parent','lead')),
                       cwd TEXT, created_at INTEGER NOT NULL);
CREATE TABLE sources  (id TEXT PRIMARY KEY,
                       kind TEXT NOT NULL CHECK (kind IN ('telegram','session','tool','file','user_edit','import')),
                       ref TEXT,                               -- chat+message id, opencode session id, tool callID, path#line
                       parent_id TEXT REFERENCES sources(id),  -- file -> tool call -> session -> telegram message
                       created_at INTEGER NOT NULL);
CREATE TABLE entities (id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT,
                       aliases TEXT,                           -- JSON array, used for entity resolution
                       home_scope TEXT);                       -- set for project-local entities
CREATE TABLE facts (
  id TEXT PRIMARY KEY,
  scope TEXT NOT NULL REFERENCES scopes(id),
  type TEXT NOT NULL CHECK (type IN ('user','feedback','project','reference')),
  visibility TEXT NOT NULL,                                    -- see section 5
  subject TEXT NOT NULL REFERENCES entities(id),
  attribute TEXT NOT NULL,
  value TEXT NOT NULL,                                         -- normalized; entity id when the value is an entity
  text TEXT NOT NULL,                                          -- the sentence that gets injected
  importance INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 5),
  created_at INTEGER NOT NULL,
  source_id TEXT REFERENCES sources(id),
  superseded_by TEXT REFERENCES facts(id),                     -- NULL = active
  deleted_at INTEGER,                                          -- soft delete ("forget")
  curator TEXT NOT NULL,                                       -- 'explicit' | 'import' | model id that wrote it
  last_recalled_at INTEGER, recall_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE fact_entity     (fact_id TEXT NOT NULL, entity_id TEXT NOT NULL, PRIMARY KEY (fact_id, entity_id)) WITHOUT ROWID;
CREATE TABLE fact_derivation (fact_id TEXT NOT NULL, parent_id TEXT NOT NULL, PRIMARY KEY (fact_id, parent_id)) WITHOUT ROWID;
CREATE TABLE fact_supersedes (new_id TEXT NOT NULL, old_id TEXT NOT NULL, PRIMARY KEY (new_id, old_id)) WITHOUT ROWID;
CREATE TABLE entity_degree   (entity_id TEXT PRIMARY KEY, d INTEGER NOT NULL);   -- active mentions, +1/-1 on write
CREATE TABLE conflicts (id INTEGER PRIMARY KEY, scope TEXT, subject TEXT, attribute TEXT,
                        fact_ids TEXT NOT NULL, opened_at INTEGER NOT NULL, resolved_at INTEGER, resolution TEXT);
CREATE TABLE capture_cursor (session_id TEXT PRIMARY KEY, last_message_id TEXT, updated_at INTEGER NOT NULL);
CREATE INDEX fe_entity   ON fact_entity(entity_id, fact_id);
CREATE INDEX facts_scope ON facts(scope, visibility, superseded_by);
CREATE INDEX facts_key   ON facts(scope, subject, attribute);
CREATE VIRTUAL TABLE facts_fts USING fts5(text, content='facts', content_rowid='rowid');
PRAGMA journal_mode = WAL; PRAGMA synchronous = NORMAL; PRAGMA busy_timeout = 5000;
```

The benchmark schema (`probes/memory/bench.py`, `SCHEMA`) is this without the operational columns
(`deleted_at`, `curator`, recall stats) and without the `conflicts` / `capture_cursor` tables. The
`entity_degree` table is measured separately (`sqlite-tuning-*.log`).

## 5. Scopes and visibility

Scopes: `parent` (the always-on orchestrator that talks on Telegram) and `lead:<name>` (one per
workstream, keyed by the lead registry, which also maps the lead's cwd to its scope).

| fact lives in | `visibility` values | who can read it |
|---|---|---|
| `lead:L` | `local` | lead L only (working notes) |
| `lead:L` | `parent` (default) | lead L and the parent |
| `parent` | `local` | the parent only |
| `parent` | `global` | the parent and every lead (user profile, standing rules) |
| `parent` | `lead:L` | the parent and lead L (a briefing for that lead) |

- **Lead L reads** its own scope, plus parent facts whose visibility is `global` or `lead:L`.
- **The parent reads** its own scope, plus lead facts with visibility `parent`.
- **Leads never read each other.** Knowledge moves between leads only through **promotion**: the
  parent copies a lead fact into its own scope as `global` (or `lead:M`), with a `derived_from` link
  back, so provenance survives.
- **Writes:** a lead writes only its own scope, with `local` or `parent`. Only the parent writes
  `global` or `lead:<name>`.
- **Enforcement:** the scope is never taken from model output. The plugin resolves it from the
  opencode instance (lead registry: cwd → lead), and every store call takes the caller's scope and
  applies the predicates above. There is no raw-SQL tool for the model. This guards against noise
  and accidental cross-talk, not against a hostile local process; it is all the same user's
  machine and file.

These are exactly the rules the oracle encodes (`gen_memory.py`: `o_visible_to_lead`, `o_q7a`,
`o_q7b`). Q3, Q7a, Q7b and Q8 apply them, and all three backends matched the oracle on every run
at every scale, so no fact leaked across leads.

## 6. Write path

### 6.1 Explicit

`memory_save(text, subject?, attribute?, value?, type?, visibility?)`,
`memory_forget(query | id)` and `memory_search(query)` are exposed as plugin tools (or as an MCP
server, if one implementation should also serve Claude Code). "Remember X" in Telegram routes to
`memory_save` with `curator='explicit'`. Explicit facts outrank model-captured ones in conflicts
(6.4).

### 6.2 Auto-capture

Trigger: the plugin's `event` hook sees `session.idle` (verified), debounced about 30 s. It reads
the messages added since `capture_cursor` for that session (plugin `client` / `opencode serve`
API). `experimental.session.compacting` is a second, opportunistic trigger: capture what is about
to be summarized, and push the top memories into `output.context` so they survive compaction.
It is catalogued but not exercised.

The pipeline runs in this order. The two deterministic steps never depend on the model:

1. **Deterministic secret scrub** of the transcript before any model sees it: key formats (`sk-…`,
   `ghp_…`, `AKIA…`, `xox…`, PEM blocks, URLs with `user:pass@`), plus a high-entropy token
   detector. Matches are replaced with `[REDACTED]`. A second scrub runs on the model's output
   before write. Track B (section 6.5) shows why the model alone cannot be trusted with this.
2. **Extraction** by the curator model into the fact schema (JSON-schema-constrained output).
3. **Entity resolution**: exact name and alias match against `entities` within the scope first; the
   model is used only for leftovers.
4. **Deterministic upsert** (6.3) when subject and attribute resolve to an existing key.
5. **Model judgement only on a candidate set**: FTS5 + entity recall returns at most 5 nearby active
   facts, and the model answers "duplicate of?" and "which does this supersede?" over those only.
6. **Policy gate** (6.4), then write with provenance (session id, message ids, tool callIDs).

Cost: one curator call per idle session (about 1–3k tokens in), plus at most two candidate
judgements.

**Use the agent's own model, at idle.** At `session.idle` that session's model is free. Loading a
second small model for curation does not work on a default single-GPU ollama
(`OLLAMA_NUM_PARALLEL=1`, ollama 0.33.3). With qwen3:32b serving requests, a request to
lfm2.5-thinking got no answer within 150 s, even after the probe's own 80 s qwen3 request had
finished, because qwen3's queue never drained (`evidence/memory/ollama-two-models.log`). A second
model is starved or forces swaps of a 20 GB runner.

### 6.3 The upsert (W1, measured)

One transaction:

1. Read the active facts with the same `(scope, subject, attribute)`.
2. Same normalized value: **skip** (record the new source as extra provenance).
3. Different value: insert the new fact and set `superseded_by` on the old one (history kept; no
   in-place update).
4. No match: insert.

Measured p50 at 50k facts: SQLite 0.23 ms, Neo4j 22 ms, flat files 24 ms. At 500k: SQLite 0.19 ms.
All matched the oracle on all 36 planted writes per scale (12 duplicates, 12 changed values, 12 new
keys).

### 6.4 What a model may decide on its own

- A model-captured fact **may supersede** another model-captured `project` or `reference` fact.
- It **may not silently supersede** a `user` or `feedback` fact, or anything written explicitly by
  the user. Those open a row in `conflicts` and go into the next Telegram digest as a question.
- Hearsay, hypotheticals and temporary states are rejected at extraction (Track B planted all three).
- Duplicate merges only skip the new fact; nothing is deleted. A wrong merge loses the new wording,
  never the old fact.

### 6.5 Model tiers for curation

*[Track B results: pending. This section will hold the per-model table from
`evidence/memory/curation-results.json` and the tier rule derived from it.]*

### 6.6 Promotion

The parent promotes a lead fact (user preference learned by a lead, a shared credential location,
a cross-project decision) by writing a `global` fact in the parent scope with
`derived_from = [lead fact]`. Leads never write `global`.

## 7. Recall path

1. **Every prompt.** The `chat.message` hook (verified) adds one synthetic text part, at most about
   800 tokens, built from:
   - FTS5 matches on the user's message (Q8);
   - facts about entities named in the message (Q1, alias-aware);
   - the decay ranking of the caller's visible set (Q3).

   It is filtered by the caller's visibility (section 5), deduplicated, top 8–12. Measured cost
   with `bun:sqlite` at 50k facts, p50 (`bun-sqlite.log`):

   | query | macOS | Linux |
   |---|---|---|
   | entity recall | 0.46 ms | 0.88 ms |
   | FTS recall | 0.53 ms | 1.05 ms |
   | decay top-10 (ranked in JS) | 0.78 ms | 1.63 ms |

   That is noise next to an LLM turn.
2. **Session start.** The same block, built from the decay ranking alone: the `MEMORY.md`
   equivalent. It is delivered with the first message's `chat.message` rather than
   `experimental.chat.system.transform` (P2: the model ignored refreshed system text).
3. **On demand.** `memory_search` for depth: provenance (Q4), 2-hop neighbours with hubs capped
   (Q6), raw entity history including superseded facts.
4. **Parent only.**
   - Lead digest (Q7a).
   - Cross-project links: "lead M already knows this about Redis" (Q2).
   - Open conflicts for review (Q5).

   All are dashboard or digest queries, not per-prompt.

## 8. Dedupe, update, delete

- **Dedupe.**
  - Structured duplicates (same key, same value) are caught by the upsert, no model needed.
  - Paraphrases with unresolved keys go to the model, over FTS candidates only.
  - The corpus plants 7% paraphrase duplicates. Track B tests the model judgement on 24 cases,
    including traps: same wording in another project, an added detail, a changed value.
- **Update.** Supersede, never overwrite. Q5 lists keys with more than one active value (unresolved
  conflicts). The upsert prevents new ones for structured keys; model captures that the policy gate
  refused land in `conflicts`.
- **Delete.**
  - `memory_forget`: soft delete (`deleted_at`) plus removal of the FTS row, so recall stops
    immediately.
  - A periodic purge hard-deletes after 30 days, then `VACUUM`.
  - A leaked secret gets an immediate hard purge plus `VACUUM`, and the export mirror is rewritten.

## 9. Provenance

Every fact points at the deepest source it came from: a file read inside a tool call, inside an
opencode session, started by a Telegram message. `sources.parent_id` links that chain to its root,
and `fact_derivation` links summaries and promotions to their inputs. Q4 returns the full upstream
closure (facts and sources). It matched the oracle at every scale. With the join order pinned by
`CROSS JOIN`, it costs 0.017 ms at 50k and 0.022 ms at 500k (`sqlite-tuning-*.log`); Neo4j takes
5.2 ms and 2.7 ms.

## 10. Recency and decay

`score = importance × 0.5 ^ (age_days / half_life(type))`. Half-lives: `user` 180 d, `feedback`
120 d, `project` 30 d, `reference` 90 d. These are the benchmark's values (`gen_memory.py`,
`HALF_LIFE_DAYS`) and are tunable. Recall bumps `last_recalled_at` / `recall_count`. Reinforcement
by recall is a follow-up and was not benchmarked. `bun:sqlite` has no user-defined functions, and
`pow()` exists in both measured builds, so ranking can stay in SQL or move to JS. JS ranking of
the visible set measured 0.8–1.6 ms at 50k.

## 11. Storage engine: flat files vs SQLite vs Neo4j

### 11.1 Method

- **Corpus.** `gen_memory.py` builds a seeded corpus and a ground-truth oracle that shares no code
  with the backends. It has:
  - 8 leads plus the parent at 600 facts, scaling to 624 leads at 500k;
  - 180 days of timestamps, and entities shared across projects (with aliases);
  - 7% paraphrase duplicates;
  - 2.5% unresolved and 2% resolved contradictions, 5% updates (some chained);
  - 5% derived and 1.5% promoted facts;
  - a source tree.

  The self-check asserts that the oracle's Q5 answer equals the planted unresolved set. The output
  is byte-identical across runs (sha256 in `gen.log`).
- **Backends.** All three load the same JSON:
  - **files**: the Claude Code layout exactly, frontmatter carrying the structured fields, queried
    statelessly with ripgrep plus file reads, as an agent would;
  - **naive**: the same files without entity metadata, used for Q1 only;
  - **sqlite**: stdlib `sqlite3`, WAL;
  - **neo4j**: Community 2026.09.0 in docker, heap 512m, pagecache 256m, python driver 6.4.0.
- **Protocol.** Per query: the first run on a fresh client, then 30 warm runs cycling the oracle's
  instances. "correct" means every run equalled the oracle. W1 runs last because it mutates the
  store.

### 11.2 Results (p50 / p95 ms, first run in brackets; every cell matched the oracle on every run)

#### 50,000 facts

| query | files | sqlite | neo4j |
|---|---|---|---|
| Q1 recall by entity | 1,042 / 1,295 (1,046) | 2.75 / 3.71 (3.30) | 19.2 / 30.0 (208) |
| Q2 cross-project links | 2,531 / 2,991 (2,113) | 264 / 385 (379) | 174 / 213 (441) |
| Q3 recency/decay top-10 | 93.2 / 101 (107) | 1.86 / 2.06 (2.57) | 48.1 / 57.9 (339) |
| Q4 provenance chain | 35.2 / 67.9 (23.5) | 6.82 / 8.85 (9.37) | 5.23 / 8.84 (283) |
| Q5 unresolved contradictions | 2,706 / 3,459 (2,767) | 99.8 / 109 (110) | 221 / 328 (324) |
| Q6 2-hop expansion | 2,245 / 2,849 (2,326) | 12.7 / 163 (8.14) | 8.30 / 108 (238) |
| Q7a parent view of a lead | 26.0 / 30.5 (33.4) | 0.39 / 0.51 (1.07) | 10.3 / 16.3 (58.2) |
| Q7b lead view of parent | 41.4 / 43.8 (55.3) | 1.12 / 1.54 (1.62) | 19.5 / 22.8 (76.6) |
| Q8 keyword recall (FTS) | 67.5 / 74.5 (69.8) | 1.34 / 3.33 (0.79) | 10.2 / 17.4 (180) |
| W1 upsert with dedupe/supersede | 24.2 / 26.5 (24.7) | 0.23 / 0.82 (0.05) | 22.0 / 62.3 (48.5) |

Load: files 5.4 s and 202 MB on disk (50,126 files); SQLite 1.2 s and 34 MB; Neo4j 14.3 s and
516 MB.

#### 5,000 facts

Files are already 52–173 ms on scans. SQLite and Neo4j are at most 11 ms and 27 ms p95 on recall
(`bench-5000.log`).

#### 600 facts

Everything is fast. Files take 10–24 ms because each query spawns ripgrep (`bench-600.log`).

#### 500,000 facts (SQLite vs Neo4j; files extrapolate linearly from 5k→50k and were not run)

| query | sqlite p50 / p95 | neo4j p50 / p95 |
|---|---|---|
| Q1 recall by entity | 21.3 / 31.9 | 148 / 370 |
| Q2 cross-project links | 2,709 / 3,776 | 1,546 / 2,213 |
| Q3 decay top-10 | 17.3 / 18.2 | 362 / 423 |
| Q4 provenance (bench query) | 77.8 / 94.2 | 2.67 / 6.99 |
| Q4 with `CROSS JOIN` order (`sqlite-tuning-500000.log`) | 0.022 / 0.158 | — |
| Q5 unresolved contradictions | 1,109 / 1,577 | 2,019 / 2,751 |
| Q6 2-hop (bench query) | 167 / 1,739 | 19.1 / 1,278 |
| Q6 with `entity_degree` table (`sqlite-tuning-500000.log`) | 22.7 / 322 | — |
| Q7a / Q7b visibility views | 0.96 / 10.6 | 12.1 / 149 |
| Q8 keyword recall | 14.1 / 31.5 | 37.4 / 91.0 |
| W1 upsert | 0.19 / 0.55 | 20.6 / 103 |

### 11.3 What Neo4j costs on a laptop (measured unless marked)

From `neo4j-pull.log` and `neo4j-container.{json,log}`, Neo4j 2026.09.0 Community, linux/arm64,
OpenJDK 25 in the image:

| item | value |
|---|---|
| image | 1,122 MB unpacked, 406 MB compressed |
| first pull | 12.2 s on this connection |
| fresh container to Bolt answering | 10.9–13.6 s (3 runs) |
| restart with 50k facts | 10.2–12.4 s to Bolt, then 0.61–0.68 s for the first query (3 runs) |
| memory at heap 512m / pagecache 256m | 0.91 GB idle (docker stats; JVM RSS 0.87 GB), 1.17 GB after loading 50k, 1.21 GB peak under queries |
| memory at 500k facts | 1.5 GB (`bench-500000.log`) |
| smallest config that still loaded and correctly answered the 50k corpus | heap 96m / pagecache 16m (still 0.67–0.75 GB resident) |
| heap 64m / pagecache 8m | dies with `Java heap space` during the load |
| disk | 516 MB before any data: two preallocated 257 MB transaction logs (`/data/transactions/{neo4j,system}`; this preallocation is configurable). 558 MB with the 50k store checkpointed (store 43 MB; SQLite 34 MB) |

These are from the final quiet run of `neo4j_container.py`. Two earlier runs, made while the
machine was busy with other probes, were slower (fresh start up to 18–24 s); they are superseded,
not averaged in.

**Install burden for a stranger** (desk-research-only):

- On macOS and Windows the container needs Docker Desktop: a Linux VM of several GB (this machine's
  VM is configured with 8 GB of RAM). Docker Desktop's licence requires a paid subscription for
  companies with 250 or more employees or more than $10M in annual revenue.
- The no-Docker path is the Neo4j tarball/zip plus a matching Java runtime; the 2026.09 image ships
  OpenJDK 25. On Windows that means the zip distribution, run as a service via `neo4j
  windows-service install`.
- Either way it is a second long-running server, with its own port, password and upgrade path.

### 11.4 Complexity

Lines of code for all ten operations plus load and helpers (non-blank, non-comment, counted from
`bench.py`): **files 154, SQLite 104, Neo4j 107**. Cypher is not shorter than SQL for these
queries. The extra flat-file code is parsing and cross-file link following.

### 11.5 Where SQLite stops being good enough

- **Not at the sizes a personal assistant reaches for interactive recall.** At 500k facts, entity,
  keyword, visibility and decay recall stay at 1–32 ms p95.
- **Two graph-shaped queries** (provenance, 2-hop) went bad at 500k while Neo4j stayed at 2.7 ms and
  19 ms. **Both were query-shape problems, not engine limits.**
  - Provenance: the planner scanned `facts` for the join with the recursive table. Pinning the join
    order took it from 78 ms to 0.022 ms.
  - 2-hop: it counted every candidate's mentions to apply the hub cap. A degree table maintained on
    write took it from 167 to 23 ms p50.
- **What stays slow at 500k is analytics:**
  - cross-project links over every entity: 2.7 s. This is the one query where Neo4j keeps a lead:
    1.5 s, and 174 vs 264 ms at 50k;
  - all open conflicts: 1.1 s. Neo4j is slower here: 2.0 s.

  Both are dashboard and digest work. The design maintains them incrementally (`conflicts` is
  written by the upsert; a per-entity scope count can be kept the same way as `entity_degree`)
  instead of scanning.
- **Flat files** stop being good enough around 5k facts. Full-scan queries take 130–230 ms there
  and 2–3 s at 50k.

## 12. Process model and concurrency

- **One SQLite file per user** (for example `~/.opencode-soma/memory.sqlite`), opened in WAL mode by
  three kinds of process:
  - each opencode instance's plugin (the parent and every lead);
  - the opencode-soma daemon (capture jobs, digest, export mirror);
  - the dashboard (read-only).
- **Concurrency.** WAL allows concurrent readers and one writer at a time. Writes are single
  sub-millisecond transactions (W1), so contention is negligible with `busy_timeout` set. WAL needs
  a local filesystem; it does not work on a network share. **desk-research-only**: SQLite's
  documented WAL constraints.
- **`bun:sqlite` is what a plugin gets.** opencode is a compiled Bun binary and plugins run inside
  it. `bun:sqlite` has FTS5, recursive CTEs, JSON1, UPSERT and WAL on macOS (system SQLite 3.51.0)
  and on Linux (Bun's bundled SQLite 3.51.2). It has **no user-defined SQL functions**
  (`evidence/memory/bun-sqlite.log`). Run `ANALYZE` / `PRAGMA optimize` after bulk loads: without
  it, Linux FTS recall at 50k was 18 ms; with it, 1 ms.
- **Windows: desk-research-only.** Bun ships `bun:sqlite` there too. Not measured here (no Windows
  host).
- **No vector index in v1.** `sqlite-vec` is a loadable extension, and Bun's documentation says
  macOS's system SQLite does not allow extensions without `Database.setCustomSQLite()` and a
  separate library build. That is a packaging risk on every OS. FTS5 plus entities plus decay is the
  v1 recall. **desk-research-only.**

## 13. Fallbacks

- **Corrupt or missing database.** WAL plus nightly `VACUUM INTO` backups; the markdown export
  mirror can rebuild facts, sources and links via the importer (section 14).
- **No model trustworthy enough for auto-capture.** Turn auto-capture off. Explicit
  `memory_save` / `memory_forget` still work, and so do deterministic dedupe and supersede. Tier
  rule in 6.5.
- **Runtime without `bun:sqlite`.** The daemon owns the database (any SQLite binding) and serves the
  plugin over localhost MCP/HTTP. The cost is a hop per recall, not a different store.
- **Tiny memories.** Below about 2k facts the flat-file mirror alone would work: 13–65 ms at
  600–5k facts for entity recall. It loses contradiction detection, cross-project links and fast
  provenance, so it is a degraded mode, not a choice.

## 14. Migration from claude-soma

The importer turns each `~/.claude/projects/<slug>/memory/*.md` into a fact:

- frontmatter `type` maps to `type`; the body becomes `text`; `name` and `description` are kept;
- the attribute is `note-<file>`, so imports never conflict;
- the scope comes from the lead registry's cwd → slug mapping, the same candidates `read_memory`
  tries; the orchestrator slug becomes `parent`;
- `[[links]]` become `fact_derivation` rows (related-to);
- `curator='import'`, and the source is `import:<path>`.

The curator can later split imported notes into keyed facts. The export mirror writes `MEMORY.md`
per scope, so the existing dashboard memory view keeps working until it moves to SQL.

## 15. Open items

- Track B results and the model tier table (6.5).
- `experimental.session.compacting` and plugin-defined tools are catalogued, not exercised. A
  plugin `tool` probe plus a forced compaction would close this.
- A Windows run of `bun_sqlite_probe.ts`.
- Reinforcement by recall (recall_count in the ranking), not benchmarked.
- Semantic recall (embeddings) deferred; see the packaging risk in section 12.
