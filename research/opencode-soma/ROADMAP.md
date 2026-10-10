# opencode-soma roadmap

Phases P0 to P6. Each phase lists its scope, what is out of scope, an acceptance command and what
it asserts, and the evidence it rests on. Paths are under `research/opencode-soma/` unless they
start with `docs/`, `scripts/` or `src/`. The acceptance tests do not exist yet: writing each one is
part of its phase.

**Pinned versions.**

- opencode 1.18.8, the version every probe ran against (`evidence/opencode/SUMMARY.md`).
- ollama 0.33.3 with qwen3:32b (`evidence/opencode/conditions.log`).
- 27 opencode releases followed 1.18.8 in 10 weeks, up to 1.18.35
  (`evidence/platform/opencode-releases.log`). From P2 on, a newer opencode is adopted only after
  the probe suite (`probes/opencode/run-all.sh`) passes against it as a contract test (RISKS item 4).

## P0: walking skeleton (one lead session)

**Scope.**

- A laptop (macOS or Linux), a foreground process, ollama with qwen3:32b, and no OS service yet.
- A Python 3.12 package `opencode-soma`. Its daemon, `opencode-soma run`:
  - runs a Telegram long-poll bridge, behind the channel adapter interface (`receive`, `send`,
    `ack`; ARCHITECTURE.md 2.1), with a one-chat allowlist and an instant deterministic ack
    (`sendChatAction` typing, plus an "on it" line if the turn passes about 5 s);
  - launches the orchestrator `opencode serve` (127.0.0.1, random port and password, isolated XDG
    dirs) with a daemon-hosted soma MCP server: `spawn_lead`, `message_lead`, `list_leads`,
    `memory_save`, `memory_search`;
  - sends Telegram text in with `prompt_async`, collects the final assistant text from SSE at
    `session.idle`, and sends it back.
- Exactly one lead: its own `opencode serve`, cwd and data dir. Its brief goes in with
  `prompt_async`, and `message_lead` returns the lead's reply.
- Minimal SQLite memory (facts, entities, sources). The daemon is the single writer, and a
  `chat.message` recall-injection plugin on the orchestrator reads it read-only
  (MEMORY-DESIGN.md section 12).

**Out of scope.** Voice, the gate and activity plugins, OS services, the scheduler, the dashboard,
auto-capture, more than one lead, Windows.

**Acceptance:** `pytest tests/acceptance/test_p0_skeleton.py -v`. It runs against a local fake Bot
API (a stub serving `getUpdates`, `sendMessage` and `sendChatAction`), so no token is needed, and
asserts in order:

1. "Remember my build server is called kestrel": ack in under 2 s, and a memory row exists.
2. "Start a project called hello that writes hello.txt containing hi": the lead's serve is up and
   the file appears.
3. "Ask hello what it did": the reply is relayed and mentions hello.txt.
4. The daemon is killed with `kill -9` and restarted.
5. "What is my build server called?": the reply contains "kestrel". That proves recall plus crash
   resume with no wedged session.

Then a manual smoke test with a real token on an operator-made bot
(`probes/platform/telegram_longpoll_e2e.py` shows the shape).

**Evidence it rests on.**

- **Serve API.** Prompts, SSE, basic auth (401 without the password), attach:
  `evidence/opencode/P8-serve/serve.log`.
- **Acking a busy session.** A busy session takes `prompt_async` at once, and `abort` returns it to
  idle: `evidence/opencode/P1-headless/busy.log`.
- **Crash resume:** `evidence/opencode/P1-headless/crash-serve.log` and `crash-run.log`.
- **One writer per session**, or it wedges: `evidence/opencode/SUMMARY.md` item 2 and
  `P1-headless/run1/wedge-snapshot.log`.
- **An independent lead serve** with its own data dir: `evidence/opencode/P4-subagents/lead-team.log`.
- **MCP servers callable, scoped per agent:** `evidence/opencode/P3-mcp/mcp.log` and
  `scoping-verdict.log`.
- **Recall injection by `chat.message`:** `evidence/opencode/P2-hooks/inject.log`.
- **Isolated XDG dirs:** `evidence/opencode/P0-setup/sandbox-verify.log`.
- **Long polling with no inbound port:** `evidence/platform/telegram-nat.log`.
- **SQLite recall and the read-only plugin path:** `evidence/memory/bench-50000.log` and
  `bun-sqlite.log`.

