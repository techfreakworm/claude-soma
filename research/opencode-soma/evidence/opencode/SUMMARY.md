# opencode 1.18.8 probe summary (probe-engineer)

All probes ran against the operator's existing binary `/Users/techfreakworm/.opencode/bin/opencode`
(version 1.18.8, never upgraded) in a throwaway sandbox (HOME + all XDG dirs + TMPDIR redirected
under `$TMPDIR/ocsoma-pe/<probe>`), model = local ollama only. A logging reverse proxy sat between
opencode and ollama so "which tools each agent was offered", "what the system prompt contained" and
"usage per request" are hard evidence, not inference.

- Host: Apple M5 Max, 128 GiB RAM, macOS 26.4.1. ollama 0.33.3.
- Models: `qwen3:32b` (Q4_K_M, native ctx 40960, thinking off via `reasoningEffort:"none"`) used
  everywhere tool use must be reliable; `lfm2.5-thinking` (1.2B, ctx 128000) only where model
  quality is irrelevant.
- Isolation: PASS. `isolation-check.log` shows nothing under `~/.config|.local/share|.cache|
  .local/state/opencode`, `~/.opencode`, `$TMPDIR/opencode` or the `~/.claude` top level changed
  between the before-snapshot and after all probes. opencode honored the XDG/HOME redirection fully
  (`opencode debug paths` resolves everything inside the sandbox).

## Per-probe verdicts (evidence path -> verdict)

| Probe | Verdict | Evidence |
|---|---|---|
| Sandbox + ollama + proxy | PASS | `P0-setup/sandbox-verify.log` |
| CLI surface (every subcommand --help) | PASS | `cli-help.log` |
| Hook + event catalog (from 1.18.8 type defs) | PASS | `P2-hooks/hook-catalog.log`, `fetch-types.log` |
| P1 run: session reuse (`-s`, `--continue`) | PASS | `P1-headless/run-session.log` |
| P1 crash resume: kill -9 `run` mid-gen | PASS | `P1-headless/crash-run.log` |
| P1 crash resume: kill -9 `serve` mid-gen | PASS | `P1-headless/crash-serve.log` |
| P1 busy session (queue / block / abort / attach) | PASS | `P1-headless/busy.log`, `run1/wedge-snapshot.log` |
| P2a inject context every prompt | PASS | `P2-hooks/inject.log` |
| P2b gate a tool call before it runs (+ subagent exempt) | PASS | `P2-hooks/gate.log` |
| P2c log every tool use (incl. MCP + subagent) | PASS | `P2-hooks/activity.log`, `events.log` |
| P3 stdio + streamable-HTTP MCP callable from `run` | PASS | `P3-mcp/mcp.log` |
| P3 per-agent MCP scoping (telegram denied to lead) | PASS | `P3-mcp/scoping-verdict.log` (supersedes the raw `scoping.log` FAIL) |
| P4 subagent via task tool + parent/child linkage | PASS | `P4-subagents/basic.log` |
| P4 nesting (blocked at depth 1, works at `subagent_depth:2`) | PASS | `P4-subagents/nesting.log` |
| P4 parallel task calls run concurrently | PASS | `P4-subagents/parallel.log` |
| P4 background subagents + `/experimental/.../background` | PASS | `P4-subagents/background.log` |
| P4 independent lead process spawns its own team | PASS | `P4-subagents/lead-team.log` |
| P5 tmux send-keys + capture-pane scrape | PARTIAL | `P5-tmux/tmux.log`, `pane-analysis.log` |
| P6 permission model (rules/env/flags) | PASS | `P6-permissions/model.log` |
| P6 unattended `ask`: `run` auto-rejects, `serve` holds | PASS/obs | `P6-permissions/unattended.log` |
| P6 plugin answers a permission (event + SDK reply) | PASS | `P6-permissions/plugin-answer.log` |
| P6 reject a pending permission over HTTP | PASS | `P6-permissions/reject-retest.log` |
| P6 danger demo: injection blocked by the gate | PARTIAL | `P6-permissions/danger.log` |
| P7 token accounting (stats/export/HTTP/db) + rebuild | PASS | `P7-usage/usage.log` |
| P8 serve: OpenAPI, sync+async, SSE, auth, attach, web | PASS | `P8-serve/serve.log`, `openapi.json` |
| P8b CORS + /global/upgrade listed | PASS | `P8-serve/cors.log` |
| P2 extras: gate parentID-lookup cost, permission.ask-never-fires, idle/token events, custom-tool ctx.agent | PASS | `P2-hooks/extras.log` |
| P4 background-subagents capability flag | PASS | `P4-subagents/capabilities.log` |
| P5 attach-to-serve send-keys delivery (5/5) | PARTIAL | `P5-tmux/attach.log` |
| P6 reject a pending permission over HTTP | PASS | `P6-permissions/reject-retest.log` |
| P9 compat: claude-soma skills/agent/command load under opencode | PASS | `P9-compat/compat.log` |
| Conditions + per-model turn timing | PASS | `conditions.log` |

