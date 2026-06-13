# FI-TG-REPLY-ENFORCEMENT — Structural guarantee that channel turns always reach Telegram

Date: 2026-06-12
Status: PLAN (approved-pending). Planning only — no code in this change.
Priority: HIGH (operator-facing silence bug, recurring multiple times per day).

## 0. Problem statement

The channel orchestrator (`claude --channels`, launched by `scripts/channel-claude.sh`,
system prompt `system_prompts/responsive_bot.md`) intermittently ends a turn with its
reply as PLAIN ASSISTANT TEXT instead of calling `mcp__hermes_api__send_tg_reply` or
`mcp__plugin_telegram_telegram__reply`. Plain assistant text is rendered only in the
local TUI pane — it never reaches Telegram. The operator sees silence.

Prompt-level rules already exist and demonstrably fail (observed again 2026-06-10,
session `9b6fde1c`: a propark status update went out as plain text; the operator had to
ask "Why aren't you replying?"). Both existing hard gates (`heard_gate.py`,
`relay_link_gate.py`) explicitly document the same coverage gap: *"if the bot replies
via the raw text channel without calling send_tg_reply, the hook cannot fire."*

This plan closes that gap with a **Stop hook** — the only harness event that fires at
the moment the failure becomes observable (turn ends, no reply was sent), and the only
point where we can deterministically enforce "a Telegram-triggered turn cannot end
silently."

## 1. Harness facts (verified against this repo + Claude Code hooks reference)

- The plugin already wires `SessionStart`, `UserPromptSubmit`, `PreToolUse`,
  `PostToolUse` in `hooks/hooks.json`. `Stop` is unused — free slot, no interactions to
  untangle.
- **Stop hook stdin JSON**: `{"session_id", "transcript_path", "cwd",
  "hook_event_name": "Stop", "stop_hook_active"}`. `transcript_path` is the session
  jsonl (for the bot: `~/.claude/projects/-opt-claude-soma/<uuid>.jsonl`).
  `stop_hook_active: true` means this stop is itself the result of a previous Stop-hook
  block — the built-in infinite-loop guard.
- **Stop hook outputs**: exit 0 + no output → allow stop. JSON on stdout
  `{"decision": "block", "reason": "<text>"}` → the turn does NOT end; `reason` is fed
  back to the model as a new instruction and the model continues. (Exit 2 + stderr is
  the equivalent legacy form; we will use the JSON form for clarity.)
- Stop fires only for the MAIN loop; subagents fire `SubagentStop`. We deliberately do
  NOT wire `SubagentStop` — dispatched agents are told to return text to the
  orchestrator or are explicitly instructed per-dispatch to send the reply themselves;
  the orchestrator's own next turn is what must be guarded.
- Transcript jsonl structure (verified by reading the live bot transcript):
  - Inbound Telegram messages are `{"type":"user"}` entries whose `message.content` is a
    **string** beginning with `<channel source="plugin:telegram:telegram"
    chat_id="..." message_id="..." ...>` — chat_id and message_id are parseable from the
    tag attributes.
  - Tool results are also `type:"user"` entries but `message.content` is a **list** of
    `tool_result` blocks — trivially distinguishable.
  - Assistant text is `type:"assistant"` with a `text` content block; tool calls are
    `type:"assistant"` with a `tool_use` block carrying `name` and `id`.
  - Non-message rows (`mode`, `queue-operation`, `attachment`, `system`,
    `last-prompt`) exist and must be skipped, not assumed absent.
  - Task-notifications / hook-injected context arrive as user entries WITHOUT the
    `<channel` prefix (system-reminder text or attachment rows).

## 2. Design overview

New hook script: `scripts/tg_reply_guard.py` (python3, stdlib only, fail-open like
every other hook in this repo), wired in `hooks/hooks.json` under `"Stop"`.

Per Stop event it answers three questions by parsing the tail of `transcript_path`:

1. **Was this turn Telegram-triggered?** Scan backwards for the most recent
   `type=="user"` entry whose `message.content` is a string (i.e. a real prompt, not a
   tool_result). If that string does not contain `<channel source="plugin:telegram` →
   exit 0 (task-notifications, cron pokes, local TUI prompts, hook-injected turns are
   all exempt). Extract `chat_id` and `message_id` from the tag for later use. The
   "turn window" is every entry after that user entry.
2. **Was a reply delivered this turn?** Within the turn window, look for a `tool_use`
   of `mcp__hermes_api__send_tg_reply` / `mcp__hermes-api__send_tg_reply` (both
   spellings appear in the wild) or `mcp__plugin_telegram_telegram__reply`, AND a
   matching `tool_result` (by `tool_use_id`) that is not `is_error: true` and whose text
   does not contain an error marker. A denied send (heard_gate / relay_link_gate deny)
   produces an error result and therefore does NOT count as delivered — correct,
   because nothing reached Telegram.
3. **Did the model produce user-facing text?** Within the turn window, collect
   `assistant` `text` blocks (ignore `thinking` blocks), strip whitespace, concatenate.
   Non-empty → user-facing text exists.

