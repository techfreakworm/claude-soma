# PLAN — FI-NOTIFY: lead → orchestrator notify channel

Author: soma-improver. Date: 2026-05-30. Subject: implementation plan for the
`FUTURE_IMPROVEMENTS.md` Orchestration sub-section "Lead → orchestrator notify channel",
scored P1, leverage 5, effort M, score 8.33 in `BUGS_PLAN.md`.

---

## TL;DR

- **Mechanism:** localhost HTTP API (background thread in `hermes_api`, port `HERMES_NOTIFY_PORT` default 9100) + SQLite spool (`lead_events` table in `registry.sqlite`). Confirms the prior recommendation with one important revision: the HTTP listener is a background thread inside the existing `hermes_api` MCP process, not a new uvicorn service (hermes_api has no uvicorn).
- **Lead-side surface:** a new `hermes-notify` MCP server (`mcp__hermes_notify__notify_orchestrator`) added to `lead-mcp.json` with a single tool. Leads call it like any other MCP tool; they have no awareness of Telegram or HTTP.
- **Orchestrator-side surface:** hybrid — the HTTP listener DMs the user immediately for urgent events (COMPLETED, NEEDS_INPUT, ERROR) via `_tg_post_json` in hermes_api; a new unconditional `UserPromptSubmit` hook (`notify_inject.sh`) injects recent unread events as `additionalContext` on every bot turn so the bot always has current lead status in-context.
- **Restart requirements:** one channel restart (picks up new `lead-mcp.json` stanza + new hook + new HTTP listener thread + updated system prompt). Existing running leads do not receive the tool until they are killed and respawned.
- **Effort:** ~590 LOC across ~9 files; estimated 1.5–2 days for a sonnet+max+seq-thinking subagent.

---

## Problem statement

Leads currently have no way to push a completion notification back to the orchestrator (the bot). As the user put it: "whenever a project lead finishes something it cannot tell the orchestrator hence I do not get to know on telegram." The only signaling direction today is bot→lead via `tmux send-keys`. The reverse does not exist because leads cannot hold the Telegram MCP (only one process may call `getUpdates` per bot token; a lead claiming the poller crashes the channel).

Result: the user must keep pinging the bot with "Status?" on every active lead. The bot must `capture-pane` every lead on every such ping, dispatching a background agent for each one (the reply-poll is unbounded). This friction was observed roughly eight times on 2026-05-30 alone. The fix is a one-way lead→orchestrator notification channel that lets leads push events without any Telegram access.

---

## Mechanism selection

### Re-evaluation of the four candidates

The criteria table from `FUTURE_IMPROVEMENTS.md`, reproduced for reference:

| Criterion | (1) inotify / systemd .path | (2) SQLite event table (poll-only) | (3) Localhost HTTP API | (4) Named pipe (FIFO) |
|---|---|---|---|---|
| **Durability across restarts** | Good — events persist on disk; .path unit must live outside the channel's cgroup or it dies on restart too | Excellent — unread rows survive indefinitely; bot drains on startup | Poor alone — a POST while the server is down is silently lost; SQLite spool cures this | Very poor — a write blocks until the reader is present; events are gone if the reader is not up |
| **Latency** | Good — inotify is instant; systemd service activation adds 1–2 s | Fair — bounded by poll interval (5 s typical) | Excellent — POST round-trip < 1 s; `_tg_post_json` adds ~300 ms | Excellent — but only while the reader is alive |
| **Ordering guarantees** | Fair — per-lead ordering preserved; rapid cross-lead events may be coalesced by systemd | Excellent — autoincrement rowid gives strict global ordering | Good — concurrent POSTs from multiple leads may interleave at the socket; per-lead ordering preserved | Fair — atomic only up to PIPE_BUF (4 096 B); larger payloads from concurrent leads interleave |
| **Fault isolation** | Excellent — one lead's file does not block another's | Excellent — a crashed INSERT rolls back cleanly (WAL mode) | Good — stateless POST handler; slow leads queue at the socket, do not block each other | Fair — a stalled reader stalls all writers after the pipe buffer fills |
| **Discoverability** | Good — convention-based path | Excellent — well-known DB path | Good — port from HERMES_NOTIFY_PORT env var | Excellent — fixed path |
| **Schema-friendliness** | Excellent | Excellent | Excellent | Good — newline-delimited JSON; payloads must stay under PIPE_BUF to avoid interleaving |

**Eliminated:** Named pipes — fatally absent durability; reader is a single point of failure.

**Eliminated as standalone:** SQLite poll-only — acceptable durability and ordering, but N-second delivery lag is observable friction for a user waiting on a COMPLETED event.

**Viable with a caveat:** inotify — requires an independent systemd .path unit outside the channel's cgroup; systemd activation coalescing may swallow rapid back-to-back events; 1–2 s activation latency is the worst of the durable options. Operationally fragile.

**Winner:** localhost HTTP API + SQLite spool — sub-second delivery in the normal case, zero event loss across restarts. The only combination that is both fast and lossless.

### Selected mechanism

**Localhost HTTP API + SQLite spool (`lead_events` table in `registry.sqlite`).**

The HTTP listener runs as a background thread inside the existing `hermes_api` MCP process (the same pattern already used by `_start_socket_server()` for the dashboard bridge). **Correction from the prior FUTURE_IMPROVEMENTS text:** hermes_api is a FastMCP stdio server, not a uvicorn service; it has no existing HTTP port. The right home for the new listener is a second background thread in hermes_api — this co-locates the DM logic (`_tg_post_json`, `_load_tg_token`) with the listener, avoids cross-process calls, and means the listener shares the channel's lifetime for natural drain-on-restart semantics.

