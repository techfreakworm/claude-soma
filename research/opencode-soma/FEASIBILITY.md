# opencode-soma Feasibility

The verdict is yes, feasible: opencode 1.18.8 can replace Claude Code as the engine for a soma-style
personal assistant, driven by any model including a fully local one, installable by a stranger with
no VPS, and every load-bearing mechanism claude-soma leans on has a verified opencode equivalent — a
headless HTTP+SSE server per lead, crash-resilient sessions, instant-ack queuing, a plugin
gate/inject/log layer, MCP tools with per-agent scoping (which holds against the model; a trusted
HTTP caller can re-grant a tool per request, so the daemon is the only client and the server
password is the boundary), subagents including background dispatch, and
SQLite usage accounting. What is honestly lost is model quality measured on local models: qwen3:32b
made 4 destructive curation calls in 100 planted cases (2 false merges, 2 false supersedes) and
stored 0 secrets even with the secret scrub off (recall 1.0, 0 leaks with the pre-scrub on), while the
1.2B lfm2.5 stored 5 secrets from 4 of 5 secret-bearing chats among 16 dangerous outcomes, and a
hosted frontier model was not tested. The single biggest risk is unattended 24/7 reliability on a local model — silent judgement
errors plus opencode's hang classes: the second-writer session wedge, pending approvals with no
server-side timeout, and no default provider timeouts in 1.18.8 (desk). The concrete DEGRADED losses
are: no anywhere-access like Claude's Remote Control (LAN/tunnel only), single-host leads (claude-soma's
multi-VPS leads are dropped), the Claude Max subscription path is gone (Anthropic is API-key only, desk),
and Windows is WSL2-only and unverified. Seven of the twelve surfaces are NEEDS-BUILD (the daemon is
the product), five are DEGRADED, and none is full PARITY.

