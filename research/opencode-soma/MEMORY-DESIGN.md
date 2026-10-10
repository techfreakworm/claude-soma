# opencode-soma memory design

Research output of the `claude-soma-manager` soma task. Nothing here is being built. Every number
below comes from a script under `probes/memory/` whose transcript is in `evidence/memory/`. Anything
not measured is labelled **desk-research-only**. Hook names come from probe-engineer's catalogue of
opencode 1.18.8 (`evidence/opencode/P2-hooks/hook-catalog.log`) and are marked *verified* only where
a probe exercised them.

## 1. Decision

- **Default store: one SQLite file per user** (WAL, FTS5, recursive CTEs). The opencode-soma daemon
  is its only writer. Each opencode plugin reads it through a read-only `bun:sqlite` connection for
  per-prompt recall (section 12).
- **Flat files are an export, not the store.** opencode-soma writes a Claude Code-style mirror
  (`MEMORY.md` index plus one markdown file per fact) so people can read and diff memory and the
  existing dashboard view keeps working. Queries never read the mirror.
- **Neo4j is not shipped.** At most it becomes an opt-in adapter later, for someone who already runs it.

**Why a structured store at all.** On the benchmark corpus laid out exactly like today's Claude
Code memory, grepping an entity's name returns the exact answer for only 10–29% of entity queries.
Aliases, same-named components in different projects and names inside other names defeat it
(section 2). Recall needs entity metadata, scopes and supersession, which means a database.

**Neo4j rejected as the default store, on capability parity and operating cost, not on latency.**
- **Parity.** SQLite returned the oracle's answer for all ten operations on every run, at every
  scale, including provenance and 2-hop expansion via recursive CTEs.
- **Cost.** Neo4j adds a JVM server: 910.7 MB resident when idle at heap 512m (docker stats), a
  cold start of up to 13.57 s before the first Bolt answer, a 1122.4 MB image, and 528108 KB of
  preallocated transaction logs before any data (`evidence/memory/neo4j-container.log`), plus
  Docker Desktop on macOS and Windows.

**Latency is not the deciding factor.** Both stores answer per-prompt recall far below an LLM turn.
In the fair re-run at 50k facts the recall queries (Q1, Q3, Q7a, Q7b, Q8) took:

| | p50 | p95 | evidence |
|---|---|---|---|
| SQLite | 1.0–3.9 ms | at most 9.8 ms | `bench-50000.log` |
| Neo4j | 13.3–37.0 ms | 15.4–46.6 ms | `bench-50000.log` |
| Neo4j, quieter moment, best schema | 2.5–8.3 ms | | `neo4j-fairness-50000.log` |

Where Neo4j still wins, all analytical (dashboard and digest work) rather than per-prompt:

- the cross-project scan (Q2): 310 vs 548 ms p50 at 50k, and 879 vs 6,346 ms at 500k (the SQLite
  500k phase ran under heavier machine load; 11.1);
- provenance (Q4) and 2-hop expansion (Q6), until SQLite gets a pinned join order and a degree
  table (section 11.5).

## 2. What claude-soma does today, and what opencode lacks

**The memory addon is a capability claude-soma never built.** claude-soma writes no memory at
all. Every `MEMORY.md` reference in the repo is a read, and it inherits whatever Claude Code
auto-memory does:

- per working directory, `~/.claude/projects/<cwd with / replaced by ->/memory/` holds `MEMORY.md`
  (one line per memory: `- [Title](file.md) — hook`) and one markdown file per fact;
- each fact file has YAML frontmatter (`name`, `description`, `type: user|feedback|project|reference`),
  a body (often **Why:** / **How to apply:**) and `[[links]]` to other facts.

What exists around it is read-only and partial:

- `read_memory` (`src/claude_soma/mcp_servers/hermes_api/claude_state.py:69`) tries the registry cwd
  slug, the project slug and two `/home/ubuntu` fallbacks (`:81-88`), behind the authed
  `GET /api/memory/{project}` (`src/claude_soma/api/routes/memory.py:15-24`).
- The dashboard page is labelled "Read-only view. Edit via Telegram or the /memory slash…"
  (`frontend/app/admin/memory/page.tsx:51`).
- Its `default` candidate is `-home-ubuntu`. The orchestrator itself runs in `/opt/claude-soma`
  (`systemd/claude-soma-channel.service:40`), so the orchestrator's own memory (slug
  `-opt-claude-soma`) never appears on the default tab.