Every `notify_orchestrator` MCP call (on the lead side) writes to `lead_events` first (guaranteed persistence regardless of server state), then POSTs to `127.0.0.1:HERMES_NOTIFY_PORT/notify` for immediate delivery. On bot restart, hermes_api drains any `delivered_at IS NULL` rows for urgent event types (COMPLETED, NEEDS_INPUT, ERROR) and retries DM delivery.

Port: `HERMES_NOTIFY_PORT` env var, default `9100`. Bound to `127.0.0.1` only (loopback, no external exposure). Documented in `secrets.env` as an operator-overridable knob.

SQLite table: `lead_events` in `/opt/claude-soma/registry.sqlite` (see DDL below).

Drain semantics on bot restart: at hermes_api startup, query `lead_events WHERE delivered_at IS NULL AND type IN ('COMPLETED','NEEDS_INPUT','ERROR') ORDER BY id ASC` and retry DM delivery for each row. `delivered_at` is set to `unixepoch('now','subsec')` on success. The drain runs in a background thread to avoid delaying MCP tool availability.

---

## Event schema

### Common envelope

All events share this outer shape. The lead name is read by the MCP tool from `HERMES_LEAD_NAME` (env); the lead does not supply it in the call.

```json
{
  "lead": "<lead-name>",
  "type": "STARTED|MILESTONE|COMPLETED|NEEDS_INPUT|ERROR",
  "ts": 1748641234.567,
  "payload": {}
}
```

The HTTP listener adds `id` (from SQLite autoincrement) and `created_at` (server-side timestamp). The `ts` field is the lead's own clock at the moment of the call; the server does not trust it for ordering (SQLite `id` is canonical order).

### Per-type payload shapes

#### STARTED

Realistic example:

```json
{
  "lead": "f1-tracker",
  "type": "STARTED",
  "ts": 1748891000.123,
  "payload": {
    "description": "Scraping 2024 Monaco GP qualifying results from the FIA timing portal",
    "eta": "~5 minutes"
  }
}
```

Required fields: `description` (non-empty string, max 500 chars).
Optional fields: `eta` (human-readable estimate string, max 100 chars; omit → no ETA shown in DM).
Validation: `description` must be non-empty. Unknown keys are silently ignored.

#### MILESTONE

```json
{
  "lead": "f1-tracker",
  "type": "MILESTONE",
  "ts": 1748891120.456,
  "payload": {
    "progress": "Fetched 15 of 20 drivers; writing to database",
    "percent": 75,
    "eta_remaining": "~2 minutes"
  }
}
```

Required: `progress` (non-empty string, max 300 chars).
Optional: `percent` (integer 0–100; omit → no percentage shown), `eta_remaining` (string, max 100 chars).
Validation: if `percent` is present, must be an integer in [0, 100].
Throttle: DM delivery is rate-limited to at most one DM per lead per 5 minutes. Within the window, events are stored but not immediately DM'd. On COMPLETED or after the 5-minute window, all accumulated undelivered MILESTONEs for that lead are flushed as a single bulleted DM.

#### COMPLETED

```json
{
  "lead": "ppt-manager",
  "type": "COMPLETED",
  "ts": 1748895234.567,
  "payload": {
    "summary": "Converted 42-slide deck to PDF and exported cover image. Deck is at /home/ubuntu/projects/ppt-manager/output/deck.pdf.",
    "paths": [
      "/home/ubuntu/projects/ppt-manager/output/deck.pdf",
      "/tmp/cover.png"
    ],
    "urls": [
      "https://github.com/techfreakworm/ppt-manager/releases/tag/v1.0"
    ]
  }
}
```

Required: `summary` (non-empty string, max 2000 chars).
Optional: `paths` (list of absolute path strings, each starting with `/`, max 10 items); `urls` (list of HTTPS strings, each starting with `http`, max 10 items). Both default to empty lists when omitted.
Validation: `summary` must be non-empty. Each path must start with `/`. Each URL must start with `http`.
If `paths` is non-empty, the HTTP listener calls `_tg_post_multipart` for each path after the text DM. If `urls` is non-empty, they are appended as links in the DM text.

#### NEEDS_INPUT

```json
{
  "lead": "social-publisher",
  "type": "NEEDS_INPUT",
  "ts": 1748893000.789,
  "payload": {
    "question": "Should I publish the LinkedIn post as a newsletter article or as a regular post?",
    "options": ["newsletter article", "regular post"],
    "timeout": 300
  }
}
```

Required: `question` (non-empty string, max 500 chars).
Optional: `options` (list of strings, max 5 items, each max 100 chars; omit → free-form answer); `timeout` (integer seconds > 0, max 3600; omit → no timeout).
Validation: `question` must be non-empty. `options` items must be non-empty strings. `timeout` if present must be a positive integer.
A corresponding row is written to `pending_inputs` table immediately (see DDL). The Telegram `message_id` of the DM sent to the user is stored in `pending_inputs.tg_msg_id` for correlation.

#### ERROR

```json
{
  "lead": "deploy-agent",
  "type": "ERROR",
  "ts": 1748892500.234,
  "payload": {
    "error": "git push rejected: permission denied (publickey)",
    "context": "Attempting to push compiled frontend assets to origin/main",
    "traceback": "subprocess.CalledProcessError: Command '['git', 'push', 'origin', 'main']' returned non-zero exit status 128\n  at push_to_remote() line 42\n  ...",
    "recoverable": false
  }
}
```

