# opencode-soma Feasibility

The verdict is yes, feasible: opencode 1.18.8 can replace Claude Code as the engine for a soma-style
personal assistant, driven by any model including a fully local one, installable by a stranger with
no VPS. Every load-bearing mechanism claude-soma leans on has a verified opencode equivalent — a
headless HTTP+SSE server per lead, crash-resilient sessions, instant-ack queuing, a plugin
gate/inject/log layer, MCP tools with per-agent scoping, subagents (including background dispatch),
and SQLite usage accounting. What is honestly lost is Claude Max quality (a local 32B model makes
silent judgement errors a frontier model does not), the hosted conveniences Claude Code gave for
free (channels, auto-memory, Remote Control), and unattended-24/7 robustness on one local GPU; those
become code to build in an opencode-soma daemon, not capabilities that exist out of the box. Seven
of the twelve surfaces are NEEDS-BUILD (the daemon is the product), four are DEGRADED, one is PARITY.

| # | Feature | claude-soma mechanism | opencode mechanism | Verdict | Evidence |
|---|---|---|---|---|---|
| 1 | Telegram channel | bot session + DM bridge `src/claude_soma/operator_dm.py:1-308`, unit `systemd/claude-soma-channel.service:1-93` | daemon long-poll bridge -> `prompt_async` ack -> SSE reply; no e2e run | NEEDS-BUILD | `evidence/platform/telegram-nat.log`; `desk-research-only` end-to-end bot flow with a real token not run |
| 2 | Memory, parent + per-lead | claude-soma writes no memory itself `src/claude_soma/mcp_servers/hermes_api/claude_state.py:69-75` | daemon-hosted SQLite memory MCP (single writer) | NEEDS-BUILD | `evidence/memory/bench-5000.log` |
| 3 | Voice in/out | voice-out never wired `skills/respond-with-voice/SKILL.md:16-21` | whisper-cli + piper bridge in the daemon (engine-independent); not yet wired to opencode | NEEDS-BUILD | `evidence/platform/stt-whisper.log`; `evidence/platform/tts-piper.log` |
| 4 | Project orchestration | tmux send-keys + capture-pane scrape `src/claude_soma/mcp_servers/project_orchestrator/server.py:193-230` | one `opencode serve` per lead over HTTP; subagents; independent lead serves | PARITY | `evidence/opencode/P4-subagents/basic.log`; `evidence/opencode/P8-serve/serve.log` |
| 5 | Scheduled routines | systemd timers `src/claude_soma/platform/services.py:1-555`, e.g. `systemd/claude-soma-daily-status.timer:1-11` | daemon scheduler with catch-up firing prompts over HTTP | NEEDS-BUILD | `desk-research-only` no local scheduler built or run yet; building block is the HTTP prompt API |
| 6 | Dashboard | FastAPI usage route `src/claude_soma/api/routes/usage.py:18-28` | daemon dashboard; opencode's own web UI covers per-instance session view | NEEDS-BUILD | `evidence/opencode/P8-serve/web-index.html` |
| 7 | Social posting | playwright campaign skill `skills/social-publish/SKILL.md:1-79` | playwright-mcp under opencode + persistent browser profile | NEEDS-BUILD | `evidence/platform/playwright-persist.log` |
| 8 | Extension-point parity | PreToolUse gate `scripts/orchestrator_gate.py:41-63`, inject `scripts/notify_inject.sh:1-167`, log `scripts/log_activity.sh:1-40` | plugin `tool.execute.before` gate, `chat.message`/`system.transform` inject, `tool.execute.after` log | DEGRADED | `evidence/opencode/P2-hooks/gate.log`; `evidence/opencode/P2-hooks/activity.log` |
| 9 | Model-agnostic auth | Max OAuth only `CLAUDE.md:36-40` | any provider; local ollama verified, no account | DEGRADED | `evidence/opencode/P0-setup/sandbox-verify.log`; `desk-research-only` hosted-frontier tier is documented not run |
| 10 | Remote attach | Caddy + OAuth + RC-URL refresh `src/claude_soma/install.py:578-581` | `opencode web` + `attach` on the LAN; no anywhere-relay without the user's own tunnel | DEGRADED | `evidence/opencode/P8-serve/serve.log` |
| 11 | Cross-platform no-VPS install | installer + service writer `src/claude_soma/platform/services.py:1-555` | user-level systemd / LaunchAgent; Windows via WSL2 | DEGRADED | `evidence/platform/linux-container.log`; `evidence/platform/macos-launchd.log` |
| 12 | Install wizard | `src/claude_soma/wizard/init.py:1-496` | `opencode-soma init` detect/ask/write/refuse (spec in ARCHITECTURE) | NEEDS-BUILD | `desk-research-only` wizard is specified, not built; validated unit/plist generation is the building block |