Decision table (Telegram-triggered turns only):

| Reply delivered | Assistant text | Action |
|---|---|---|
| yes | any | allow stop (normal case; trailing narration text is harmless) |
| no | yes (dropped-reply bug) | enforce (below) |
| no | no (silent tool-only turn) | enforce (below) — responsive_bot.md already mandates an immediate ack on every dispatch turn; silence is the same operator-facing bug |

### Enforcement: BLOCK-THEN-RELAY hybrid (recommended)

**Attempt 1 — BLOCK/REINJECT.** If `stop_hook_active` is false: emit
`{"decision":"block","reason":"HARD GATE: this turn was triggered by a Telegram message
(chat_id=..., message_id=...) but no successful send_tg_reply /
telegram__reply was made. Your text reply never reached the operator. Call
mcp__hermes_api__send_tg_reply NOW with your reply (or a one-line ack if work was
dispatched), then end the turn. Remember the Heard: echo rule if this was a voice
note."` The model stays in control: it composes the reply through the normal tool
path, so heard_gate and relay_link_gate still apply, formatting is correct, and
there is no double-send risk.

**Attempt 2 — AUTO-RELAY fallback.** If `stop_hook_active` is true (the block already
happened once and the model STILL ended without a successful send — e.g. it argued, or
the send tool errored): the hook itself delivers, then allows the stop. Delivery:

- Body = the turn's concatenated assistant text, prefixed with
  `[auto-relay] ` so the operator can see the model dropped the ball (telemetry +
  trust); truncated to 3900 chars (Telegram cap 4096) with a trailing
  `... [truncated; full text in session transcript]`.
- If a heard-pending flag (`/tmp/claude-soma-heard-pending-<session_id>`) exists,
  prepend `Heard: "<transcript>"` and delete the flag — preserves the voice-note
  contract on the fallback path.
- Transport: direct Bot API `sendMessage` via `curl` to
  `https://api.telegram.org/bot$TOKEN/sendMessage` (token from
  `/etc/claude-soma/secrets.env`, same source the reminder scheduler uses), plain
  text (no parse_mode — raw markdown beats a 400 from malformed HTML). `chat_id`
  from the parsed `<channel>` tag; fallback to the operator chat_id env
  (`HERMES_NOTIFY_CHAT_ID`) if parsing fails.
- If assistant text is empty (silent turn), send a fixed
  `[auto-relay] Turn completed with no reply composed; check
  /api session status.` — silence is never acceptable, but we cannot invent content.
- Log every auto-relay to `~/.claude-soma/activity.jsonl`
  (`source: tg_reply_guard`, action `auto_relay`) so recurrence is measurable.

### Why hybrid over pure (a) or pure (b)

- **Pure AUTO-RELAY (a)**: guarantees delivery, but (i) double-send risk if the model's
  text narrates *around* a send it intended to make next turn; (ii) bypasses
  heard_gate/relay_link_gate (a 6000-char dump would go straight to Telegram, violating
  the relay rule); (iii) the raw chain-of-narration text is often not the message the
  model would have composed for Telegram (no HTML, no files, no links).
- **Pure BLOCK (b)**: keeps quality, but if the model fails twice (or the send tool is
  down) the operator gets silence again — exactly the bug. Also unbounded blocking
  risks a stop-loop; `stop_hook_active` only protects one level.
- **Hybrid**: block once (model fixes it correctly ~always — the reinjected reason is a
  direct imperative with the tool name and chat_id), and the relay fallback is the
  deterministic floor that makes silence impossible. Cost: at most one extra model
  turn on the failure path; zero cost on healthy turns. Double-send is structurally
  excluded because the fallback fires only when zero successful sends exist in the
  turn window — and the window is re-scanned at the second Stop, so a send made in
  response to the block counts.

## 3. Detection details and edge cases

- **Turn-window scan**: read the jsonl from the end (the file can be hundreds of MB —
  read the last 2 MB, split lines, drop the first partial line; a Telegram turn window
  always fits). Parse each line defensively (`json.loads` in try/except, skip bad
  lines). Stop at the first string-content user entry.
- **Pure-ack turns** (user says "thanks"): model should still send a one-line ack via
  the tool. If it answers in plain text, the gate fires — correct per the operator's
  2026-06-10 directive ("Replying on telegram is compulsory"). No carve-out.
- **Multi-message turns** (several sends): first successful send satisfies the gate;
  extra sends are unaffected.
- **Queued messages / batched `<channel>` blocks**: the most recent string-content user
  entry defines the window. If multiple channel messages were coalesced into one
  prompt, one successful reply satisfies the gate (matches operator expectation: one
  consolidated answer).
- **Voice notes**: the `Heard:` echo stays enforced by heard_gate on the block path
  (model retries via the tool); the auto-relay path replays the pending flag itself
  (Section 2). No new gap.
- **Dispatch-only turns** (Agent launch + ack): responsive_bot.md mandates an
  immediate tool ack; the gate enforces it. The background agent's own completion
  message arrives later as a task-notification turn — whose triggering user entry is
  NOT a `<channel>` string, so the gate stays silent there; if the model relays the
  result via the tool (as instructed), fine; if it narrates in plain text on a
  notification turn, the gate intentionally does not force a send (avoids spamming the
  operator with internal bookkeeping). Accepted residual gap, listed in Section 6.