Required: `error` (non-empty string, max 500 chars), `context` (non-empty string, max 500 chars).
Optional: `traceback` (string, max 5000 chars; truncated to 5000 chars with `...` suffix if longer; omit → no traceback in DM); `recoverable` (bool; default `true` when omitted — absence is not the same as `false`).
Validation: `error` and `context` must be non-empty. `recoverable` if present must be a JSON boolean.

### SQLite `lead_events` table

```sql
CREATE TABLE IF NOT EXISTS lead_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    lead             TEXT    NOT NULL,
    type             TEXT    NOT NULL
                             CHECK (type IN ('STARTED','MILESTONE','COMPLETED','NEEDS_INPUT','ERROR')),
    ts               REAL    NOT NULL,
    payload_json     TEXT    NOT NULL,
    created_at       REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec')),
    delivered_at     REAL,
    delivery_error   TEXT,
    hook_injected_at REAL
);

CREATE INDEX IF NOT EXISTS idx_le_lead
    ON lead_events (lead);
CREATE INDEX IF NOT EXISTS idx_le_type
    ON lead_events (type);
CREATE INDEX IF NOT EXISTS idx_le_undelivered
    ON lead_events (delivered_at)
    WHERE delivered_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_le_uninjected
    ON lead_events (hook_injected_at)
    WHERE hook_injected_at IS NULL;
```

`id`: autoincrement rowid; canonical ordering key.
`ts`: lead's clock at emit time; stored for display only; not used for ordering.
`delivered_at`: NULL = not yet DM'd to user; set to `unixepoch('now','subsec')` on successful DM.
`delivery_error`: last error string from a failed DM attempt; cleared on success.
`hook_injected_at`: NULL = not yet injected via UserPromptSubmit hook; set when the hook reads the row.

The `pending_inputs` table for NEEDS_INPUT correlation:

```sql
CREATE TABLE IF NOT EXISTS pending_inputs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     INTEGER NOT NULL REFERENCES lead_events(id),
    lead         TEXT    NOT NULL,
    question     TEXT    NOT NULL,
    options_json TEXT,
    timeout_secs INTEGER,
    tg_msg_id    INTEGER,
    status       TEXT    NOT NULL DEFAULT 'open'
                         CHECK (status IN ('open','resolved','timed_out')),
    created_at   REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec')),
    resolved_at  REAL,
    answer       TEXT
);

CREATE INDEX IF NOT EXISTS idx_pi_open
    ON pending_inputs (status)
    WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_pi_lead
    ON pending_inputs (lead);
```

---

## Lead-side surface

### Three candidates

**(1) CLI helper** — a `soma-notify` binary or shell script placed in the lead's PATH:
```bash
soma-notify COMPLETED --payload '{"summary":"done","paths":[]}'
```
Pros: no library imports; natural for shell-based leads; binary path is fully controllable by the operator.
Cons: leads in claude-soma are Claude Code processes, not bash scripts — they primarily call MCP tools, not shell commands. A shell binary is less discoverable to a Claude Code lead than an MCP tool. The binary requires PATH management (must be in `LEAD_PATH`). Harder to validate the payload schema server-side without running a full subprocess. Leads must know the binary name and its argument conventions.

**(2) Tiny MCP tool** — `mcp__hermes_notify__notify_orchestrator(type, payload)` added to `lead-mcp.json`:
```python
mcp__hermes_notify__notify_orchestrator(
    type="COMPLETED",
    payload={"summary": "done", "paths": ["/home/ubuntu/projects/lead/out.pdf"]}
)
```
Pros: leads call it identically to every other MCP tool — zero new knowledge required. The tool reads `HERMES_LEAD_NAME` from `os.environ` silently; leads don't need to know their own name or anything about HTTP or Telegram. Schema validation happens server-side before any SQLite write. The MCP protocol handles transport; the lead has no awareness of whether it's hitting HTTP or a local socket. Perfectly minimal blast radius (one tool, no spawn/kill/read access).
Cons: takes effect only on the next lead spawn; already-running leads do not receive the tool until restarted. Requires one new stanza in `lead-mcp.json`.

**(3) Pane magic-line** — lead prints `NOTIFY: {json}` to stdout; an external watcher reads tmux panes:
Pros: zero lead-side change; nothing to install.
Cons: requires an independent watcher process that continuously polls tmux panes — another process to manage. Pane buffer wraps: a verbose lead can overwrite the magic-line before the watcher sees it. Not discoverable to Claude Code leads (they must know the prefix convention). Text-scraping for structured data is fragile. Polling latency proportional to poll interval. Entirely inconsistent with how leads do everything else. Eliminated.

### Selected surface

**MCP tool in a new `hermes-notify` MCP server.** The reasoning:

The lead must not need to import any library or know about Telegram (per the design goal). An MCP tool is the canonical way Claude Code sessions interact with external systems — it is already in the lead's normal operation vocabulary. The lead's system prompt (or brief) instructs it to call `notify_orchestrator` at key lifecycle milestones; the tool handles the rest.

A new server (rather than extending `hermes_api`) is the right scoping: hermes_api is excluded from leads by design because it has transcript read access, session inspection, and activity-log read access. Returning it to `lead-mcp.json` would reopen all those capabilities to leads. The `hermes-notify` server has exactly one tool; it cannot read any orchestrator state. This is the minimal blast-radius principle applied consistently.

**Tool signature** (in `src/claude_soma/mcp_servers/hermes_notify/server.py`):

```python
@mcp.tool()
def notify_orchestrator(
    type: str,
    payload: dict,
) -> dict:
    """Signal the orchestrator about a lead lifecycle event.

    type must be one of: STARTED, MILESTONE, COMPLETED, NEEDS_INPUT, ERROR.
    payload must match the schema for the given type (see PLAN-FI-NOTIFY.md).
    The lead name is read automatically from the HERMES_LEAD_NAME environment variable.

    Returns:
        {"stored_id": int, "delivered": bool}
        stored_id: the SQLite row id for the event.
        delivered: true if the DM to the user was sent immediately; false if
                   only stored (will be delivered on next drain cycle).
    """
```