Follow-up findings (second round):
- **Gate cost**: the `tool.execute.before` primary-vs-subagent decision needs one `GET /session/{id}`
  lookup (p50 0.73ms on loopback) per NEW session, then an in-process cache hit (0.08us); one lookup
  per session, not per tool call (`P2-hooks/extras.log`). A custom plugin *tool*'s ctx carries `agent`
  directly (tool.d.ts), unlike the `tool.execute.before` hook input.
- **permission.ask hook is dead**: declared in the 1.18.8 types, registered by the spy plugin, fired
  0 times while the bus emitted permission.asked/replied; answer permissions via the event + HTTP
  `POST /permission/{id}/reply` (reject proven in `P6-permissions/reject-retest.log`).
- **CORS**: any `https://*.opencode.ai` origin and `localhost` are reflected in
  Access-Control-Allow-Origin, applied before auth, even with a password set; a non-opencode origin
  is not (`P8-serve/cors.log`). `POST /global/upgrade` is listed in `openapi.json` (not called). A
  non-loopback bind must set `server.cors` and rely on the password.
- **Compat**: claude-soma `SKILL.md` files load from `.opencode/skill/` with their Claude
  `allowed-tools` frontmatter intact; a project-lead agent converts to opencode frontmatter and is
  accepted; a command is served. opencode auto-loads external skills only from `~/.claude/skills/`
  (HOME-level), never project `.claude/`; `.claude/agents` is never read (`P9-compat/compat.log`).
- **Background subagents** gated by `OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS`, reported by
  `GET /experimental/capabilities` (`P4-subagents/capabilities.log`).
- **Model contention (RISKS)**: one local GPU + ollama `NUM_PARALLEL=1` means a second model is not
  served while the first has a queue (`evidence/memory/ollama-two-models.log`): everything queues
  behind the agent model.

Verifier-rerun note: ports derive from `OCSOMA_PORT_BASE` (default 47100; port = base + offset, offset
= last 2 digits) and tmux sockets from `OCSOMA_TMUX_PREFIX` (default `ocsoma-pe-`); set both to rerun
in another range/namespace. `isolation.sh verify` reads the version from a sandboxed run.

The two raw-run artifacts that read FAIL/PARTIAL are superseded, not contradicted:
- `P3-mcp/scoping.log` FAIL counts case D (a client putting `tools:{telegram_send_message:true}`
  in the request body) as a scoping breach. It is not: the agent never reaches telegram on its own
  (cases B/C/E), only a trusted HTTP caller re-grants it. `scoping-verdict.log` scores it correctly.
- `P1-headless/run1/*` is the first, monolithic P1 run (one shared session, archived). It was split
  into four independent-session scripts; `run1/run-session.log` FAIL was a test artifact
  (`--continue` picked up a shared session), fixed and re-run green in `run-session.log`.

## What a rebuild can rely on (load-bearing facts)

1. **The HTTP API makes the tmux hack unnecessary.** `opencode serve` exposes a 188-operation
   OpenAPI (`/doc`), an SSE event stream (`/event`), basic auth via `OPENCODE_SERVER_PASSWORD`
   (401 without creds, including on `/doc`), sync (`POST /session/{id}/message`) and fire-and-ack
   (`POST /session/{id}/prompt_async` -> 204) prompting, `/session/{id}/abort`, and
   `/permission/{id}/reply`. `opencode attach` and `opencode run --attach <url>` drive the same
   server/session from another process or a tmux pane.

2. **One writer per session.** A session is safe only when a single long-lived `opencode serve`
   owns its data dir and every client goes through it. Starting a second independent
   `opencode run -s <id>` on a session that a `serve` is driving produced duplicate/garbled
   assistant turns and wedged the session `busy` with no LLM traffic until `POST /abort`
   (reproduced twice; `busy.log`, `run1/wedge-snapshot.log`). Never mix the v1 prompt endpoints
   with the v2 `POST /api/session/{id}/prompt` on one session — v2 starts a separate agent loop
   and writes a separate message store.

3. **Responsiveness.** On one serve a busy session takes `prompt_async` instantly and queues it;
   sync `/message` blocks until its turn; `run --attach` queues; `abort` returns to idle in ~0.15s.
   So a Telegram front end can always ack immediately and never blocks.

4. **Crash safety.** kill -9 of either `run` or `serve` mid-generation leaves history intact on
   disk (SQLite at `opencode db path`); the partial assistant message stays incomplete with empty
   text; a restart comes back idle and continues the same session id.

