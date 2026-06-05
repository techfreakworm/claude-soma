# Engagement Draft Schema — v1 (PROPOSAL, awaiting user sign-off)

**Status:** PROPOSED 2026-06-06. NOT YET ACTIVE.
**Sign-off required from:** operator (user).
**Producers that must conform:** the X harvest+draft subagent
(`scripts/engagement-browse-draft-subagent.txt`) AND social-manager via
the LinkedIn refill brief embedded in the same file. Renderer:
`scripts/engagement-hourly-drip.py :: regenerate_review_page`.

This document is the **single source of truth** for the
`/var/lib/claude-soma/engagement/queue.jsonl` line format. Producers and
the renderer MUST conform; any future shape change increments
`schema_version` (no silent drift).

---

## Why this exists

Before v1 the queue had three drifting shapes:

| Producer | excerpt field used | why_engage? | id format |
| --- | --- | --- | --- |
| X subagent (browse-x.js) | `source_post_excerpt` | no | `eng-x-<unix>-<6hex>` (per prompt) |
| social-manager (LI) | `source_excerpt` | yes | `eng-li-<unix>-<6hex>` (per brief) |
| renderer | reads `source_excerpt`, `why_engage` | n/a | reads `id` verbatim |

Result: 73/129 queue entries had `why_engage` populated, 56/129 had
`source_post_excerpt`, 73/129 had `source_excerpt`, and the live entries
in the queue use a bare 10-hex `id` that matches neither documented
format. The review doc consequently shows different shape and different
completeness from hour to hour. v1 freezes one contract.

---

## v1 fields

Every queue line MUST be a single JSON object with the keys below.
Unknown keys are tolerated (forward-compat). Missing required keys mean
the producer is broken — the renderer logs and skips the line.

### Identity (required)

| field | type | notes |
| --- | --- | --- |
| `schema_version` | string | Literal `"engagement.v1"`. |
| `id` | string | Stable user-facing ID. Format: `eng-{x|li}-{6hex}`, e.g. `eng-x-7k3f2a`. Short enough to say aloud + paste back in Telegram. Producers MUST mint this; renderer MUST NOT rewrite it. |
| `platform` | string | `"x"` or `"linkedin"`. |

### Source (required)

| field | type | notes |
| --- | --- | --- |
| `source_author` | string | `@handle` for X, `Firstname Lastname` for LinkedIn. |
| `source_permalink` | string | Canonical post URL. X: `https://x.com/<handle>/status/<id>`. LinkedIn: `https://www.linkedin.com/feed/update/urn:li:activity:<id>/`. Empty/null = un-postable; producer MUST drop the draft instead of emitting a bad line. |
| `source_excerpt` | string | First ~280 chars of the source post. Verbatim, no formatting. **Field name is `source_excerpt` — NOT `source_post_excerpt` (deprecated, removed in v1).** |

### Decision context (required — this is the "decision-useful" surface the v1 schema adds)

| field | type | notes |
| --- | --- | --- |
| `why_engage` | string | One line, ≤ 200 chars. Why this post is worth a comment for *this* operator. Concrete angle, not generic praise. Example: "Open-source skill library for Bayesian modeling — affirm + add the guardrails angle." |
| `topic` | string | One of the FROZEN tag set below. |
| `relevance_note` | string | Optional, ≤ 200 chars. Use only when the topic alone doesn't carry the "why this matters now" beat (e.g. "first OSS implementation I've seen of X", "author is one of the original MCP designers", "Mayank's last post touched the same theme"). Empty string `""` if unused. |

#### Frozen `topic` tag set (v1)

The renderer treats anything not in this list as `"other"` and logs a
warning. Add new tags via a schema-version bump only.

- `claude-code` — Claude Code CLI, hooks, MCP servers, plugins, agent SDK
- `agents` — multi-agent architecture, agent runtimes, agent design
- `mcp` — Model Context Protocol, MCP servers, MCP clients
- `infra` — deployment, ops, monitoring, CI/CD, hosting, OCI / cloud
- `dev-tooling` — IDE, build systems, dev workflows, tooling
- `ai-research` — papers, models, evals, benchmarks
- `voice` — STT / TTS / voice agents
- `other` — fallback when nothing above fits

### Draft (required)

| field | type | notes |
| --- | --- | --- |
| `draft_text` | string | 1–3 sentence comment in the operator's voice. Terse, technical, no emoji, no "great post!" filler. ≤ 600 chars (the LinkedIn comment cap is 1250, X reply 280 — keep room). |