A second tool for the bot to call when resolving a NEEDS_INPUT answer:

```python
@mcp.tool()
def resolve_pending_input(event_id: int, answer: str) -> dict:
    """Mark a NEEDS_INPUT event as resolved with the given answer.

    Called by the bot after it routes the user's reply to the relevant lead.
    Updates pending_inputs.status = 'resolved' so the UserPromptSubmit hook
    stops injecting the open question into future turns.

    Returns: {"resolved": true}
    """
```

**Server host:** `hermes-notify` MCP server (new), NOT an extension of `hermes_api`. The two-tool server lives at `src/claude_soma/mcp_servers/hermes_notify/server.py`.

**Lead name discovery:** `HERMES_LEAD_NAME` env var injected at spawn time by `spawner._wrap_in_transient_unit()` (the `--setenv` injection point at line 154 of `spawner.py`). The tool reads it via `os.environ.get("HERMES_LEAD_NAME")`. If absent (CI/dev box): the tool returns a validation error and the event is not stored.

**New stanza in `config/claude/lead-mcp.json`:**

```json
"hermes-notify": {
  "type": "stdio",
  "command": "/opt/claude-soma/.venv/bin/python",
  "args": [
    "-m",
    "claude_soma.mcp_servers.hermes_notify.server"
  ],
  "env": {
    "HERMES_NOTIFY_PORT": "9100"
  },
  "alwaysLoad": true
}
```

`HERMES_NOTIFY_PORT` in the stanza env block is the compile-time fallback. The authoritative value at runtime is the one injected by the spawner into the unit env (so all leads and the hermes_api listener always share the same port from a single source). The stanza env is read only if the spawner has not injected the var.

---

## Orchestrator-side surface

### The constraint

channel-claude is a long-running session. When a lead emits COMPLETED, two things must happen:
1. The user's phone buzzes with a DM immediately — not on the next "Status?" ping.
2. When the user follows up ("what did lead X complete?"), the bot must answer correctly without doing a fresh `capture-pane` round-trip.

The bot also needs to handle NEEDS_INPUT events: when a lead is blocked on a question, the user's reply must be routed back to the correct lead, not lost or answered in the wrong context.

### Candidate paths

**(i) Notify-server DMs directly, bot learns only when queried.**
The HTTP listener in hermes_api calls `_tg_post_json` on receipt of urgent events; the bot has no automatic awareness of events and must call a query MCP tool when the user asks about a lead.

Pros: lowest latency; bot is never disrupted; simplest.
Cons: bot has no in-context awareness of events. NEEDS_INPUT correlation breaks: the user's reply comes in as a new Telegram message; the bot has no context that there is an outstanding question from any lead. Under load (10 events/minute), the bot's context is always stale unless it proactively calls a query tool — which it won't do on every turn. Cold start: no issue (DMs fire from the listener; bot drains and re-fires on startup). Long Agent dispatch: DMs still fire (listener is in a background thread independent of MCP tool processing).

**(ii) Inline injection via tmux send-keys into the bot's own pane.**
When the HTTP listener receives an event, it injects it into the bot's tmux pane via `send-keys`.

Pros: bot sees the event in-context immediately.
Cons: disrupts the bot mid-thought, mid-tool-call, or mid-Agent-dispatch. The `--channels` input handling is not designed to receive injected keystrokes. Risk of corrupting in-flight prompts. Not a documented or supported pattern. Eliminated.

**(iii) Bot calls `get_recent_events(lead, since)` MCP tool on demand — lazy/pull.**
A query tool is added to hermes_api; the bot calls it when the user asks about a lead.

Pros: accurate data when actually needed; no disruption.
Cons: requires the bot to remember to call the tool — needs instructions in system prompt, and instructions can be missed. Doesn't solve the immediate-DM requirement. Bot might not call the tool when it should. Under load: bot context drifts further with each turn it doesn't query. Cold start: fine. Long Agent dispatch: the bot is busy and won't call the tool mid-dispatch.

**(iv) UserPromptSubmit hook injects recent-events context as `additionalContext` on every turn.**
A new hook script reads unread `lead_events` from SQLite and outputs `{"additionalContext": "..."}` to stdout, injecting a "recent lead events" block before every bot turn.

Pros: bot gets fresh context on every turn; no tool call required; deterministic. NEEDS_INPUT open questions appear in context automatically. Events arrive before the bot starts reasoning, not after.
Cons: fires on every user message including trivial ones ("ok", "thanks") — adds ~10 ms for a SQLite read. Many events in a burst → large additionalContext block; must cap at N events + size limit. Events marked `hook_injected_at` after injection to prevent re-injection.

**(v) Hybrid (i) + (iii): direct DM + query tool.**
HTTP listener DMs urgent events immediately. Bot calls `get_recent_events()` when answering history questions.

Pros: good latency; bot can be accurate when queried.
Cons: NEEDS_INPUT correlation still broken (no automatic context). Bot must remember to call the tool; may duplicate information (DM says "lead X completed" AND bot calls the tool and reports it again). Under load: bot context still drifts between turns.

**(vi) Hybrid (i) + (iv): direct DM + UserPromptSubmit hook injection.**
HTTP listener DMs user immediately for urgent events. On every user message, `notify_inject.sh` injects recent unread events as `additionalContext`. Events marked `hook_injected_at` after injection to prevent re-injection.