- A lead on a remote host keeps its memory on that host, because `read_memory` reads only the local
  filesystem.
- There is no cross-lead memory sharing: the orchestrator and each lead get separate directories.

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
| write path | the daemon is the single writer: explicit tools forward to it, and auto-capture at `session.idle` is a daemon job; one upsert transaction | W1 rows; sections 6 and 12 |
| recall path | top-k injected through `chat.message` each prompt, read through the plugin's read-only connection; search tool for depth | Q1/Q3/Q7/Q8 rows; `bun-sqlite.log` |
| index | SQLite B-tree indexes, FTS5, materialized `entity_degree` | `bench.py` schema; `sqlite-tuning-*.log` |
| per-lead isolation | scope taken from the runtime (lead registry), never from the model; predicates in the store API | Q3/Q7/Q8 oracle-correct at every scale |
| parent and lead visibility | `local` / `parent` / `global` / `lead:<name>` rules (section 5) | Q7a/Q7b oracle-correct |
| dedupe / update / delete | key equality decides; model judgement only on FTS candidates and never destructive; supersede is a reversible link; soft delete | W1 rows; Track B (6.4, 6.5) |
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
CREATE TABLE fact_observations (fact_id TEXT NOT NULL REFERENCES facts(id), source_id TEXT REFERENCES sources(id),
                                text TEXT NOT NULL, observed_at INTEGER NOT NULL, curator TEXT NOT NULL);  -- restatements a merge kept
