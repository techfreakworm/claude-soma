# PLAN-TUI-ONLY-INTERACTIONS.md

**Status:** DRAFT — awaiting user approval. NO implementation.

**Author:** PLAN-TUI-ONLY-INTERACTIONS. **Date:** 2026-06-03.
**Subject:** `AskUserQuestion` renders only in TUI and deadlocks the Telegram user.

## Headline summary

| Item | Verdict | Severity | Recommended fix |
|---|---|---|---|
| `AskUserQuestion` deadlocks the channel | Confirmed bug | P0 — silent freeze | System-prompt ban + PreToolUse hook deny (B+A) |
| Other TUI-only surfaces | Audited (Part 2) | Mostly mooted or unreachable | Per-surface matrix (Part 5) |
| Primary winner | **Candidate B + Candidate A (lite)** combined | — | Ship in one round |
| Stretch winner (v2) | **Candidate C** — `ask_user_via_telegram` MCP tool | — | Defer unless leads need a structured picker |

---

## Part 1 — Root cause investigation

### How `AskUserQuestion` actually renders

`AskUserQuestion` is a **Claude Code built-in tool**, not an MCP tool. The
harness emits it in the tool list at session start; when the model calls
it with `{question, options[]}`, the harness pauses the conversation,
draws a curses-style picker on the controlling TTY, reads the user's
keypress, and returns the chosen option as the tool result. No MCP server
is involved.

In our channel session this happens inside the detached tmux pane
(`new-session -d -s hermes`). No human at that keyboard. The picker blocks
on `read()` forever. The LLM turn stays stuck mid-tool-call; the channel
plugin's poller keeps receiving inbound DMs but the model can't process
them — it is waiting for a tool result that will never arrive.

### Why the channel plugin cannot intercept it

Source: `~/.claude/plugins/cache/claude-plugins-official/telegram/0.0.6/server.ts`.
The plugin declares two experimental capabilities (lines 387-395):

- `claude/channel` — inbound DM relay.
- `claude/channel/permission` — the "🔐 Permission" inline-keyboard flow
  (lines 418-443).

There is **no `claude/channel/question`** capability. The harness never
emits a corresponding notification. The plugin has no surface to grab.

### Why `--dangerously-skip-permissions` does not save us

The flag bypasses **permission** prompts (consent to perform an action).
`AskUserQuestion` is an **information** prompt — asking the user *what* to
do, not *whether*. Different code path, not affected.

### Why the user has not hit this constantly

Per `responsive_bot.md`, the orchestrator answers most things inline via
Telegram and dispatches heavy work to background agents. It rarely calls
`AskUserQuestion`. The bug fires when (a) a dispatched skill uses it (e.g.
`skills/make-a-video/SKILL.md`), or (b) the model spontaneously decides a
picker is the right disambiguation. The 2026-06-03 incident matches (b).

### Confirmation that hooks cannot synthesize a tool result

`PreToolUse` hooks return `{permissionDecision: allow|deny|ask,
permissionDecisionReason: string}` (see `scripts/orchestrator_gate.py` and
its test fixtures). The schema has **no slot for a synthetic tool
result**. A hook can deny (the LLM sees the reason and retries
differently) or allow (passes through). It cannot transparently route the
question through Telegram. This rules out the *fully-automated* form of
Candidate A.

---

## Part 2 — TUI-only-interaction surface audit

| Surface | Deadlocks remote user? | Current mitigation | Severity |
|---|---|---|---|
| `AskUserQuestion` | YES (confirmed) | None | P0 |
| `ExitPlanMode` | YES if plan mode is entered | Orchestrator does not use plan mode today; no explicit ban | P2 (latent) |
| Permission prompts (tool consent) | NO | `--dangerously-skip-permissions` is set in `channel-claude.sh`; if removed, `claude/channel/permission` flow takes over | Mooted |
| Slash-command interactive args (a skill that prompts via stdin) | YES in theory | `Skill` is denied by `orchestrator_gate.py` line 56 — skills cannot run inline | Mooted |
| MCP tool blocking-await for confirmation | NO | MCP tools are JSON-RPC; they do not read TTY. Anything that *could* prompt goes through the permission system → mooted | Mooted |
| Bash sandbox approval flow | NO | Same as permission prompts — mooted | Mooted |
| Auto-compaction "compact now?" prompt | LIKELY auto | Has not been observed to block in weeks of channel runtime. Verify in Part 7 open questions | P3 (verify) |
| OAuth token re-auth | YES if it ever fires | Token is refreshed externally; if it expired in-session, the harness would error rather than prompt — needs verification | P3 (verify) |
| `/clear`, `/compact` slash invocations | N/A | Operator-only; not callable by the LLM | Mooted |
| `TodoWrite` editor prompt | NO | The harness `TodoWrite` is non-interactive (writes a JSON file) | Mooted |