Pros: immediate DMs (user gets notified without asking); bot has up-to-date lead state on every turn; NEEDS_INPUT correlation works automatically (the pending question is in context every turn until resolved); no duplicate capture-pane needed; no tool call required from the bot. Under load (10 events/minute): the hook caps injection at 20 events + 2000 chars; surplus events accumulate in SQLite and inject on the next turn. Cold start: drain fires first; hook processes remaining rows. Long Agent dispatch: DMs still fire (listener is independent); on the turn after the agent completes, the hook injects any accumulated events.
Cons: hook fires on every user message; must stay fast (< 50 ms, which a simple indexed SQLite query achieves). Large pending_inputs list (multiple concurrent NEEDS_INPUT from different leads) could confuse the bot — mitigated by FIFO injection (show oldest unresolved question first, one at a time).

### Selected path

**Hybrid (i) + (iv): direct DM via hermes_api HTTP listener + UserPromptSubmit hook injection.**

Reasoning: this is the only candidate that solves all three sub-problems simultaneously — immediate DM delivery, bot-side context awareness without extra tool calls, and NEEDS_INPUT correlation. The hook is the architectural key: it bridges the gap between the push delivery (DM fires, user reads it on their phone) and the pull retrieval (bot needs to know about it on the next Telegram message). Without the hook, the bot must either be told to call a query tool (unreliable) or receive injected keystrokes (unsafe). The hook pattern already exists in this repo (session_start_context.sh and orchestrator_gate.sh); a new unconditional UserPromptSubmit hook is consistent with that pattern.

### Concrete details for the selected path

**Files touched:**

1. `src/claude_soma/mcp_servers/hermes_api/server.py` — add HTTP listener background thread (bound to `127.0.0.1:HERMES_NOTIFY_PORT`), POST `/notify` handler (validate → write lead_events → throttle check → DM), POST `/resolve` handler (update pending_inputs), startup drain, `get_recent_lead_events` MCP tool.

2. `scripts/notify_inject.sh` (new) — reads `lead_events WHERE hook_injected_at IS NULL ORDER BY id DESC LIMIT 20` and `pending_inputs WHERE status='open' ORDER BY id ASC LIMIT 1` from registry.sqlite; formats compact text; outputs `{"additionalContext": "..."}` to stdout; updates `hook_injected_at` for fetched rows. If no unread events and no open NEEDS_INPUT: outputs `{}` and exits 0. Script must complete in < 50 ms under normal load (single indexed SQLite query).

3. `hooks/hooks.json` — add unconditional UserPromptSubmit hook entry for `notify_inject.sh`. The existing conditional hook (voice_intake.sh, gated on `audio_path`) is separate and stays.

4. `system_prompts/responsive_bot.md` — new section "Lead lifecycle events (FI-NOTIFY)" explaining: events may appear in `additionalContext`; if an OPEN NEEDS_INPUT entry is present, interpret the user's current message as the answer, call `mcp__hermes_notify__resolve_pending_input(event_id, answer)`, then relay the answer to the lead via tmux send-keys (same mechanism as normal lead messaging); for COMPLETED events in context, no extra action needed (DM already sent).

5. `src/claude_soma/mcp_servers/hermes_api/server.py` — expose `get_recent_lead_events` as a new MCP tool (optional fallback for the bot to query history explicitly):

```python
@mcp.tool()
def get_recent_lead_events(lead: str | None = None, limit: int = 20) -> list[dict]:
    """Query recent lead lifecycle events. If lead is None, returns all leads."""
```

**Config changes:**

- `config/claude/lead-mcp.json`: add `hermes-notify` stanza (see Lead-side surface).
- `hooks/hooks.json`: add unconditional UserPromptSubmit hook (see below).
- `secrets.env` documentation: add `HERMES_NOTIFY_PORT` (default 9100) and `HERMES_NOTIFY_CHAT_ID` (the Telegram chat_id to DM on events — see Open Questions).

**Restart requirements:**

Channel restart is required. It is the single restart that covers all of:
- New `hermes-notify` stanza in `lead-mcp.json` registers with claude code
- New UserPromptSubmit hook entry in `hooks.json` activates
- New HTTP listener thread in hermes_api starts and binds port 9100
- Drain logic runs at hermes_api startup (delivers any events accumulated before the feature was deployed)
- New `get_recent_lead_events` MCP tool becomes available to the bot
- Updated `responsive_bot.md` system prompt takes effect

Frontend restart: not required. Dashboard-API restart: not required (hermes_api is a channel-side process, not the FastAPI dashboard).

Existing running leads do NOT receive the `hermes-notify` MCP tool until they are killed and respawned. This is expected and acceptable: the tool takes effect on all new leads immediately after the channel restart.

**Throttling rules:**

- MILESTONE: per-lead in-memory throttle dict `{lead_name: last_milestone_dmed_ts}`. DM fires if current time − last_milestone_dmed_ts > 300 s (5 min) OR if this MILESTONE event follows a COMPLETED event for the same lead. On channel restart, the dict is reconstructed from `SELECT lead, MAX(delivered_at) FROM lead_events WHERE type='MILESTONE' GROUP BY lead`.
- All other types: no throttle. At most one meaningful event per lifecycle phase per lead.
- `additionalContext` budget cap: max 20 events, max 2000 chars total. If the formatted block would exceed 2000 chars, events are truncated oldest-first with a trailing "... and N more unread events." note.
- NEEDS_INPUT injection: FIFO, one open question per turn (oldest unresolved first). If multiple open questions exist, the bot sees only the oldest; later questions appear after the current one is resolved.

---

## Routing decision logic