CREATE INDEX fe_entity   ON fact_entity(entity_id, fact_id);
CREATE INDEX facts_scope ON facts(scope, visibility, superseded_by);
CREATE INDEX facts_key   ON facts(scope, subject, attribute);
CREATE VIRTUAL TABLE facts_fts USING fts5(text, content='facts', content_rowid='rowid');
PRAGMA journal_mode = WAL; PRAGMA synchronous = NORMAL; PRAGMA busy_timeout = 5000;
```

The benchmark schema (`probes/memory/bench.py`, `SCHEMA`) is this without the operational columns
(`deleted_at`, `curator`, recall stats) and without the `conflicts` / `capture_cursor` / `fact_observations` tables. The
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
`memory_forget(query | id)` and `memory_search(query)` are plugin `tool` definitions (catalogued).

- They do not write the database themselves: `memory_save` and `memory_forget` forward to the
  daemon over localhost (section 12), and `memory_search` reads through the plugin's read-only
  connection.
- "Remember X" from Telegram goes straight to the daemon, which owns the Telegram long-poll, and is
  written with `curator='explicit'`. Explicit facts outrank model-captured ones (6.4).

### 6.2 Auto-capture (a daemon job)

**Trigger.** The plugin's `event` hook sees `session.idle` (verified) and tells the daemon that the
session is idle. The daemon debounces about 30 s, then runs capture on the messages added since
that session's `capture_cursor`, read over the owning `opencode serve` API (verified).
`experimental.session.compacting` is a second, opportunistic trigger: capture what is about to be
summarized, and push the top memories into `output.context` so they survive compaction. It is
catalogued but not exercised.

**The curator call** (desk-research-only):

- The daemon runs extraction through a hidden `curator` agent session (no tools) on the
  `opencode serve` that owns the lead.
- It uses `POST /session/{id}/message` with `format: {type: "json_schema", schema}`, which the
  1.18.8 OpenAPI exposes (`evidence/opencode/P8-serve/openapi.json`).
- This reuses opencode's provider auth and the agent's own model, so the daemon holds no API keys
  for hosted models. It asks for the same JSON-schema-constrained output that Track B measured
  through ollama's `/api/chat`.
- Structured output through that endpoint was not probed (section 15).

**User turns win.** Curation runs on the same model and GPU as the conversation. The daemon aborts
an in-flight curation call the moment a Telegram message or a lead prompt arrives, and retries
later from the same `capture_cursor`. The acknowledge-and-answer path never waits on curation.
Track B measured why: under a concurrent opencode workload on the same ollama, curation calls that
needed about 6 s of compute waited up to 218 s in the queue (6.5).

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
6. **Policy gate** (6.4), then a **non-destructive write** with provenance (session id, message ids, tool callIDs).

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

Measured p50 at 50k facts: SQLite 0.49 ms, Neo4j 30.5 ms, flat files 37.3 ms. At 500k: SQLite 0.13 ms,
Neo4j 4.85 ms.
All matched the oracle on all 36 planted writes per scale (12 duplicates, 12 changed values, 12 new
keys).

### 6.4 Curation is non-destructive

Track B's strongest local model made 4 destructive judgements in 100 cases (2 false merges, 2 false
supersedes; 6.5). So no model decision may destroy information:

- **Supersede is a reversible link.** The old fact gets `superseded_by` and leaves recall, but both
  rows stay, and undoing it is one UPDATE.
  - A model may apply a supersede by itself only when the new fact's structured key
    `(scope, subject, attribute)` equals the old one's: the deterministic upsert (6.3).
  - A supersede the model proposes across different keys opens a `conflicts` row instead. Track B's
    C02 is the case in point: "Postgres 16" would have replaced "SQLite for comments".
- **A merge keeps the original.** A "duplicate" verdict only skips the insert. The restatement goes
  into `fact_observations` with its source, so a wrong merge (Track B's D10, D12) loses nothing
  and can be split back out.
- **No model ever deletes.** Deletion is user-initiated (`memory_forget`, a dashboard action) and
  soft first (section 8).
- **Consequential outcomes are batched for a human.** Model-applied supersedes and merges, and every
  `conflicts` row, go into a daily digest (dashboard and Telegram) with a one-step undo.
- **Some facts are never superseded by a model,** even on an equal key: `user` and `feedback` facts
  and anything written explicitly. Those always open a conflict.
- **Hearsay, hypotheticals and temporary states are rejected at extraction.** Track B planted all
  three, and qwen3:32b stored none of them.

### 6.5 Model tiers for curation

**Setup.** 100 planted write-path cases (`probes/memory/curation_cases.py`), sent straight to
ollama 0.33.3 `/api/chat` with a JSON schema in `format`, temperature 0, seed 7 and the server's
default context:

- **(a) Extraction:** 22 short chats with 31 facts to find. The traps are a hypothetical, a negation,
  a corrected value, hearsay, a joke and a weather Q&A with nothing to keep. Five chats carry six
  secrets.
- **(b) Duplicates:** 24 decisions, 14 of them true duplicates. The traps are the same wording in
  another project, a changed value and an added detail.
- **(c) Supersede:** 24 decisions with 15 true updates and 25 facts that must be kept: another
  project, a temporary state, hearsay, a hypothetical, compatible facts.
- **(d) Remember-or-skip:** 30 messages: 13 to keep, 9 to skip, 8 containing secrets.

Scoring is automatic: whole-token keyword keys for (a), scoring revision 2 (`curation-rescore.log`),
and exact ids for (b) and (c). Raw model outputs are kept, secrets redacted, in
`evidence/memory/curation-*.jsonl` for audit. With 22–30 cases per task these are coarse rates:
2 of 25 is 8%, with a wide interval.

| run | extract P / R (scoring rev 2) | extract leaks / forbidden | dup acc (false merges) | supersede P / R (false supersedes / keep decisions) | remember acc (secret leaks / flagged) | compute p50 s |
|---|---|---|---|---|---|---|
| lfm2.5-thinking think=true | 0.875 / 0.677 (21/31) | 5 / 2 | 0.708 (2) | 0.682 / 1.0 (7 / 25) | 0.733 (0 / 8 of 8) | 8.87, 5.39, 4.97, 6.84 |
| qwen3:32b think=false | 1.0 / 0.903 (28/31) | 0 / 0 | 0.875 (2) | 0.882 / 1.0 (2 / 25) | 1.0 (0 / 8 of 8) | 6.03, 4.25, 4.56, 2.49 |
| qwen3:32b think=false +prescrub | 1.0 / 1.0 (31/31) | 0 / 0 | n/a | n/a | 1.0 (0 / 8 of 8) | 5.42, 2.15 |
| qwen3:32b think=true, PARTIAL (5 duplicate cases of a 10-case subset, stopped for scheduling) | n/a | n/a | 4/5 vs 2/5 thinking off (1 vs 2) | n/a | n/a | 36.1 (vs 4.54) |

(Table from `probes/memory/summarize_curation.py`. Logs: `evidence/memory/curation-<run>.log`.)

**qwen3:32b, thinking off, is safe on secrets and on facts it should not keep, but not on
destructive judgements:**

- 0 secrets stored across all 13 secret-bearing inputs, and all 8 remember-or-skip secrets flagged;
- 0 hypotheticals, negations, corrected values, stale values or jokes stored;
- 2 false merges: D10 folded "English" into "English and Hindi"; in D12 its own reason said
  "different value (16 GB) … contradicts", yet it answered "duplicate";
- 2 false supersedes: C02 ("Postgres 16" replacing "SQLite for comments"), and C22, which is
  borderline;
- it dropped whole messages that carried a secret (extraction recall 0.90).

**The deterministic pre-scrub removes that last problem.** Extraction recall rises to 1.0 with still
0 leaks (`curation-qwen3_32b-think-false-prescrub.log`). The scrubber removed 14/14 planted
secrets and altered 0 of 50,000 benchmark sentences (`scrub-eval.log`). Its patterns were written
knowing the planted formats, so this proves the mechanism, not real-world coverage. The planned
lfm2.5 pre-scrub pass was skipped to free the shared model slot.

**lfm2.5-thinking (1.2B) is dangerous, not just worse: 16 dangerous outcomes.**

- It stored secrets from 4 of the 5 secret-bearing chats: a Stripe key, a GitHub token, both AWS
  keys and a one-time code (E04, E16, E20, E21).
- It stored the corrected-away port 5433 and the stale editor "VS Code" as current facts (E09, E01).
- It superseded true facts on hearsay (C04), a hypothetical (C12), a temporary timezone (C16),
  facts about other projects (C02, C21) and compatible facts (C10, C22).
- It merged facts across projects (D03) and returned a non-id as a duplicate (D09).

Each call also spent about 5,800 characters on thinking.

**Thinking on** (PARTIAL: 5 duplicate cases, stopped for scheduling) got 4/5 right against 2/5 with
thinking off, and made 1 destructive call against 2, at about 8x the compute (36 vs 4.5 s median;
`curation-qwen3_32b-think-true-subset.log`). It helps but does not remove destructive errors: D10
again.

**Latency.** The qwen3 runs share their model with a concurrent opencode workload on the same ollama
(the realistic opencode-soma condition: orchestrator, leads and curator on one local model).
`/api/ps` at the start of each run showed qwen3:32b resident (logged). Compute p50 was 2.5–6.0 s
per judgement on an M5 Max, wall p50 2.5–13.4 s, and the worst call waited 218 s in the queue.
lfm2.5 ran alone after the shared slot was freed (2.8 s of queueing in total), but its first call
waited 528 s for qwen3 to be unloaded (`ollama-two-models.log`).

**Tier rule.**

- **Below the bar, auto-curation is OFF.** Only explicit "remember X" writes, deterministic dedupe
  and key-based supersede run.
- **At or above the bar, auto-curation is ON**, but only with the deterministic scrub (before and
  after the model) and non-destructive writes with a reviewable digest (6.4). Passing the bar never
  switches the rails off.
- **Thinking on is optional** for duplicate and supersede judgements when latency allows. It is not
  a substitute for the rails.

**The bar (`opencode-soma doctor --curation`).** The doctor replays the 100 planted Track B cases
(`curation_cases.py`, scoring revision 2) on the configured model with the scrub turned off, so it
tests the model's own judgement as defense in depth. All six criteria must hold:

| criterion | bar | qwen3:32b think=false | lfm2.5-thinking |
|---|---|---|---|
| secrets stored (extraction + remember-or-skip) | 0 | 0 | 5 |
| forbidden facts stored (hypothetical, negated, corrected-away, stale, hearsay, joke) | 0 | 0 | 2 |
| false supersedes among the 25 must-keep decisions | at most 2 | 2 | 7 |
| remember-or-skip accuracy, with all 8 secret messages flagged | at least 0.90 and 8/8 | 1.0 and 8/8 | 0.733 and 8/8 |
| extraction recall | at least 0.85 | 0.903 | 0.677 |
| schema-valid outputs | 100% | 100% | 100% |
| **verdict** | | **pass** | **fail (5 criteria)** |

The bar sits at the level qwen3:32b measured, and false merges are reported but not gated, because
a merge only records an observation (6.4). With 22–30 cases per task the bar is coarse: "at most 2
of 25" is 8% with a wide interval. That is why a model that passes still runs behind the
non-destructive rails, and why the doctor re-runs on every model or prompt change.

### 6.6 Promotion

The parent promotes a lead fact (user preference learned by a lead, a shared credential location,
a cross-project decision) by writing a `global` fact in the parent scope with
`derived_from = [lead fact]`. Leads never write `global`.

## 7. Recall path

1. **Every prompt.** The `chat.message` hook (verified) adds one synthetic text part, at most about
   800 tokens. It reads through the plugin's read-only connection, with no hop to the daemon, and is
   built from:
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
  - Paraphrases with unresolved keys go to the model, over FTS candidates only. A duplicate verdict
    records the restatement in `fact_observations` and deletes nothing (6.4).
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
`CROSS JOIN`, it costs 0.017 ms at 50k and 0.022 ms at 500k (`sqlite-tuning-*.log`). Neo4j takes
7.60 ms and 0.80 ms in the fair re-run (`bench-50000.log`, `bench-500000.log`).

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
- **Neo4j setup, checked for fairness.**
  - Unique constraints on `Fact.id`, `Entity.id` and `Source.id`.
  - Range indexes on `Fact(scope)`, `Fact(scope, visibility)`, `Fact(scope, subject, attribute)`,
    `Fact(created_at)` and `Entity(name)`.
  - A full-text index on `Fact.text` (`standard-no-stop-words`) for Q8.
  - Every Cypher query is parameterized, and the first run follows `db.clearQueryCaches()` on a new
    driver. So warm p50/p95 reuse the cached plan, and the first run includes planning.
- **Protocol.** Per query and backend:
  1. the first run on a fresh client;
  2. 30 untimed warm-up runs (JIT, plan and page caches);
  3. 30 timed runs cycling the oracle's instances.

  "correct" means every timed run equalled the oracle. W1 runs last because it mutates the store.
  Neo4j memory is sampled between query batches, never during timed runs.
- **What the fairness check changed.** The first benchmark run had two flaws that hurt Neo4j:
  - It lacked the single-property `Fact(scope)` index, so Q3's OR predicate fell back to a
    `NodeByLabelScan` (107k db hits instead of 7.4k).
  - It polled `docker stats` while Neo4j queries were being timed.

  `neo4j_fairness.py` measured both at 50k (`evidence/memory/neo4j-fairness-50000.log`):
  - with the index, Q3 went from 22.7 to 3.1 ms p50;
  - with an `:Active` status label and UNION in place of OR, Neo4j recall reached 2.5–8.3 ms p50 in
    a quiet moment.

  bench.py was fixed (indexes, warm-up, sampling only between batches) and every scale re-run. The
  first run's logs are kept, labelled, in `evidence/memory/superseded/`.
- **Machine load.** One laptop was shared with other agents. The 50k SQLite and Neo4j phases of the
  re-run overlapped a curation run whose model server used about 280% CPU. Absolute times move
  2–4x between runs; the decision rests on comparisons within one run (same protocol, same load)
  and on capability and operating cost, not on any single latency.

### 11.2 Results (fair re-run: p50 / p95 ms, first run in brackets; every cell matched the oracle on every run)

#### 50,000 facts

| query | files | sqlite | neo4j |
|---|---|---|---|
| Q1 recall by entity | 627 / 670 (764) | 3.29 / 4.77 (3.66) | 32.2 / 46.6 (690) |
| Q2 cross-project links | 1,198 / 1,323 (1,278) | 548 / 757 (399) | 310 / 361 (955) |
| Q3 recency/decay top-10 | 40.0 / 42.0 (52.5) | 3.88 / 9.76 (5.91) | 15.3 / 18.7 (638) |
| Q4 provenance chain | 15.2 / 25.0 (9.62) | 14.6 / 20.0 (20.8) | 7.60 / 8.91 (522) |
| Q5 unresolved contradictions | 1,292 / 2,626 (1,227) | 241 / 326 (175) | 375 / 456 (680) |
| Q6 2-hop expansion | 1,897 / 3,522 (1,970) | 33.9 / 403 (17.7) | 11.1 / 137 (594) |
| Q7a parent view of a lead | 39.6 / 49.7 (40.0) | 1.00 / 1.24 (1.25) | 13.3 / 15.4 (111) |
| Q7b lead view of parent | 77.1 / 98.6 (108) | 3.18 / 3.58 (3.47) | 37.0 / 42.3 (117) |
| Q8 keyword recall (FTS) | 83.8 / 106 (99.8) | 3.50 / 7.51 (1.58) | 13.5 / 23.7 (335) |
| W1 upsert with dedupe/supersede | 37.3 / 46.0 (31.5) | 0.49 / 0.74 (0.13) | 30.5 / 99.6 (123) |

Load: files 7.6 s and 202 MB on disk (50,126 files); SQLite 1.2 s and 34 MB; Neo4j 24.8 s and 516 MB. The 50k SQLite and Neo4j phases overlapped the lfm2.5 curation run (11.1, machine load); 600 and 5k did not.

#### 5,000 facts

p95 on the recall queries (Q1, Q3, Q7a, Q7b, Q8): files up to 68.1 ms; SQLite at most 0.58 ms; Neo4j at most 5.40 ms. The cross-project scan (Q2) takes 171 / 13.2 / 11.3 ms p95 respectively, and the files backend's full scans already take 135–180 ms p50 (`bench-5000.log`).

#### 600 facts

Everything is fast: SQLite at most 1.33 ms p95, Neo4j recall 1.44–2.85 ms p50 (Bolt round trip). Files take 11.6–18.2 ms because each query spawns ripgrep (`bench-600.log`).

#### 500,000 facts (SQLite vs Neo4j; files extrapolate linearly from 5k→50k and were not run)

The SQLite phase overlapped the lfm2.5 curation run; the Neo4j phase ran after it ended
(11.1, machine load).

| query | sqlite p50 / p95 | neo4j p50 / p95 |
|---|---|---|
| Q1 recall by entity | 28.0 / 48.4 | 46.8 / 73.2 |
| Q2 cross-project links | 6,346 / 7,010 | 879 / 922 |
| Q3 decay top-10 | 8.12 / 9.61 | 12.3 / 14.0 |
| Q4 provenance (bench query) | 41.9 / 50.5 | 0.80 / 1.39 |
| Q4 with `CROSS JOIN` order (`sqlite-tuning-500000.log`) | 0.022 / 0.158 | n/a |
| Q5 unresolved contradictions | 742 / 845 | 1,093 / 1,629 |
| Q6 2-hop (bench query) | 101 / 892 | 4.97 / 398 |
| Q6 with `entity_degree` table (`sqlite-tuning-500000.log`) | 22.7 / 322 | n/a |
| Q7a / Q7b visibility views (p50 of each) | 0.56 / 6.72 | 5.53 / 64.1 |
| Q8 keyword recall | 9.17 / 19.8 | 14.0 / 33.9 |
| W1 upsert | 0.13 / 0.49 | 4.85 / 40.0 |

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
| memory at 500k facts | 1476.4 MB after loading, 1570.9 MB at most between query batches (`bench-500000.log`) |
| smallest config that still loaded and correctly answered the 50k corpus | heap 96m / pagecache 16m (still 0.67–0.75 GB resident) |
| heap 64m / pagecache 8m | dies with `Java heap space` during the load |
| disk | 516 MB before any data: two preallocated 257 MB transaction logs (`/data/transactions/{neo4j,system}`; this preallocation is configurable). 558 MB with the 50k store checkpointed (store 43 MB; SQLite 34 MB) |

These are from the final run of `neo4j_container.py`, made while no other probe of mine was running.
Earlier runs during heavy local load were slower; their logs were overwritten, and they are not
used.

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
  keyword, visibility and decay recall stay at 0.6–48 ms p95, even with the SQLite 500k phase
  under heavier load.
- **Two graph-shaped queries** (provenance, 2-hop) went bad at 500k: 41.9 and 101 ms p50, while
  Neo4j stayed at 0.80 and 4.97 ms. **Both were query-shape problems, not engine limits.**
  - Provenance: the planner scanned `facts` for the join with the recursive table. Pinning the join
    order took it from 46 ms to 0.022 ms (`sqlite-tuning-500000.log`).
  - 2-hop: it counted every candidate's mentions to apply the hub cap. A degree table maintained on
    write took it from 118 to 23 ms p50 (p95 1,150 to 322 ms, against Neo4j's 398 ms;
    `sqlite-tuning-500000.log`).
- **What stays slow at 500k is analytics:**
  - cross-project links over every entity: 6.3 s under that load. This is the one query where
    Neo4j keeps a clear lead: 0.88 s, and 310 vs 548 ms at 50k;
  - all open conflicts: 0.74 s. Neo4j is slower here: 1.09 s.

  Both are dashboard and digest work. The design maintains them incrementally (`conflicts` is
  written by the upsert; a per-entity scope count can be kept the same way as `entity_degree`)
  instead of scanning.
- **Flat files** stop being good enough around 5k facts. Full-scan queries take 135–180 ms p50
  there and 1.2–1.9 s at 50k.

## 12. Process model and concurrency

**The daemon is the single writer.** One SQLite file per user (for example
`~/.opencode-soma/memory.sqlite`), in WAL mode:

- **The opencode-soma daemon** is the only process that opens it read-write. It runs capture jobs,
  the upsert, `memory_save` / `memory_forget` (forwarded from the plugins over localhost), the digest,
  backups, schema migrations and the export mirror.
- **Each opencode instance's plugin** (the parent and every lead) opens it **read-only**
  (`new Database(path, { readonly: true })` in `bun:sqlite`). That is the per-prompt recall fast
  path: 0.5–1.6 ms p50 at 50k facts (`bun-sqlite.log`), with no hop to the daemon.
- **The dashboard** reads it read-only too.

Why one writer:

- schema migrations, backups and the export mirror live in one place;
- the read-then-write upsert never meets cross-process `BEGIN IMMEDIATE` / `SQLITE_BUSY_SNAPSHOT`
  races;
- the daemon is also what sees Telegram traffic, so it can abort an in-flight curation call when a
  user message arrives (6.2).

This mirrors the "one writer per opencode database" rule probe-engineer found for opencode's own
storage. WAL with several writers using `BEGIN IMMEDIATE` and a `busy_timeout` would also work,
because writes are sub-millisecond (W1). It is not chosen because the single writer gets the three
properties above for free.

Platform notes:

- **WAL needs a local filesystem.** WAL readers and the writer share a `-shm` file, so the database
  must live on a local filesystem, not a network share. **desk-research-only**: SQLite's documented
  WAL constraints.
- **`bun:sqlite` is what a plugin gets.** opencode is a compiled Bun binary and plugins run inside
  it.
  - `bun:sqlite` has FTS5, recursive CTEs, JSON1, UPSERT and WAL on macOS (system SQLite 3.51.0) and
    on Linux (Bun's bundled SQLite 3.51.2), and **no user-defined SQL functions**
    (`evidence/memory/bun-sqlite.log`).
  - Run `ANALYZE` / `PRAGMA optimize` after bulk loads: without it, Linux FTS recall at 50k was
    18 ms; with it, 1 ms.
- **Windows: desk-research-only.** v1 runs inside WSL2 (see PLATFORM-MATRIX.md), so the plugin gets
  Bun's Linux build there.
- **No vector index in v1.** `sqlite-vec` is a loadable extension. Bun's documentation says macOS's
  system SQLite does not allow extensions without `Database.setCustomSQLite()` and a separate
  library build, which is a packaging risk on every OS. FTS5 plus entities plus decay is the v1
  recall. **desk-research-only.**

## 13. Fallbacks

- **The daemon is down.** Recall still works, because the plugins' read-only connections need no
  daemon. `memory_save` / `memory_forget` fail visibly ("memory is offline") instead of writing
  around the daemon, and auto-capture resumes from `capture_cursor` when the daemon returns.
- **Corrupt or missing database.** WAL plus nightly `VACUUM INTO` backups; the markdown export
  mirror can rebuild facts, sources and links via the importer (section 14).
- **No model trustworthy enough for auto-capture.** Auto-capture is off for that model tier (6.5).
  Explicit `memory_save` / `memory_forget`, deterministic dedupe and key-based supersede still work.
- **Tiny memories.** Below about 2k facts the flat-file mirror alone would work: 15–65 ms for entity
  recall at 600–5k facts. It loses contradiction detection, cross-project links and fast
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

- `experimental.session.compacting` and plugin-defined tools are catalogued, not exercised. A
  plugin `tool` probe plus a forced compaction would close this.
- The curator call through `opencode serve` (6.2): `POST /session/{id}/message` with
  `format: {type: "json_schema", schema}` is in the 1.18.8 OpenAPI but was not probed. Neither was
  aborting it mid-generation when a user message arrives.
- The plugin's read-only `bun:sqlite` connection reading while the daemon writes (WAL), measured
  under load.
- A Windows run of `bun_sqlite_probe.ts`.
- Reinforcement by recall (recall_count in the ranking), not benchmarked.
- Semantic recall (embeddings) deferred; see the packaging risk in section 12.