**Lead sessions:** every surface above applies to leads as well — leads run
the same Claude Code binary under `--dangerously-skip-permissions` with no
operator at their tmux pane. Leads can call `AskUserQuestion` from any skill
they happen to invoke. Audit-wise: lead = same surfaces, same severities.

---

## Part 3 — Approach evaluation

### Candidate A — PreToolUse hook intercept + Bot API relay

**Idea:** a hook intercepts the call, DMs the question + options, waits
for the user's reply, returns the chosen option as the tool result.

**Hard truth (Part 1):** the hook schema cannot synthesize a tool result.
The full automated form needs upstream harness surgery (a new
`hookSpecificOutput.toolResult` field). Not viable in our timeframe.

**Lite form (viable):** deny the call with `permissionDecisionReason=
"AskUserQuestion is TUI-only and deadlocks the Telegram user — emit a
plain Telegram message with numbered options instead."` The LLM sees the
denial and retries with `reply()`. A 2-line addition to
`scripts/orchestrator_gate.py` plus a 2-row addition to
`tests/test_orchestrator_gate.py`. Effort: S. Risk: minimal.

### Candidate B — System-prompt ban + Telegram-native Q&A

**Idea:** a binding paragraph in `system_prompts/responsive_bot.md`
forbids `AskUserQuestion` (and `ExitPlanMode`). The orchestrator sends a
numbered-options Telegram message and reads the user's free-form reply on
the next turn — the channel's established Q&A pattern.

**Why this works for the orchestrator:** the orchestrator is a Telegram
chat agent that already does free-form Q&A every turn. Telling it "ask via
Telegram, not via picker" asks the LLM to do less, not more.

**Why this works for leads:** leads already have FI-NOTIFY `NEEDS_INPUT`.
A lead calls `mcp__hermes_notify__notify_orchestrator(type="NEEDS_INPUT",
payload={question, options})`. The orchestrator DMs the user, the user
replies, the orchestrator resolves via `resolve_pending_input`, and the
answer is relayed to the lead via tmux. Proven, in production, with
SQLite spool and proactive-DM path. "Ban AskUserQuestion in leads" maps
1:1 to "use NEEDS_INPUT" — no new infrastructure.

Effort: S. Risk: low. UX: slightly less structured than a native picker
but matches the channel's conversational style.

### Candidate C — Custom MCP tool `ask_user_via_telegram`

**Idea:** add a new tool to `hermes_api` mimicking `AskUserQuestion`'s
shape: `(question, options[]) -> selected option`. Generates a
`request_id`, DMs numbered options, inserts a `pending_inputs` row,
blocks on a `threading.Event` until the user's answer is resolved, returns
it.

**OK for leads, not for the orchestrator:** the orchestrator must stay
responsive to the next inbound DM during the question. A blocking MCP
tool call freezes the channel. Leads should block (NEEDS_INPUT does so
today). Candidate C is really a lead-side ergonomic upgrade, not an
orchestrator fix. The orchestrator must use Candidate B regardless.

Effort: M (~150 LOC + tests + an `options` column in `pending_inputs`).
Risk: medium — must reuse `pending_inputs` cleanly and avoid duplicating
the proactive-DM path.

### Candidate D — Synthetic stdin / TUI injection

A daemon tails the tmux pane, detects the picker render, DMs the user,
injects the reply via `tmux send-keys`. Fragile: version-fragile glyph
parsing, races the harness's own keypress handler, re-introduces the
lead-pane-blocking model FI-NOTIFY was built to avoid. Recommend against.