5. **Plugins (v1 `Hooks`, 20 keys).** `tool.execute.before` blocks a tool call by throwing (the
   model sees the thrown message as the tool result); `chat.message` can inject a synthetic text
   part on every prompt (the notify-inject analog) and `experimental.chat.system.transform` appends
   to the system prompt; `tool.execute.before/after` log every tool call (built-in, MCP, task, and
   a subagent's own calls) with session/parent/agent/duration. Plugins load from
   `<project>/.opencode/plugin/*.js` and `<config>/opencode/plugin/*.js`; they run in file order and
   a throwing plugin stops later plugins' hooks for that call.

6. **Agent/MCP scoping is defense against the model, not the caller.** A per-agent `permission`
   (`telegram_*: deny`) or legacy `tools: {telegram_*: false}` keeps a tool out of the model's tool
   list entirely (proved via the proxy). A trusted HTTP caller can still re-grant a tool per message
   via the request `tools` map, so the `OPENCODE_SERVER_PASSWORD` is the real boundary against
   callers.

7. **Permissions.** Config `permission` is `name -> allow|ask|deny` or `name -> {glob: action}`,
   global and per agent; `OPENCODE_PERMISSION` env JSON merges over it; `--auto` (and hidden
   `--yolo` / `--dangerously-skip-permissions`) approve every non-denied ask. Unattended, `run`
   AUTO-REJECTS a pending ask (fail-closed, does not hang); `serve` holds the request pending and
   the orchestrator answers it over HTTP (`once`/`always`/`reject`) or a plugin answers the
   `permission.asked` bus event via the SDK. The declared `permission.ask` hook never fires in
   1.18.8 (use the event path).

8. **Usage.** Per-message and per-session input/output/cache tokens are recorded for ollama models
   (HTTP, `export`, `stats`, and the SQLite `message`/`session` tables) and match ollama's own
   usage blocks; `P7-usage/aggregate.py` rebuilds a claude-soma-style daily_usage table from the DB.

## Surprises / caveats (things a design must account for)

- **`permission.ask` hook is dead in 1.18.8.** It is in the published types but never fired across
  a full session; programmatic permission answering must use the `permission.asked` event + an SDK
  reply, or the HTTP `/permission/{id}/reply`.
- **`tool.execute.before` input is minimal: `{tool, sessionID, callID}`** — no `agent`, no
  `parentID`. To tell a primary from a subagent (the claude-soma `agent_id` exemption) the hook must
  look the session up itself (`client.session.get` -> `parentID`). Demonstrated working.
- **Two event buses / two message stores.** v1 (`/event`, `/session/{id}/message`) and v2
  (`/api/event`, `/api/session/{id}/message`, 89 event types incl. token-level `session.next.*`)
  coexist. Pick one lane per session and stay in it.
- **Reasoning tokens are not separately accounted for ollama.** lfm2.5-thinking emitted 3264 chars
  of reasoning but opencode logged `tokens.reasoning: 0`; ollama folds reasoning into
  `completion_tokens` and reports no separate count. Input/output/cache are accurate.
- **A new session costs one extra `small_model` LLM call** (title generation) on top of the turn.
- **Baseline per-turn cost on qwen3:32b:** ~6.8K prompt tokens for a bare turn (9.7K-char system
  prompt + 10 built-in tools ~21K chars of JSON schema). Turn timing (across 184 qwen3 turns):
  wall p50 7.5s / p90 49.8s, ttfb p50 ~6s, decode ~8 tok/s, prefill ~106 tok/s. qwen3 cold-start
  first turn ~31s. These numbers are upper bounds: other teammates' local benchmarks shared the GPU
  during the run (`conditions.log`).
- **`opencode web`** serves an embedded SPA (one ~3 MB JS asset) from the same server behind the
  same basic auth, and shells out to `open`/`xdg-open` to launch a browser (shimmed in the probe so
  nothing opened). LAN exposure is opt-in: `--hostname 0.0.0.0`, `--mdns` (advertises
  `opencode.local`), `--cors`; default bind is 127.0.0.1.
- **P5 tmux scrape is viable but lossy** and strictly worse than the HTTP API: 6/6 messages
  (short, long, multi-line, quotes, `$`/backticks) were delivered to the server verbatim, but
  whole-line `capture-pane` scraping found only 5/6 replies because the TUI's right sidebar shares
  terminal rows (column-aware parsing finds 6/6), and long replies are truncated to the visible
  screen (alt-screen, no tmux scrollback). Use the API, not the TUI, for a rebuild.

## How to re-run
`research/opencode-soma/probes/opencode/run-all.sh [--isolation]` runs every probe sequentially
(one LLM consumer at a time; ~1h on qwen3:32b). Each probe writes its own evidence log; `*/analyze.sh`
re-derive verdicts from saved artifacts without new LLM turns.