| Type | DM the user immediately? | Surface to the bot? | Throttle? |
|---|---|---|---|
| STARTED | Yes — one-liner: "Lead `<name>` started: `<description>`" (+ETA if present) | Inject into next UserPromptSubmit additionalContext | None |
| MILESTONE | No — accumulate; flush batch DM after 5 min per lead OR on COMPLETED | Inject latest undelivered MILESTONEs into next additionalContext | 5 min per lead for DM; no throttle for hook injection |
| COMPLETED | Yes — celebratory DM: "Lead `<name>` completed: `<summary>`"; attach `paths[]` via multipart; append `urls[]` as links | Inject into next additionalContext; clear MILESTONE accumulator for that lead | None |
| NEEDS_INPUT | Yes — question DM + options (if any) | Inject oldest open NEEDS_INPUT into every additionalContext until resolved | None |
| ERROR | Yes — severity-tagged DM: "[ERROR] Lead `<name>`: `<error>`\nContext: `<context>`"; append "Lead has stopped — manual intervention may be needed" suffix if `recoverable: false` | Inject into next additionalContext; keep visible until user sends a message (hook_injected_at marks it after first injection) | None |

**NEEDS_INPUT correlation mechanism — concrete detail:**

1. hermes_api HTTP listener receives NEEDS_INPUT POST → validates → writes `lead_events` row → writes `pending_inputs` row (status='open', event_id, lead, question, options_json, timeout_secs, tg_msg_id=NULL) → sends DM to user → stores returned Telegram `message_id` in `pending_inputs.tg_msg_id`.

2. `notify_inject.sh` runs on every subsequent UserPromptSubmit → queries `pending_inputs WHERE status='open' ORDER BY id ASC LIMIT 1` → if a row exists, appends to additionalContext: `OPEN NEEDS_INPUT [event_id=<N>]: Lead <name> is waiting for your answer: "<question>"` (+ bulleted options if present).

3. Bot's system prompt instructs: "If your `additionalContext` contains an `OPEN NEEDS_INPUT [event_id=N]` entry, the user's current message is the answer to that question. Call `mcp__hermes_notify__resolve_pending_input(event_id=N, answer=<user's message text>)` first, then relay the answer to lead `<name>` via `tmux -L soma-lead-<name> send-keys -t soma-proj-<name> -l '<answer>'` followed by a separate `C-m`."

4. `resolve_pending_input` tool → updates `pending_inputs SET status='resolved', resolved_at=now, answer=answer WHERE id=event_id` → returns `{"resolved": true}`. After this the hook no longer injects this entry.

5. If the user's reply was NOT to a pending NEEDS_INPUT (bot judges the message is about something else), the bot leaves the `pending_inputs` row open. It re-appears in the next turn's additionalContext. This is intentional: the question stays visible until explicitly resolved.

**Reply-to correlation fallback:** if the user uses Telegram's "Reply" gesture on the NEEDS_INPUT DM (produces `reply_to_message.message_id` in the incoming update), the bot should still call `resolve_pending_input` — the additionalContext route is the primary mechanism; Telegram reply-to is a convenience affordance. The `pending_inputs.tg_msg_id` column enables a secondary lookup if needed (`SELECT event_id FROM pending_inputs WHERE tg_msg_id = ?`), but this is not required for the v1 flow.

---

## Failure modes + graceful degradation

### Notify server (HTTP listener in hermes_api) is down when a lead POSTs

The `hermes-notify` MCP server writes to `lead_events` SQLite first, then POSTs. If the POST fails (connection refused — listener not yet up, or channel restarting), the MCP tool returns `{"stored_id": N, "delivered": false}` — success from the lead's perspective. The event is durably in SQLite. On the next channel startup, the drain delivers it.

The lead is never blocked. The brief should state: "call `notify_orchestrator` and continue immediately regardless of the `delivered` flag."

### SQLite locked

`registry.sqlite` is already used with WAL mode (concurrent reads + one writer). Transient write contention: the hermes-notify MCP tool retries with backoff: 3 attempts at 100 ms, 200 ms, 400 ms (total max 700 ms). If all retries fail, the tool returns an MCP error; the event is not stored. The lead may retry. This failure mode is expected to be extremely rare in practice (single-box, low concurrent write rate).

### Telegram API rate-limited (HTTP 429)

Telegram's Bot API rate limit for a single chat is 30 messages/second and 20 messages/minute. This should never be reached in normal operation (a user with 6 concurrent leads completing work simultaneously would generate at most 6 COMPLETED events — well within limits). If rate-limited: the HTTP listener honors the `Retry-After` header if present. The DM attempt is re-queued in-memory for retry. The `lead_events.delivery_error` column records the last error. If the retry also fails, `delivered_at` stays NULL; the drain on the next restart will retry.

### Bot's UserPromptSubmit hook crashes

Consistent with existing hook failure behavior: the hook fails open. `notify_inject.sh` must always exit 0 even on SQLite errors. In the error case it outputs `{}` (empty JSON object) to stdout — no additionalContext injected but the bot's turn proceeds normally. Log failures to `/var/log/claude-soma/notify-inject.log`.

### Lead emits malformed JSON

The `hermes-notify` MCP tool validates the payload before any SQLite write. If `type` is not in the allowed set, required fields are missing, values exceed max length, or types are wrong (e.g. `percent` is a string): the tool returns an MCP error. The lead sees it as a tool call error in its conversation. The event is NOT written to SQLite. Leads can retry with a corrected payload.

### Lead impersonates another lead's name