Verdicts: **PARITY** — every claude-soma behavior for the item is reproduced by components we actually
ran; only wiring remains. **DEGRADED** — works, but measurably worse in a named dimension.
**NEEDS-BUILD** — no off-the-shelf equivalent; building blocks may be verified, but real unknowns
remain in the build. **IMPOSSIBLE** — cannot be done on this stack.

## 1. Telegram channel — NEEDS-BUILD
What we ran: nothing of the bot itself; `evidence/platform/telegram-nat.log` confirms a home-NAT
long-poll client needs no inbound port. Gap: the whole bridge (getUpdates loop -> create/resume an
opencode session -> `prompt_async` for an instant ack -> stream the reply from SSE -> sendMessage)
is new daemon code; claude-soma got the channel free from Claude Code. Honest substitute: a Python
long-poll daemon; the opencode side (ack + reply) is proven by P1/P8. The one claim not verified end
to end is a real bot token round trip (`desk-research-only`), which needs an operator decision.

## 2. Memory, parent + per-lead — NEEDS-BUILD
What we ran: the memory benches (`evidence/memory/bench-5000.log`). Gap: claude-soma itself writes
no durable memory — `src/claude_soma/mcp_servers/hermes_api/claude_state.py:69-75` only reads a
Claude-Code-authored file — so opencode-soma's SQLite memory service is strictly more than parity,
but it is new code with a single-writer constraint. See MEMORY-DESIGN.md.

## 3. Voice in/out — NEEDS-BUILD
What we ran: the deterministic bridge, end to end, both directions — whisper-cli transcribed a
Telegram-style OGG/Opus note at WER 0.000 (`evidence/platform/stt-whisper.log`) and piper + ffmpeg
produced a valid Opus voice note, round-tripped at WER 0.000 (`evidence/platform/tts-piper.log`).
This pipeline is subprocess-only and engine-independent, so it ports unchanged. Gap: it is not wired
into an opencode session yet, and claude-soma's own voice-out was a stub
(`skills/respond-with-voice/SKILL.md:16-21`). Honest substitute: the daemon shells to whisper/piper
around the HTTP prompt; running claude-soma's `voice_stt` MCP under opencode would upgrade this to
DEGRADED (quality capped by local models).

## 4. Project orchestration — PARITY
What we ran: P4 in full — a lead spawns subagents via the task tool with real parent/child session
linkage (`evidence/opencode/P4-subagents/basic.log`), nesting works at `subagent_depth:2`, parallel
task calls run concurrently, background subagents return immediately and inject their result later,
and an independent `opencode serve` with its own data dir runs its own team. The HTTP API
(`evidence/opencode/P8-serve/serve.log`) replaces claude-soma's load-bearing tmux send-keys hack
(`src/claude_soma/mcp_servers/project_orchestrator/server.py:193-230`) outright. Gap: macOS has no
cgroup memory caps for leads (ARCHITECTURE covers per-OS isolation).