| # | Feature | claude-soma mechanism | opencode mechanism | Verdict | Evidence |
|---|---|---|---|---|---|
| 1 | Telegram channel | `scripts/channel-claude.sh:88-96` launches the channel; routing in `system_prompts/responsive_bot.md:94-153` | daemon long-poll bridge -> `prompt_async` ack -> SSE reply | NEEDS-BUILD | `evidence/platform/telegram-nat.log`, `evidence/opencode/P1-headless/busy.log`, `evidence/opencode/P8-serve/serve.log`; `desk-research-only` the real-token round trip was not run |
| 2 | Memory, parent + per-lead | claude-soma only reads a Claude-authored file `src/claude_soma/mcp_servers/hermes_api/claude_state.py:69-75` | daemon-hosted SQLite memory service (single writer): memory tools on the soma MCP, per-prompt recall by a read-only inject plugin | NEEDS-BUILD | `evidence/memory/bench-results.json`, `evidence/memory/neo4j-container.json`, `evidence/memory/curation-qwen3_32b-think-false.log` |
| 3 | Voice in/out | voice-out stub `skills/respond-with-voice/SKILL.md:16-21`; STT `src/claude_soma/mcp_servers/voice_stt/server.py:73-87`; TTS `src/claude_soma/mcp_servers/voice_tts/server.py:55-78` | whisper-cli + piper + ffmpeg bridge in the daemon | NEEDS-BUILD | `evidence/platform/stt-whisper.log`, `evidence/platform/tts-piper.log` |
| 4 | Project orchestration | tmux send-keys/scrape + multi-VPS RemoteRunner `src/claude_soma/mcp_servers/project_orchestrator/server.py:193-230`, spawn `src/claude_soma/mcp_servers/project_orchestrator/spawner.py:479-557`, scrape-reply `skills/message-project/SKILL.md:32-91` | one `opencode serve` per lead over HTTP; subagents; independent lead serves; single host only | DEGRADED | `evidence/opencode/P4-subagents/basic.log`, `evidence/opencode/P8-serve/serve.log`, `evidence/opencode/P5-tmux/tmux.log` |
| 5 | Scheduled routines | cron routines `skills/schedule-routine/SKILL.md:58-96`, one-shot reminders are detached sleeps `src/claude_soma/mcp_servers/hermes_api/server.py:193-243`, reaper timer `systemd/claude-soma-lead-reaper.timer:11-13` | daemon scheduler firing HTTP prompts, with catch-up | NEEDS-BUILD | `evidence/platform/linux-container.log`, `evidence/platform/macos-launchd.log`; `desk-research-only` no local scheduler was built or run; reminders are not reboot-safe and the timers assume systemd |
| 6 | Dashboard | FastAPI app `src/claude_soma/api/main.py:9-44`, GitHub-OAuth gate `frontend/lib/auth.ts:4-30`, reverse proxy `Caddyfile:17-44` | daemon dashboard; opencode's own web UI covers per-instance view | NEEDS-BUILD | `evidence/opencode/P8-serve/web-index.html` |
| 7 | Social posting | playwright MCP servers `.mcp.json:43-96`, headed browser `systemd/vncserver@.service:6-17`, persistent login `scripts/pw-login.js:2-19` | playwright-mcp under opencode + persistent profile | NEEDS-BUILD | `evidence/platform/playwright-persist.log` |
| 8 | Extension-point parity | PreToolUse gate `scripts/orchestrator_gate.py:41-63`, all hooks `hooks/hooks.json:1-89`, lead MCP scoping `config/claude/lead-mcp.json:1-130` | plugin `tool.execute.before`/`chat.message`/`tool.execute.after`; MCP per-agent scoping | DEGRADED | `evidence/opencode/P2-hooks/gate.log`, `evidence/opencode/P2-hooks/extras.log`, `evidence/opencode/P3-mcp/scoping-verdict.log`, `evidence/opencode/P9-compat/compat.log` |
| 9 | Model-agnostic auth | Max-OAuth token, no API key `secrets.env.example:22-24`, injected via EnvironmentFile `src/claude_soma/mcp_servers/project_orchestrator/spawner.py:48-56` | any opencode provider; local ollama verified, no account | DEGRADED | `evidence/opencode/P0-setup/sandbox-verify.log`; `desk-research-only` the hosted-frontier tier was documented not run |
| 10 | Remote attach | Remote Control URL per lead `src/claude_soma/mcp_servers/project_orchestrator/spawner.py:497-501` | `opencode attach` + web UI on 127.0.0.1; beyond the machine only via a user-set SSH tunnel or Tailscale (ARCHITECTURE.md section 9) | DEGRADED | `evidence/opencode/P8-serve/serve.log`, `evidence/opencode/P5-tmux/attach.log` |
| 11 | Cross-platform no-VPS install | canonical runbook `INSTALL.md:3`, per-OS path resolver `src/claude_soma/platform/paths.py:128-146` | user-level systemd / LaunchAgent; Windows via WSL2 | DEGRADED | `evidence/platform/linux-container.log`, `evidence/platform/macos-launchd.log`, `evidence/platform/telegram-nat.log` |
| 12 | Install wizard | interactive installer `src/claude_soma/wizard/init.py:406-491` | `opencode-soma init` detect/ask/write/refuse (spec in ARCHITECTURE.md section 11) | NEEDS-BUILD | `desk-research-only` the wizard is specified not built; validated unit/plist generation is the building block |

Verdicts: **PARITY** — every claude-soma behavior for the item is reproduced by components we actually
ran; only wiring remains. **DEGRADED** — works, but measurably worse in a named dimension.
**NEEDS-BUILD** — no off-the-shelf equivalent; building blocks may be verified, but real unknowns
remain in the build. **IMPOSSIBLE** — cannot be done on this stack.

## 1. Telegram channel — NEEDS-BUILD
What we ran: `evidence/platform/telegram-nat.log` confirms the NAT shape (below, §11); P1/P8 prove
the opencode side of the bridge (instant `prompt_async` ack, SSE reply). Gap: the bridge itself
(`scripts/channel-claude.sh:88-96` is where Claude Code gave claude-soma the channel for free) is new
daemon code. The one unverified claim is a real bot-token round trip (`desk-research-only`), an
operator decision. The bridge must be channel-pluggable: claude-soma routes operator DMs
Discord-first because "Telegram is banned in India (~2026-06)" and its sendMessage returned HTTP 000
there (`src/claude_soma/operator_dm.py:1-7`), yet api.telegram.org WAS reachable from this laptop at
06:21Z (getMe returned a clean 401 over outbound HTTPS, `evidence/platform/telegram-nat.log`). So the
daemon needs a Telegram adapter first and a Discord adapter second behind one channel interface.