v1: not prevented. `HERMES_LEAD_NAME` is in each lead's unit env, and a lead process can read its own env. A lead could theoretically pass a different name — but all leads on this box are single-user trusted processes spawned by the orchestrator. The realistic risk is a buggy lead accidentally using the wrong name; the worst consequence is a misrouted DM. Not a security concern for single-user, single-box deployment.

### Auth

**v1:** trust the env-derived name. The `hermes-notify` MCP tool reads `HERMES_LEAD_NAME` from `os.environ` and passes it as the `lead` field in the SQLite write and HTTP POST. The HTTP listener on `127.0.0.1:9100` trusts the name in the POST body. Binding to loopback means no external attacker can reach the endpoint without already having local process access — at which point the attacker has access to the entire box anyway.

**v1.5 hardening:** per-lead random `HERMES_LEAD_NOTIFY_TOKEN`:
- At spawn time, `spawner._wrap_in_transient_unit()` generates `uuid.uuid4().hex` and adds `--setenv=HERMES_LEAD_NOTIFY_TOKEN=<token>`.
- The token is also stored in the `projects` table of `registry.sqlite` (new `notify_token` column).
- The `hermes-notify` MCP tool reads `HERMES_LEAD_NOTIFY_TOKEN` from env and passes it in the POST body.
- The HTTP listener looks up the token in the registry: `SELECT name FROM projects WHERE notify_token = ?`. If not found, the POST is rejected with HTTP 403.
- Impersonation now requires reading another lead's systemd unit env — blocked by cgroup isolation and process namespace separation.
- v1.5 is explicitly deferred to round N+2 (or later); v1 ships in round N+1.

---

## Implementation cost estimate

### New files

| File | Estimated LOC |
|---|---|
| `src/claude_soma/mcp_servers/hermes_notify/__init__.py` | 5 |
| `src/claude_soma/mcp_servers/hermes_notify/server.py` | 160 |
| `scripts/notify_inject.sh` | 90 |
| `tests/mcp_servers/test_hermes_notify.py` | 130 |

**New files subtotal: ~385 LOC**

### Modified files

| File | Estimated LOC delta |
|---|---|
| `src/claude_soma/mcp_servers/hermes_api/server.py` | +130 |
| `src/claude_soma/mcp_servers/project_orchestrator/spawner.py` | +15 |
| `config/claude/lead-mcp.json` | +14 |
| `hooks/hooks.json` | +6 |
| `system_prompts/responsive_bot.md` | +40 |

**Modified files subtotal: +205 LOC**

**Grand total: ~590 LOC**

### New env vars

| Var | Default | Where set |
|---|---|---|
| `HERMES_NOTIFY_PORT` | `9100` | `secrets.env`, also in `lead-mcp.json` env block as fallback |
| `HERMES_NOTIFY_CHAT_ID` | (empty — must be set) | `secrets.env` |
| `HERMES_LEAD_NAME` | (injected per-lead) | spawner `--setenv` in `_wrap_in_transient_unit` |

### New config knobs

- `HERMES_NOTIFY_PORT`: the TCP port for the HTTP listener; default 9100.
- `HERMES_NOTIFY_CHAT_ID`: the Telegram chat_id to DM on events. Must be set in `secrets.env` for DM delivery to work; listener logs a warning on startup if absent.
- `HERMES_NOTIFY_MILESTONE_THROTTLE_SECS`: throttle period for MILESTONE DMs per lead; default 300 (5 minutes). Operator-overridable.
- `HERMES_NOTIFY_HOOK_MAX_EVENTS`: max events injected per UserPromptSubmit hook call; default 20.
- `HERMES_NOTIFY_HOOK_MAX_CHARS`: max chars for the additionalContext block; default 2000.

### Test files

| File | Estimated test count |
|---|---|
| `tests/mcp_servers/test_hermes_notify.py` | 12 |
| (additions to existing test files for hermes_api HTTP listener and notify_inject.sh) | 13 |

**Total estimated test count: ~25**

### Estimates

- **Total estimated LOC:** ~590
- **Total estimated implementation time:** 1.5–2 days (sonnet+max+seq-thinking subagent)
- **Risk:** Medium. The HTTP listener background thread in hermes_api is the most complex part (threading + asyncio interaction; the existing Unix socket thread is a model). The UserPromptSubmit hook adds ~10 ms per bot turn (indexed SQLite read); must be validated under load. Everything else is straightforward.

### Restart requirements summary

| Service | Restart required? | Reason |
|---|---|---|
| `claude-soma-channel.service` | YES | New MCP tool, new hook, new HTTP listener thread, updated system prompt |
| `claude-soma-api.service` | No | Not involved |
| `claude-soma-frontend.service` | No | Not involved |
| Existing running leads | Not required but no benefit | Receive the tool only after kill + respawn |

---

## Testing strategy

### Unit tests (function-level)

- `hermes-notify` server: `notify_orchestrator` with each valid type → stored_id returned; invalid type → MCP error; missing `HERMES_LEAD_NAME` → MCP error; required field absent → MCP error; oversize payload → field truncated or rejected per type-specific rule; HTTP POST mocked via `unittest.mock`.
- `hermes-notify` server: `resolve_pending_input` → status updated in DB; nonexistent event_id → MCP error.
- `hermes_api` HTTP listener: POST `/notify` with valid envelope → row written + DM called (mock `_tg_post_json`); invalid JSON body → 400; unknown `type` → 400; MILESTONE below throttle window → DM suppressed; MILESTONE above window → DM fired.
- `hermes_api` startup drain: rows with `delivered_at IS NULL, type IN (COMPLETED, NEEDS_INPUT, ERROR)` → DM retried; already-delivered rows → skipped.
- `notify_inject.sh`: no rows → output `{}`; 1 row → output includes `additionalContext`; 20 rows → all injected; 21 rows → 20 injected, 1 deferred; rows marked `hook_injected_at` on success; crash → exits 0.