## 5. Scheduled routines — NEEDS-BUILD
What we ran: nothing scheduler-side; the firing mechanism (an HTTP prompt to a lead) is proven by
P1/P8. Gap: claude-soma used systemd timers (`src/claude_soma/platform/services.py:1-555`,
`systemd/claude-soma-daily-status.timer:1-11`); opencode-soma needs a daemon scheduler with
catch-up. `desk-research-only`: no local scheduler built or run in these probes.

## 6. Dashboard — NEEDS-BUILD
What we ran: opencode's own web UI is served behind basic auth (`evidence/opencode/P8-serve/web-index.html`),
which covers per-instance session viewing. Gap: claude-soma's fleet dashboard
(`src/claude_soma/api/routes/usage.py:18-28`) aggregates across leads and tails activity; that is
new daemon code.

## 7. Social posting — NEEDS-BUILD
What we ran: a persistent browser profile survives across runs (`evidence/platform/playwright-persist.log`).
Gap: the campaign flow (`skills/social-publish/SKILL.md:1-79`) under opencode with playwright-mcp was
not run here; the skill/agent conversion is proven by P9, the browser automation is not.

## 8. Extension-point parity — DEGRADED
What we ran: P2 — a plugin `tool.execute.before` blocks a tool call before it runs and the model
sees the reason (`evidence/opencode/P2-hooks/gate.log`), per-prompt context injection works, and
`tool.execute.after` logs every tool call including MCP and subagent calls
(`evidence/opencode/P2-hooks/activity.log`). These reproduce claude-soma's gate
(`scripts/orchestrator_gate.py:41-63`), inject (`scripts/notify_inject.sh:1-167`) and log
(`scripts/log_activity.sh:1-40`). Degraded dimensions: there is no blocking Stop hook; the
`permission.ask` hook never fires in 1.18.8 (use the event + reply); `system.transform` text is
sometimes ignored by a weak local model; agents and commands need conversion; the gate's
primary-vs-subagent test needs a session lookup the Claude hook got for free.

## 9. Model-agnostic auth — DEGRADED
What we ran: a fully local ollama provider with no account, every opencode path sandboxed
(`evidence/opencode/P0-setup/sandbox-verify.log`). This is strictly more flexible than claude-soma,
which is Max-OAuth-only by rule (`CLAUDE.md:36-40`). Degraded dimension: the Claude Max quality path
is gone; a hosted-frontier tier exists but was not run here (`desk-research-only`). ARCHITECTURE
carries the capability/quality tier table (deterministic / local-small / local-32B / hosted-frontier).

## 10. Remote attach — DEGRADED
What we ran: `opencode serve` exposes the full API, `opencode web` an embedded SPA, and
`opencode attach` drives a session from another process — all over the LAN behind basic auth
(`evidence/opencode/P8-serve/serve.log`). Degraded dimension: claude-soma's Remote Control gave
anywhere-access via a hosted relay (`src/claude_soma/install.py:578-581`); opencode-soma is
LAN-only unless the user installs their own tunnel (e.g. Tailscale).

## 11. Cross-platform no-VPS install — DEGRADED
What we ran: a Linux container and a macOS LaunchAgent path (`evidence/platform/linux-container.log`,
`evidence/platform/macos-launchd.log`); the service model maps to claude-soma's writer
(`src/claude_soma/platform/services.py:1-555`). Degraded dimension: Windows is WSL2-only and
desk-verified, and macOS lacks the Linux cgroup memory caps. See PLATFORM-MATRIX.md.

## 12. Install wizard — NEEDS-BUILD
What we ran: nothing of a wizard; claude-soma's installer (`src/claude_soma/wizard/init.py:1-496`)
is the reference. Gap: `opencode-soma init` (detect OS/opencode/ollama/ports, pair the bot, write
config + a user service, refuse root / unpinned opencode / non-loopback bind) is specified in
ARCHITECTURE but not built. `desk-research-only`: the building block is validated unit/plist
generation from the platform evidence.