### Lifecycle (required: `status`, `queued_at`; rest set by the drip script)

| field | type | notes |
| --- | --- | --- |
| `status` | string | One of `queued`, `pending_review`, `approved`, `posted`, `failed`, `declined`. Producers MUST emit `queued`. |
| `queued_at` | float | Unix epoch when the producer appended the draft. |
| `freshly_drafted_at` | float | Only set by the X fresh-hour subagent. Pool drafts (social-manager LI refills) MUST omit this field. The hybrid drip's "fresh" filter keys on it. |
| `released_at` | float | Set by the drip when `queued → pending_review`. |
| `approved_at` | float | Set by approve action. |
| `posted_at` | float | Set by `mark_posted` / `mark_posted_error`. |
| `post_permalink` | string | Set by `mark_posted` to the live posted-comment URL. |
| `post_error` | string | Set by `mark_posted_error`. |
| `declined_at` | float | Set by decline action. |
| `decline_reason` | string | Set by decline action. |

### Fields removed in v1

- `source_post_excerpt` — renamed to `source_excerpt`. Migration: existing
  rows are read with the legacy name as a fallback for one schema version,
  but no new writes use it. v2 will drop the fallback.

---

## Producer contracts

### X harvest+draft subagent (`scripts/engagement-browse-draft-subagent.txt`)

The subagent reads `engagement-browse-x.js` harvest output (which itself
emits `source_post_excerpt` — `browse-x.js` MUST be updated to emit
`source_excerpt` instead, or the subagent MUST rename on the fly).

Each draft line MUST be exactly:

```json
{
  "schema_version": "engagement.v1",
  "id": "eng-x-<6hex>",
  "platform": "x",
  "status": "queued",
  "queued_at": <unix-float>,
  "freshly_drafted_at": <run-start-unix-int>,
  "source_author": "<handle from harvest>",
  "source_permalink": "<URL from harvest — REQUIRED>",
  "source_excerpt": "<first ~280 chars>",
  "why_engage": "<1-line rationale>",
  "topic": "<one of the frozen tag set>",
  "relevance_note": "",
  "draft_text": "<the humanized comment>"
}
```

Generate the hex with `openssl rand -hex 3`. Null/empty `source_permalink`
→ SKIP that post (do NOT emit a draft for it).

### social-manager LinkedIn refill (the brief `send_to_project` sends)

Same JSON shape, with `platform: "linkedin"`, `id: "eng-li-<6hex>"`, and
NO `freshly_drafted_at` field (these are pool drafts, not "fresh this
hour" drafts — the dispatcher's hybrid drip pops them from the pool
oldest-first).

---

## Renderer contract (`regenerate_review_page`)

The renderer MUST output **identical structure regardless of queue
contents** — only the entries inside `## Pending Review` and `## Queued`
vary. The doc's shape is FROZEN below.

### Determinism rules

1. **No timestamps above the fold.** The `_Last regenerated_` line moves
   to the FOOTER so the top of the doc never churns.
2. **Stable sort within each section.** Sort by `queued_at` ascending
   (oldest pool first), ties broken by `id` lexicographic.
3. **Same per-entry block every time** — six fields, same order, same
   labels (see "Entry block" below).
4. **`topic`** rendered even when blank (as `(uncategorized)`) so the
   field is visually present every entry.
5. **No conditional sections.** "Pending Review (0)" still renders the
   heading + `_None._`. Same for Queued.

### Doc template (v1)

```markdown
# Engagement Review

_Schema version: engagement.v1_

**Actionable totals** — Pending review: {P} ({pending-counts})  ·  Queued: {Q} ({queued-counts})

Reply via Telegram to act on drafts:
- `approve <id>` — approve a single draft
- `approve all` — approve every pending_review draft
- `decline <id>` — decline a draft (won't post)

---

## Pending Review ({P})

{per-entry block, or "_None._"}

## Queued (next up — {Q})

{per-entry block, or "_None._"}

---

_Last regenerated: {iso8601}_
```

### Entry block (identical for Pending and Queued)

```markdown
### {id} · {Platform} · {source_author}

- **Topic:** {topic-or-(uncategorized)}
- **Source:** {source_permalink}
- **Why engage:** {why_engage-or-(no rationale)}
- **Source excerpt:**
  > {source_excerpt-or-(no excerpt)}
- **Draft:**
  > {draft_text}

`approve {id}` | `decline {id}`

---
```