### Integration tests (lead emits → server stores → DM fires)

- Spin up hermes_api in-process with a test SQLite DB and a mock Telegram endpoint; call `notify_orchestrator` via the hermes-notify MCP server; assert the SQLite row exists, assert the mock Telegram endpoint received a POST with the correct text.
- NEEDS_INPUT flow: emit NEEDS_INPUT → DM sent → pending_inputs row written → `resolve_pending_input` called → status=resolved → hook no longer injects.

### End-to-end test (full happy path)

- Start a real lead (or simulate with a test script) that calls `notify_orchestrator(type="COMPLETED", payload={...})`.
- Assert: `lead_events` row exists with `delivered_at NOT NULL`; the Telegram Bot API received a `sendMessage` call (use a test bot token and a dedicated test chat).
- Assert: on the next simulated UserPromptSubmit hook run, the COMPLETED event appears in additionalContext if `hook_injected_at IS NULL` (before the hook runs) and does not appear after.

### Negative tests

- Malformed JSON body to HTTP listener → HTTP 400, no SQLite write.
- Payload with `percent: "seventy-five"` (wrong type for MILESTONE) → MCP error.
- `traceback` field longer than 5000 chars → truncated to 5000 chars + `...`.
- `HERMES_NOTIFY_CHAT_ID` absent from env → DM delivery skipped, event stored, `delivery_error` set.
- Telegram API returns 429 → retry attempted; event stored with `delivery_error` on second failure.
- `HERMES_LEAD_NAME` absent from lead env → `notify_orchestrator` returns MCP error immediately.

### Manual smoke test

After deploying, with one active lead:
1. Add a one-line call to the lead's brief or send it a message asking it to call `notify_orchestrator(type="STARTED", payload={"description":"smoke test"})`.
2. Observe the DM arrives on the user's phone within ~2 seconds.
3. Run `sqlite3 /opt/claude-soma/registry.sqlite "SELECT * FROM lead_events ORDER BY id DESC LIMIT 1;"` and confirm the row.
4. Send the bot a Telegram message; confirm `notify_inject.sh` injected the STARTED event into additionalContext (visible in the bot's transcript or activity log).

---

## Open questions for the user (block on these before implementation)

1. **`HERMES_NOTIFY_CHAT_ID` value:** The HTTP listener needs a preconfigured Telegram chat_id to send proactive DMs. Your chat_id appears as `935376085` in the bot's system prompt examples. Should `HERMES_NOTIFY_CHAT_ID` be set to that value in `secrets.env`, or do you prefer a different config approach (e.g. derive it from the channel's last-known Telegram `chat_id` stored somewhere)?

2. **NEEDS_INPUT timeout enforcement:** The `NEEDS_INPUT` payload includes an optional `timeout` field (seconds before the lead proceeds or aborts). What should the server do when the timeout expires? Options: (a) do nothing — leave `pending_inputs` open, lead times out on its own; (b) send an additional DM "Lead `<name>` timed out waiting for your answer and has proceeded with a default"; (c) explicitly close the `pending_inputs` row with `status='timed_out'`. Recommended: (b)+(c), but needs your confirmation before implementing the timeout-monitor logic.

3. **Multiple concurrent NEEDS_INPUT from different leads:** The plan injects one open question at a time (FIFO: oldest first). If you have 3 leads simultaneously waiting for input, the bot sees only lead-1's question until that's resolved, then lead-2's, etc. Is FIFO the right behavior, or do you want all open questions shown at once (more complex bot reasoning, higher context usage)?

4. **Lead brief template update:** Should `spawner.spawn_background_lead()` automatically append a standard "how to use `notify_orchestrator`" paragraph to every lead brief, or should the orchestrator (the bot, via the spawn prompt) be responsible for including that instruction? The former is simpler but adds ~10 lines to every brief; the latter is more flexible but relies on the bot always including it.

5. **v1.5 token auth scope:** Should the per-lead random token (`HERMES_LEAD_NOTIFY_TOKEN`) be in scope for round N+1 alongside the main feature, or explicitly deferred to round N+2? The main feature works without it (same-box trust); adding it in the same round avoids shipping a known hardening gap, but increases the round's scope from M to M+S.

---

## Out of scope

- **Bot-to-lead reverse notify:** leads cannot receive push signals from the bot (the bot already has `tmux send-keys` for synchronous messages; async push from bot to lead is a different problem).
- **Multi-tenant:** the design assumes a single Telegram user (one `HERMES_NOTIFY_CHAT_ID`). Supporting multiple users per bot instance is deferred.
- **Web admin events log view:** surfacing `lead_events` in the dashboard UI (a filterable table of past events, useful for post-mortems) is a separate dashboard feature, not in this plan.
- **Daily-digest rendering:** summarizing the day's events into a single narrative DM. The event data will exist in SQLite; the digest rendering is a future feature.
- **NEEDS_INPUT timeout enforcement beyond DM notification:** the plan covers DM + `timed_out` status. Automatically relaying a default answer to the lead when timeout expires is deferred to v2.
- **Showing more than one NEEDS_INPUT simultaneously:** v1 injects questions FIFO (one at a time). Multi-question concurrent display is deferred.
- **`soma-notify` CLI binary:** the CLI-helper candidate is rejected; no binary is created.
- **Pane magic-line watcher:** the magic-line candidate is rejected; no watcher process is created.
- **hermes_api uvicorn:** hermes_api is a FastMCP stdio server, not a uvicorn process. The notify HTTP listener is a background thread, not a separate HTTP service.