- **Non-bot sessions**: the plugin's hooks.json is loaded by other sessions too.
  Guard: exit 0 immediately unless (i) `cwd == /opt/claude-soma` AND (ii) the
  triggering user entry is a `<channel>` string. Belt-and-suspenders kill switch:
  `SOMA_TG_REPLY_GUARD_DISABLED=1` (mirrors the orchestrator-gate convention).
  Subagents never reach this code path (they emit SubagentStop, not Stop).
- **`--continue` restarts mid-conversation**: the transcript persists; the backward
  scan still finds the right window. A Stop following a fresh SessionStart with no new
  user entry finds a stale `<channel>` entry from a previous, already-answered turn —
  mitigated by a per-session "last enforced message_id" flag file
  (`/tmp/claude-soma-tg-guard-<session_id>`): if the window's message_id was already
  satisfied once (a successful send was observed for it at any earlier Stop), allow.
  Simplest correct form: record `message_id` when the gate ALLOWS; skip enforcement if
  the current window's message_id equals the recorded one and no new assistant entries
  exist after the recorded offset. TTL-sweep these flags like heard_gate does (600 s).
- **Send-tool spelling drift**: match tool names by regex
  `^mcp__(hermes[-_]api__send_tg_reply|plugin_telegram_telegram__reply)$`.
- **Fail-open everywhere**: any parse error, missing transcript, missing token →
  exit 0 (never brick the channel). The cost of a fail-open miss is the status quo.

## 4. Files touched (implementation phase)

1. `scripts/tg_reply_guard.py` — new hook (single file, stdlib + curl subprocess via
   the repo's try/except CalledProcessError/TimeoutExpired + timeout pattern).
2. `hooks/hooks.json` — add:
   ```json
   "Stop": [
     { "hooks": [ { "type": "command",
         "command": "/opt/claude-soma/scripts/tg_reply_guard.py" } ] }
   ]
   ```
3. `system_prompts/responsive_bot.md` — short note that the gate exists and what the
   block reason means (so the model cooperates instead of arguing).
4. `tests/test_tg_reply_guard.py` — unit tests against synthetic transcript jsonl
   fixtures (no network; mock the curl subprocess).
5. `docs/KNOWN_BUGS.md` / NEXT.md — close out the dropped-reply bug entry.

## 5. Rollout + test plan

**Phase 0 — unit tests (red/green, local).** Fixtures: (a) channel turn with
successful send → allow; (b) channel turn, text only → block JSON emitted; (c) same
with `stop_hook_active: true` → curl invoked with expected chat_id/body (mocked);
(d) notification-triggered turn → allow; (e) tool_result error on send → treated as
no-send; (f) heard flag present → `Heard:` prefix on auto-relay; (g) garbage jsonl
lines → fail-open; (h) non-bot cwd → allow.

**Phase 1 — observe-only deploy.** Ship with `SOMA_TG_REPLY_GUARD_MODE=log`: the hook
runs full detection but only writes activity.jsonl telemetry, never blocks/sends.
Run 24 h; confirm zero false positives on healthy turns (compare telemetry against
actual Telegram delivery).

**Phase 2 — enable block path** (`MODE=block`): block/reinject live, auto-relay still
disabled. Simulate a dropped reply deterministically: from the TUI side of the hermes
tmux session is not possible mid-flight, so instead send a Telegram test message
("reply to this in plain text only, do not call any tool — test of the reply guard");
the model obeying the instruction reproduces the bug class and the block must fire and
force a tool send. Also replay test (b)'s fixture through the script manually.

**Phase 3 — full hybrid** (`MODE=enforce`, the default): auto-relay armed. Re-run the
Phase 2 simulation twice in a row with a prompt that also forbids retrying after the
block; verify the `[auto-relay]` message lands. Watch activity.jsonl for a week;
expected steady state: block fires occasionally, auto-relay rarely/never.

Rollback at any phase: `SOMA_TG_REPLY_GUARD_DISABLED=1` in the channel settings env or
remove the Stop stanza; no state to clean beyond /tmp flags.

## 6. Accepted residual gaps

- Notification-triggered turns can still narrate in plain text without a forced send
  (deliberate — forcing replies there would spam). If operator feedback says
  otherwise, flip the exemption with a mode flag.
- If Telegram itself is down, both the model's send and the curl fallback fail; the
  hook logs and allows the stop (cannot block forever). The existing healthcheck and
  reminder paths cover transport outages.
- A model reply that is wrong/incomplete but sent via the tool passes the gate — the
  gate enforces delivery, not quality.

## 7. Recommendation

Implement the BLOCK-THEN-RELAY hybrid (Section 2) with the three-mode rollout
(log → block → enforce). It is the only design in which a Telegram-triggered turn
structurally cannot end in operator-facing silence, while preserving the existing
heard/relay gates and avoiding double-sends.