## 2. Memory — NEEDS-BUILD
claude-soma writes no durable memory of its own — `src/claude_soma/mcp_servers/hermes_api/claude_state.py:69-75`
only parses a Claude-Code-authored file — so opencode-soma's SQLite memory service is strictly more,
but new, single-writer code. Benches in `evidence/memory/bench-results.json`; see MEMORY-DESIGN.md.

## 3. Voice in/out — NEEDS-BUILD
What we ran: the deterministic bridge end to end, both directions — whisper-cli on a Telegram
OGG/Opus note at WER 0.000 (`evidence/platform/stt-whisper.log`, the subprocess call is
`src/claude_soma/mcp_servers/voice_stt/server.py:73-87`) and piper+ffmpeg round-tripped at WER 0.000
(`evidence/platform/tts-piper.log`). STT/TTS components are verified; the remaining unknown is
Telegram file transfer (getFile / sendVoice) inside the item-1 bridge, untested without a token,
hence NEEDS-BUILD. claude-soma's own voice-out was a stub (`skills/respond-with-voice/SKILL.md:16-21`).

## 4. Project orchestration — DEGRADED
The load-bearing hack the operator asked about is claude-soma's tmux `send-keys` + `capture-pane`
scrape (`src/claude_soma/mcp_servers/project_orchestrator/server.py:193-230`, driven by
`skills/message-project/SKILL.md:32-91`, leads spawned by `spawner.py:479-557`). We tested it against
opencode directly over 2 rounds x 6 messages (`evidence/opencode/P5-tmux/tmux.log`): `send-keys`
DELIVERY was 12/12 verbatim (short, long, multi-line, quotes, `$`/backticks, long-reply), and once
the turn completes a sidebar-excluded, completion-gated STRICT scrape read the reply 12/12 (so did a
plain whole-line scrape, 11-12/12 across two independent 2-round runs, post-completion) - but that completion signal itself came from the
HTTP API (the server's turn-complete status), so scraping ALONE cannot tell when a turn has ended.
And the scrape is fragile: the naive whole-line
checker FALSE-POSITIVED mid-turn on 6-8/12 (two 2-round runs: mine and the verifier's, evidence/verify/rerun/), matching the TUI's auto-generated session title before any
reply existed (a single-sample run flipped on exactly this in the verifier's C10 rerun), and long
replies are truncated to the visible screen (alt-screen, no tmux scrollback). `opencode attach`
delivery was 5/5 in a separate run (`evidence/opencode/P5-tmux/attach.log`). So on messaging
opencode-soma is strictly BETTER: the HTTP API (`evidence/opencode/P8-serve/serve.log`) is 6/6 with
none of the scrape's truncation or false-positive failure modes, and P4
(`evidence/opencode/P4-subagents/basic.log`) shows subagents and independent lead serves. The reason
this is DEGRADED, not PARITY, is the honest gap by
this doc's own definition: claude-soma's orchestration includes multi-VPS leads (the RemoteRunner
path that `server.py:193-230` opens with `if host != "local"`) and no probe ran a remote lead, so
those are dropped by design for a no-VPS single-host install; macOS also has no cgroup memory caps
for leads (ARCHITECTURE.md section 5 covers per-OS isolation).

## 5. Scheduled routines — NEEDS-BUILD
The firing mechanism (an HTTP prompt) is proven by P1/P8. Gap: claude-soma's cron routines
(`skills/schedule-routine/SKILL.md:58-96`) ran on Claude Code's scheduler, its one-shot reminders are
detached `sleep` subprocesses that do not survive a reboot (`src/claude_soma/mcp_servers/hermes_api/server.py:193-243`),
and its lifecycle timers assume systemd (`systemd/claude-soma-lead-reaper.timer:11-13`). opencode-soma
needs a daemon scheduler with catch-up. `desk-research-only`: none was built or run here.

## 6. Dashboard — NEEDS-BUILD
opencode's own web UI is served behind basic auth (`evidence/opencode/P8-serve/web-index.html`),
covering per-instance session viewing. Gap: claude-soma's fleet dashboard (`src/claude_soma/api/main.py:9-44`)
with a GitHub-OAuth allow-list (`frontend/lib/auth.ts:4-30`) behind Caddy (`Caddyfile:17-44`)
aggregates across leads — new daemon code.

## 7. Social posting — NEEDS-BUILD
A persistent browser profile survives across runs (`evidence/platform/playwright-persist.log`). Gap:
claude-soma drives playwright MCP servers (`.mcp.json:43-96`) in a headed VNC browser
(`systemd/vncserver@.service:6-17`) with a persisted login (`scripts/pw-login.js:2-19`); the campaign
flow under opencode was not run, only the skill/agent conversion (P9) and the profile persistence.

## 8. Extension-point parity — DEGRADED
See the full accounting below. Core result: a plugin `tool.execute.before` blocks a call before it
runs and the model sees the reason (`evidence/opencode/P2-hooks/gate.log`), reproducing the gate
(`scripts/orchestrator_gate.py:41-63`); the whole Claude hook set (`hooks/hooks.json:1-89`) maps
across except the Stop hook; and per-agent MCP scoping (`config/claude/lead-mcp.json:1-130`) holds
against the model (`evidence/opencode/P3-mcp/scoping-verdict.log`).

### 8a. Full accounting: what exists, what must be built, what cannot be done

| claude-soma hook (`hooks/hooks.json:1-89`) | opencode-soma | status |
|---|---|---|
| SessionStart `session_start_context.sh` | inject on the first prompt (chat.message / system.transform) | exists (P2) |
| UserPromptSubmit `notify_inject.sh` | `chat.message` synthetic part, re-read per prompt | exists, verified (`evidence/opencode/P2-hooks/inject.log`) |
| UserPromptSubmit `voice_intake.sh` | a no-op in claude-soma; the daemon voice bridge handles it | n/a (bridge) |
| PreToolUse `orchestrator_gate.sh` | `tool.execute.before` + a parentID lookup, one per session, p50 0.73ms (`evidence/opencode/P2-hooks/extras.log`) | exists |
| PreToolUse `heard_gate.py` / `relay_link_gate.py` | the deterministic daemon bridge (not a model hook) | substitute |
| PostToolUse `log_activity.sh` | `tool.execute.after` JSONL (incl. MCP + subagent calls) | exists (`evidence/opencode/P2-hooks/activity.log`) |
| Stop `tg_reply_guard.py` / `discord_reply_guard.py` | NO blocking Stop hook in 1.18.8 — cannot be a hook; substitute: the bridge auto-forwards the final assistant text | must-build (substitute) |
| 18 skills `skills/` | load from `.opencode/skill/` with Claude `allowed-tools` frontmatter intact; project `.claude/skills` is NOT read (desk) | exists (`evidence/opencode/P9-compat/compat.log`) |
| 5 agents `agents/` | converted to opencode agent frontmatter and accepted | exists (P9) |
| commands | opencode `.opencode/command/*.md`, served | exists (P9) |
| lead MCP set `config/claude/lead-mcp.json:1-130` | per-agent deny holds against the model, but a trusted HTTP caller can re-grant a tool per request, so the daemon must be the only client and the server password is the boundary | exists, qualified (`evidence/opencode/P3-mcp/scoping-verdict.log`) |
| permission.ask hook | dead in 1.18.8 — fired 0 times; use the `permission.asked` event + `POST /permission/{id}/reply` | must-use-event (`evidence/opencode/P2-hooks/extras.log`) |

## 9. Model-agnostic auth — DEGRADED
A fully local ollama provider, no account, every path sandboxed (`evidence/opencode/P0-setup/sandbox-verify.log`),
is strictly more flexible than claude-soma's Max-OAuth-only rule (`secrets.env.example:22-24`,
injected via an EnvironmentFile `src/claude_soma/mcp_servers/project_orchestrator/spawner.py:48-56`).
Degraded dimension: the Claude Max quality path is gone and a hosted-frontier tier was not run. The
capability/quality tier table, measured vs desk:

| Capability | Deterministic | Local small (lfm2.5 1.2B) | Local 32B (qwen3:32b) | Hosted frontier | Fails silently? |
|---|---|---|---|---|---|
| Telegram ack / routing | bridge echo (no model) | n/a | n/a | n/a | no |
| Voice STT/TTS | whisper+piper WER 0.000 (measured) | n/a | n/a | n/a | no |
| Orchestrator tool use | — | unreliable | tool calls OK in P1-P4 (measured) | desk: expected OK | yes if the model skips a tool |
| Gate re-routing | gate is deterministic (measured) | — | — | — | no |
| Recall injection | inject is deterministic; model must quote it | not measured | qwen3 quoted msg, sometimes ignored system (measured) | desk: not tested | yes |
| Memory curation | pre-scrub is deterministic | FAIL: 16 dangerous, 5 secrets from 4/5 chats (measured) | extraction precision 1.0 / recall 0.903, 4 destructive, 0 secrets with the scrub off; recall 1.0 with the scrub on (measured) | desk: not tested | yes (silent bad merges) |
| Lead coding | — | too weak | works, turn p50 7.5s / p90 50s (measured) | desk | partial |
| Social drafting (approval-gated posting) | — | too weak | plausible, not run under opencode | desk | yes |
| Scheduling | cron is deterministic | n/a | n/a | n/a | no |
| Usage accounting | counts are exact (measured, P7) | input/output/cache match ollama (reasoning recorded as 0) | input/output/cache match ollama (reasoning recorded as 0) | desk: not tested | no |

Numbers: curation from `evidence/memory/curation-results.json`
(`evidence/memory/curation-qwen3_32b-think-false.log`, `evidence/memory/curation-lfm2.5-thinking-think-true.log`);
timing from `evidence/opencode/conditions.log`. Hosted-frontier is `desk-research-only` (ChatGPT
Plus/Pro OAuth or an API-key provider), not run.

## 10. Remote attach — DEGRADED
`opencode attach`, `run --attach` and the web UI drive a session on 127.0.0.1 behind basic auth,
verified on loopback (`evidence/opencode/P8-serve/serve.log`), and attach delivered 5/5
(`evidence/opencode/P5-tmux/attach.log`). Architecture decision (approved, ARCHITECTURE.md section 9):
opencode serves NEVER bind beyond loopback; the LAN gets only the paired soma dashboard (`--lan`
opt-in), and anywhere-access needs the operator's own SSH tunnel or Tailscale (desk). Degraded
dimension: claude-soma's Remote Control gave anywhere-access via a hosted relay URL per lead
(`src/claude_soma/mcp_servers/project_orchestrator/spawner.py:497-501`); opencode-soma reaches a
remote device only through a tunnel the operator sets up (LAN/remote is `desk-research-only`).

## 11. Cross-platform no-VPS install — DEGRADED
A Linux container and a macOS LaunchAgent path (`evidence/platform/linux-container.log`,
`evidence/platform/macos-launchd.log`; the LaunchAgent plist was `plutil -lint`-validated only, never
loaded, per PLATFORM-MATRIX.md) map to the per-OS resolver (`src/claude_soma/platform/paths.py:128-146`,
runbook `INSTALL.md:3`). The NAT result (`evidence/platform/telegram-nat.log`): the host is a private
`192.168.0.4` behind NAT; a fake-token `getMe` returned JSON 401 over outbound HTTPS; a 10s held
request survived (11.1s); long polling needs no inbound port while a webhook would (webhook ports
443/80/88/8443); the real 30-50s hold with a live token was not run. Note claude-soma itself
long-polls (no webhook route), so its VPS was for being always-on, not for reachability. Degraded:
Windows is WSL2-only and desk-verified, and macOS lacks Linux cgroup memory caps (PLATFORM-MATRIX.md).

## 12. Install wizard — NEEDS-BUILD
claude-soma's installer (`src/claude_soma/wizard/init.py:406-491`) is the reference. `opencode-soma
init` (detect OS/opencode/ollama/ports, pair the bot, write config + a user service, refuse
root / unpinned opencode / non-loopback bind) is specified in ARCHITECTURE.md section 11 but not built.
`desk-research-only`: the building block is validated unit/plist generation from the platform evidence.