### Candidate E (own design — rejected)

A `--disable-tool AskUserQuestion` flag would be ideal, but Claude Code
doesn't expose that surface. A patched-binary fork is excessive.

---

## Part 4 — Recommended approach + sequencing

### Primary winner — **Candidate B + Candidate A (lite)**

Ship them together in one round. Two micro-edits, two-line hook addition,
no new services, no DB schema changes, no restarts beyond a single channel
session bounce to pick up the system-prompt edit. Both restart pieces are
already routinely deployed during ordinary fix rounds.

**Why both, not just B:** the system prompt is the primary directive; the
hook is the deterministic backstop. The gate-hook pattern is established
(it already enforces `Skill`/`Edit`/`WebFetch`/etc.), and the cost of
adding one more tool name to the deny list is trivial. Defense in depth.

**Token cost:** B = 0 (the orchestrator already sends Telegram messages
every turn; one numbered-options reply is identical to existing patterns).
A (lite) = 0 at runtime (the hook is shell + JSON, no LLM call).

**Latency:** both approaches resolve at the speed of the user's typing —
identical to every other Q&A turn. The current bug's deadlock is replaced
with a normal Telegram round-trip.

**Failure modes:** B alone — a curious LLM that ignores the system prompt
falls back to `AskUserQuestion`. The hook catches that. A (lite) alone —
the LLM gets a denial and may or may not pick the right alternative
(Telegram numbered options). The system prompt teaches the right
alternative. Together: belt + suspenders.

### Stretch winner — **Candidate C** as v2

Only ship if the user wants the structured-picker UX for **leads** (the
orchestrator must use B regardless). Build atop the existing
`pending_inputs` table; new tool is a thin wrapper that adds an
`options[]` column to the DM rendering and validates the reply against the
allowed values. Estimated 1 day of subagent work. Defer until the user
asks for it.

---

## Part 5 — Per-surface fix plan

| Surface | Fix | Where |
|---|---|---|
| `AskUserQuestion` (orchestrator) | Ban via system prompt + PreToolUse hook deny | `system_prompts/responsive_bot.md`, `scripts/orchestrator_gate.py` |
| `AskUserQuestion` (dispatched Agent prompts) | Orchestrator's spawn-prompt template must include "do not use AskUserQuestion; ask the user via Telegram by returning the question for me to relay" | `system_prompts/responsive_bot.md` worked-example block |
| `AskUserQuestion` (leads) | Ban via lead-brief template; direct leads to `mcp__hermes_notify__notify_orchestrator(type="NEEDS_INPUT")` | Lead brief template (in `spawner.py` or wherever the brief is composed) — see open Q 4 |
| `ExitPlanMode` | Ban via system prompt; also add to hook deny list as a backstop | `responsive_bot.md`, `orchestrator_gate.py` |
| Permission prompts | No change — already mooted by `--dangerously-skip-permissions` | — |
| Slash-command interactive args | No change — `Skill` already in hook deny list | — |
| Auto-compaction prompt | **Verify** in open Q 2 that it is automatic in `--dangerously-skip-permissions` mode; if it ever prompts, escalate | — |
| OAuth re-auth | **Verify** in open Q 2 that token refresh is fully external | — |

---

## Part 6 — Effort estimates + file list + restart matrix

### File list — Candidate B + A (lite) (primary winner)

| File | Action | Est. LOC |
|---|---|---|
| `system_prompts/responsive_bot.md` | Add "TUI-only interactions banned" section under "Hard prohibitions"; include worked-example for the Telegram numbered-options pattern | +35 |
| `scripts/orchestrator_gate.py` | Add `AskUserQuestion` and `ExitPlanMode` to the tool-name deny list with a guiding reason | +4 |
| `tests/test_orchestrator_gate.py` | Add parametrize rows for the two new denies | +2 |
| `BUGS_PLAN.md` | Mark the bug fixed + reference this plan | +5 |
| (lead brief template, if leads are also patched in this round) | Add 6-line "use NEEDS_INPUT, never AskUserQuestion" paragraph | +6 |

**Total: ~50 LOC.**

### File list — Candidate C (stretch v2)