Six lines of structured fields per entry, in this exact order, every
time. `relevance_note` is appended as a **7th line** only when non-empty:
`- **Note:** {relevance_note}` between "Topic" and "Source".

---

## Example rendered output (target shape)

```markdown
# Engagement Review

_Schema version: engagement.v1_

**Actionable totals** — Pending review: 2 (X: 1, LinkedIn: 1)  ·  Queued: 3 (X: 2, LinkedIn: 1)

Reply via Telegram to act on drafts:
- `approve <id>` — approve a single draft
- `approve all` — approve every pending_review draft
- `decline <id>` — decline a draft (won't post)

---

## Pending Review (2)

### eng-x-7k3f2a · X · @DanKornas

- **Topic:** ai-research
- **Source:** https://x.com/DanKornas/status/2062293484602880337
- **Why engage:** OSS Agent Skills for Bayesian modeling; affirm + add the guardrails angle (agents are bad at knowing when a model is misspecified).
- **Source excerpt:**
  > Your coding agent can write Bayesian models. This repo gives it guardrails. baygent-skills is a set of Agent Skills for Bayesian modeling, causal inference, and probabilistic thinking, built for Claude Code, Kimi Code, Cursor, Gemini CLI, and other agents.
- **Draft:**
  > the guardrails framing is the right one. coding agents nail the mechanics and are terrible at knowing when a model's misspecified. encoding the "what makes this wrong" checks as skills beats another prompt telling it to be rigorous. nice.

`approve eng-x-7k3f2a` | `decline eng-x-7k3f2a`

---

### eng-li-9m2p4d · LinkedIn · Jane Smith

- **Topic:** mcp
- **Note:** Jane is one of the original MCP spec authors.
- **Source:** https://www.linkedin.com/feed/update/urn:li:activity:7468100000000000000/
- **Why engage:** Concrete production MCP-server pattern; ask the sharp question about authn surface for stdio vs HTTP.
- **Source excerpt:**
  > We've been running 14 MCP servers in production for 6 months. Here's what we learned about stdio vs HTTP transports.
- **Draft:**
  > the stdio vs HTTP split is real. one thing we keep hitting: stdio collapses authn to "trust the parent process," which is fine for desktop but inverts the moment you put the server behind a network boundary. how do you handle authn for HTTP-mode MCPs in your stack?

`approve eng-li-9m2p4d` | `decline eng-li-9m2p4d`

---

## Queued (next up — 3)

### eng-x-...

... (same block shape)

---

_Last regenerated: 2026-06-06T01:51:26Z_
```

---

## Backwards-compatibility window

For one schema version (v1 → v2), the renderer reads both
`source_excerpt` and the legacy `source_post_excerpt` (preferring the
former). Producers MUST emit `source_excerpt` going forward; the legacy
fallback exists ONLY so historic queue entries still render correctly.

Existing rows that lack `why_engage`, `topic`, or `schema_version` are
rendered with `(no rationale)`, `(uncategorized)`, and an implicit
`v0` respectively — they remain readable, they just look obviously
incomplete next to v1 entries (which is fine — that visual contrast is
the migration signal).

---

## Open questions for the user

These are decisions that should be made before the implementation lands.
Defaults below are what the proposal codifies if the user accepts as-is.

1. **`id` format** — proposal: `eng-{x|li}-{6hex}`. Easy to say aloud,
   platform-prefixed, stable across the producer + renderer. Alternative:
   keep the bare 10-hex format. **Default: `eng-{x|li}-{6hex}`.**
2. **`topic` tag set** — proposal: the 8 frozen tags above. Adding new
   tags requires a schema-version bump. Alternative: free-form string.
   **Default: frozen 8-tag set.**
3. **`relevance_note` rendering** — proposal: appears as a 7th line only
   when non-empty. Alternative: always show as `- **Note:** (none)`.
   **Default: conditional 7th line.**
4. **Timestamp placement** — proposal: `_Last regenerated_` moved to the
   doc FOOTER (top stays churn-free). Alternative: keep at top.
   **Default: footer.**
5. **Legacy field fallback** — proposal: renderer reads
   `source_post_excerpt` as a fallback for one schema version, then v2
   drops it. **Default: one-version grace period.**