## P1: voice and guards

**Scope.**

- **Voice in:** Telegram `getFile`, ffmpeg to 16 kHz mono, whisper-cli, then a deterministic
  `Heard: "..."` echo before prompting (claude-soma's convention, `scripts/heard_gate.py`).
- **Voice out:** a resident piper-tts process, ffmpeg to OGG/Opus, `sendVoice`. claude-soma never
  wired this end to end (PLATFORM-MATRIX.md).
- **Long replies:** 4096-character chunking with markdown-to-HTML.
- **Plugins:**
  - gate: `tool.execute.before` with a cached `parentID` lookup;
  - activity log: `tool.execute.after`;
  - inject: unread lead events plus recall, through `chat.message`.
- **Lead event bus:** a `soma_report` tool (STARTED, MILESTONE, COMPLETED, NEEDS_INPUT, ERROR) with
  a DM policy and NEEDS_INPUT answer routing.

**Out of scope.** OS services, the scheduler, the dashboard, memory v2, Windows.

**Acceptance:** `pytest tests/acceptance/test_p1_voice_events.py -v`. Against the fake Bot API it
asserts:

- a generated OGG voice note gets a `Heard:` echo, and the reply is a valid OGG/Opus voice note
  (ffprobe: ogg/opus);
- the gate denies `npm install` in the orchestrator and allows it in a subagent;
- a lead's NEEDS_INPUT reaches the chat, and the answer routes back to that lead.

**Evidence it rests on.**

- **Speech to text:** whisper.cpp at WER 0 on WAV and on a Telegram-style OGG/Opus note, with
  ffmpeg required (`evidence/platform/stt-whisper.log`).
- **Text to speech:** piper-tts synthesizes 5 s of speech in 0.1 s once resident, into valid
  Ogg/Opus (`evidence/platform/tts-piper.log`).
- **Gate, activity and inject plugins:** `evidence/opencode/P2-hooks/gate.log`, `activity.log` and
  `inject.log`.
- **MCP tools for the event bus:** `evidence/opencode/P3-mcp/mcp.log`.

## P2: always-on

**Scope.**

- An `opencode-soma init` wizard, v1: detect, ask, write, or refuse.
- User-level service install: systemd `--user` plus linger on Linux, a LaunchAgent on macOS, and the
  WSL2 recipe on Windows (PLATFORM-MATRIX.md).
- A persisted scheduler that catches up on start and on wake. Reminders survive a reboot, unlike
  claude-soma's detached `sleep` reminders (`src/claude_soma/mcp_servers/hermes_api/server.py:194-238`).

**Out of scope.** System-wide (root) services, the dashboard, native Windows.

**Acceptance:** `opencode-soma init --dry-run --non-interactive && pytest tests/acceptance/test_p2_scheduler.py -v`.
It asserts:

- a reminder that falls due while the daemon is down fires exactly once after the restart;
- the generated units pass `systemd-analyze verify`;
- the generated plists pass `plutil -lint`.

**Evidence it rests on.**

- **systemd units:** a user service and a timer with `Persistent=true` verify cleanly, and their
  calendar expression parses (`evidence/platform/linux-container.log`).
- **LaunchAgents:** `plutil -lint` passes, and missed `StartCalendarInterval` runs coalesce into one
  on wake (`evidence/platform/macos-launchd.log`).
- **WSL2 keep-alive recipe:** PLATFORM-MATRIX.md (desk research).

## P3: dashboard and usage

**Scope.**

- Served by the daemon on 127.0.0.1. `--lan` is opt-in, with a pairing-code login, a cookie and
  CSRF protection.
- Pages:
  - leads, each with its attach command;
  - events and activity;
  - usage, from each instance's events and HTTP API (the daemon never opens an instance's
    `opencode.db`);
  - a memory browser, routines and transcripts.

**Out of scope.** Internet exposure, multi-user accounts.

**Acceptance:** `pytest tests/acceptance/test_p3_dashboard.py -v`. It asserts:

- an unauthenticated request gets 401;
- pairing yields a cookie;
- the usage totals equal the session token sums for a scripted session.