| File | Action | Est. LOC |
|---|---|---|
| `src/claude_soma/mcp_servers/hermes_api/server.py` | New `ask_user_via_telegram` tool + blocking-event resolution wiring | +80 |
| `src/claude_soma/mcp_servers/hermes_api/notify_store.py` | Add `options` column to `pending_inputs` (nullable) | +15 |
| Bot API DM formatter | Numbered-options render + validation of the reply against `options[]` | +30 |
| `tests/mcp_servers/test_hermes_api.py` | 6-8 new cases | +60 |
| Migration script for the new column | one-shot | +10 |

**Total: ~195 LOC.**

### Restart matrix

| Service | Primary winner restart? | Stretch v2 restart? | Reason |
|---|---|---|---|
| `claude-soma-channel.service` | YES (one bounce) | YES (also picks up new tool) | New system prompt + new hook tool deny |
| `claude-soma-api.service` | No | YES | New MCP tool + new DB column |
| Existing running leads | No | No | New tool reaches leads only on respawn |
| `claude-soma-frontend.service` | No | No | UI not affected |

The primary winner's channel restart is the standard `sudo systemctl
restart claude-soma-channel.service` — the same one the operator runs for
any system-prompt edit. ~10 s outage.

---

## Part 7 — Open questions for the user

1. **v1 scope** — ship the system-prompt-ban + hook backstop **only**
   (Candidate B + A-lite, ~50 LOC), or include the structured
   `ask_user_via_telegram` MCP tool (Candidate C, ~245 LOC total) in the
   same round? Recommendation: ship only the ban now; defer C until a lead
   actually needs the structured-picker UX.

2. **Audit verifications** — should we explicitly script-test the
   auto-compaction prompt and the OAuth re-auth flow under
   `--dangerously-skip-permissions` (Part 5 "verify" rows), or accept the
   live evidence (weeks of clean runtime) that they are auto?

3. **Lead-side scope** — patch the lead-brief template in the same round
   (one-line ban + pointer to `notify_orchestrator(NEEDS_INPUT)`), or
   leave leads untouched until a real incident? Today no live lead has
   tripped this; the orchestrator was the witness.

4. **Telegram-silence timeout** — if the user does not answer a
   numbered-options Telegram question within N minutes, should the
   orchestrator auto-cancel the question (apologize and end the
   conversation thread) or wait forever? Today the human eventually
   replies; we have no auto-cancel.

5. **Audit logging** — should the orchestrator log every TUI-fallback
   question (the ones that would have been `AskUserQuestion` and are now
   plain DMs) to `lead_events` for post-mortems? Cheap to add, opt-in.

---

## Part 8 — Risk + rollback

### Risk per approach

- **Candidate B (system-prompt ban):** Risk minimal. Worst case the LLM
  re-tries the ban (text-only deterrent); the hook catches it. Rollback
  = revert the system prompt edit (one commit).

- **Candidate A (lite) (hook deny):** Risk minimal. The hook is already
  the deterministic backstop for half a dozen other tools; adding two more
  rows follows the established pattern. Rollback = revert the hook edit.

- **Candidate C (custom MCP tool):** Risk medium. Adds a new column to a
  live SQLite table; needs a migration. A bug in the blocking-resolve loop
  could leak threads. Rollback = mark the tool deprecated + skip in tests,
  retain the DB column (NULL-tolerant). Operationally safe but more steps.

### Combined rollout sequence (primary winner)

1. Land the system-prompt edit + hook edit + test parametrize rows on a
   branch.
2. Run `pytest tests/test_orchestrator_gate.py -v` — expect the two new
   parametrize cases to pass.
3. Merge to main, push.
4. On the VPS: `git -C /opt/claude-soma pull --ff-only && sudo systemctl
   restart claude-soma-channel.service`.
5. Smoke-test: from Telegram, ask the bot something ambiguous that the
   LLM is likely to disambiguate with a picker (e.g. "draw something" with
   no provider). Confirm it asks the user via Telegram numbered options,
   not via TUI picker.

### Rollback

`git revert <commit> && git -C /opt/claude-soma pull --ff-only && sudo
systemctl restart claude-soma-channel.service`. Single-commit revert,
single restart, no data migration to undo.

---

**End of plan.**