**Evidence it rests on.**

- **Usage tokens:** per-message and per-session tokens match ollama's usage blocks
  (`evidence/opencode/P7-usage/usage.log`).
- **Auth, attach and web UI:** `evidence/opencode/P8-serve/serve.log`.
- **Activity rows:** `evidence/opencode/P2-hooks/activity.log`.

## P4: memory v2

**Scope.**

- **Auto-capture and curation** as a daemon job, with:
  - non-destructive writes;
  - a deterministic secret scrub;
  - key-based supersede;
  - a reviewable digest;
  - model-tier gating.
- **Visibility and storage:** parent and lead visibility rules, the markdown export mirror, an
  importer for `~/.claude/projects/*/memory`, and an optional Neo4j adapter (MEMORY-DESIGN.md).

**Out of scope.** Embeddings and vector search (a packaging risk; MEMORY-DESIGN.md section 12),
and curator models that bypass opencode's provider auth.

**Acceptance:** `pytest tests/acceptance/test_p4_memory.py -v`. It asserts:

- replaying the Track B planted cases on the configured model stores 0 secrets, and every
  destructive change appears only through the digest;
- the Q7a/Q7b visibility oracle passes;
- the importer converts a sample memory directory.

**Evidence it rests on.**

- **Store choice and the query shapes:** `evidence/memory/bench-*.log`, `sqlite-tuning-*.log` and
  `neo4j-fairness-50000.log`.
- **Curation safety by model tier:** `evidence/memory/curation-*.log` and `curation-rescore.log`.
- **The scrubber:** `evidence/memory/scrub-eval.log`.
- **Why the curator must be the agent's own model:** `evidence/memory/ollama-two-models.log`.
- **Neo4j operating cost:** `evidence/memory/neo4j-container.json`.

## P5: social

**Scope.** A Playwright MCP with a persistent desktop browser profile: a headed login on the user's
own desktop, with no VNC. Posting is approval-gated only.

**Out of scope.** Unattended posting without approval, and headless servers.

**Acceptance:** `pytest tests/acceptance/test_p5_social.py -v`, against a local mock site. It
asserts:

- the login cookie persists across a browser relaunch;
- a post without an approval token is refused.

**Evidence it rests on.**

- **Profile persistence:** cookie and localStorage survive a headless relaunch and a headed
  relaunch (`evidence/platform/playwright-persist.log`).
- **Playwright builds per OS:** `evidence/platform/artifacts.log`.

## P6: remote, hardening, Windows

**Scope.**

- Remote access with no serve exposed: `opencode-soma attach` on the machine, and from elsewhere
  an SSH tunnel or a user-installed Tailscale to the loopback ports, with the serve password
  (desk). Serves never bind beyond 127.0.0.1, and `--lan` exposes only the dashboard
  (ARCHITECTURE.md section 9).
- Linux lead memory caps via `systemd-run --user --scope -p MemoryMax=`, which should need no sudo
  (desk; this phase shows it).
  claude-soma needs a sudoers grant for `systemd-run` (`scripts/bootstrap.sh:454`), which
  `scripts/remote-exec-guard.sh:7` calls "arbitrary-root".
- The contract-test suite re-run on a newer opencode (1.18.35 or later).
- A real Windows 11 WSL2 run.

**Out of scope.** Native Windows (PLATFORM-MATRIX.md decision), leads on remote hosts, and a
Discord channel adapter (v2, for networks that block Telegram; RISKS.md item 8).

**Acceptance:** `opencode-soma selftest --contract && opencode-soma doctor`. It asserts:

- the contract suite passes on the pinned opencode, and its diff against 1.18.35 is reviewed;
- `doctor` finds every serve listening on 127.0.0.1 only and answering 401 without its password;
- `doctor` passes on Windows 11 WSL2.

**Evidence it rests on.**

- **Release churn:** `evidence/platform/opencode-releases.log`.
- **The probe suite that becomes the contract test:** `probes/opencode/run-all.sh`, described in
  `evidence/opencode/SUMMARY.md`.
- **Serve auth:** `evidence/opencode/P8-serve/serve.log`.
- **The WSL2 path:** PLATFORM-MATRIX.md (desk research).
- **Related risks:** RISKS.md items 4, 5 and 10.
