# Hermes-Claude Design

| | |
|---|---|
| **Status** | Draft for review |
| **Author** | Mayank Gupta (techfreakworm) |
| **Date** | 2026-05-22 |
| **Source-of-record** | This document |
| **Reference reading** | [Hermes-Agent](https://github.com/NousResearch/hermes-agent), Claude Code docs at <https://code.claude.com/docs/> |

---

## 1. Executive Summary

Hermes-Claude is a Claude Code **plugin + companion services** that delivers the full Hermes-Agent product surface — multi-platform messaging, scheduled autonomous work, multi-agent project orchestration, voice in/out, mobile reachability — by riding Claude Code's native primitives instead of reimplementing them.

It runs on a single Oracle Cloud free-tier Ubuntu ARM VPS, authed via Claude Max subscription (no Anthropic API key), and exposes a showcase-grade dashboard at `claude.mayankgupta.in`.

The architectural thesis: **if the engine is Claude Code, the platform layer shrinks ~10×**. Hermes-Agent is ~27,000 LOC; Hermes-Claude lands at ~4,000 LOC.

V1 timeline: ~4 weeks. V1 messaging surface: Telegram only. Trading integrations explicitly deferred to V2.

---

## 2. Why This Project

### Two motivations

**Personal**: Mayank runs multiple parallel workstreams — algo trading, AI/ML projects (LoRA training, music generation, video generation, mobile fine-tuning), portfolio content production, social campaigns. A persistent agentic platform that's reachable by voice from a phone — that can spawn dedicated project-leads on demand, each with their own team — collapses a lot of context-switching cost.

**Portfolio showcase (phase 2)**: The mayankgupta.in revamp positions trending AI + fintech projects. A live, working, multi-agent Claude Code platform — exposed at a subdomain with an observable dashboard — is a strong differentiator. The "engine is Claude Code, here's what we DON'T build because Claude already does it" narrative is fresh.

### Why not fork Hermes-Agent?

1. Hermes-Agent's engine layer (anthropic adapters, gemini adapters, openai adapters, agent loop, context engine, conversation compressor, trajectory compressor, mini SWE runner, batch runner) makes sense for a *provider-agnostic* agent platform. For a Max-subscription-only deployment, all of that is dead weight.
2. Hermes-Agent's session model, skills system, MCP system, and curator/memory subsystem **predate** Claude Code's native equivalents. Claude Code now has all of those plus durable agent teams, server-side routines, remote control, mobile push, the agent view supervisor — by riding those, we delete ~80% of what would otherwise need to be built.
3. A greenfield codebase is more honest about the design thesis. Forking would imply incremental improvement; a clean repo says "look at how small this can be."

### Hard constraints

| Constraint | Source |
|---|---|
| Must work with Claude Max subscription auth only — no `ANTHROPIC_API_KEY` anywhere | Explicit user requirement |
| Must leverage Claude Code's native primitives wherever they exist | Explicit user requirement |
| Image generation via Codex CLI (user has Codex sub) — NOT via Claude image tools | Explicit user requirement |
| Multi-agent orchestration: Telegram as orchestrator, project-leads as independent sessions, teams under each project-lead | Explicit user requirement |
| Self-host the dashboard on the same OCI VPS, no Vercel | Explicit user requirement |
| Showcase-grade dashboard from day one | Explicit user requirement |
| Python for the backend / MCP servers | Explicit user requirement |
| OCI Ubuntu ARM VPS for the host | Explicit user requirement |

---

## 3. Architecture Overview

```
                              EXTERNAL WORLD
                              ──────────────
              ┌────────────────────────────────────────────────┐
              │  Telegram (V1)                                  │
              │  Discord / iMessage / WhatsApp / Signal / Email │
              │  / Slack — V2                                   │
              └────────────────────────┬───────────────────────┘
                                       │
                                       ▼
              ┌────────────────────────────────────────────────┐
              │  OCI Ubuntu ARM VPS (Ampere A1, 4 vCPU / 24 GB) │
              │                                                 │
              │  systemd                                        │
              │   ├─ hermes-claude-channel.service              │
              │   │     claude --channels plugin:telegram@...   │
              │   │            --plugin-dir /opt/hermes-claude  │
              │   │   ↓ authed via CLAUDE_CODE_OAUTH_TOKEN (Max)│
              │   │   ↓ persistent interactive session          │
              │   │                                             │
              │   ├─ hermes-claude-api.service                  │
              │   │     uvicorn FastAPI :9000                   │
              │   │                                             │
              │   ├─ hermes-claude-frontend.service             │
              │   │     node next-standalone :3000              │
              │   │                                             │
              │   ├─ caddy.service                              │
              │   │     :443 → claude.mayankgupta.in            │
              │   │     auto-HTTPS via Let's Encrypt            │
              │   │                                             │
              │   └─ Health + reaper systemd timers             │
              └────────────────────────────────────────────────┘

         ┌──────────────────────────────────────────────────────┐
         │  ANTHROPIC-HOSTED (drawn from Max plan)               │
         │   • /schedule Routines — cloud cron                   │
         │   • Claude on Web — used for v2 cloud sessions        │
         │   • Mobile push via PushNotification + Remote Control │
         └──────────────────────────────────────────────────────┘

         ┌──────────────────────────────────────────────────────┐
         │  M5 Max (optional dev/personal use machine)           │
         │   - claude OAuth'd to SAME Max account                │
         │   - Can optionally host iMessage channel (V2)         │
         │   - Used for development of hermes-claude itself      │
         └──────────────────────────────────────────────────────┘
```

### Boundary clarity

- **Claude is the brain.** The plugin gives it eyes (channels), hands (MCPs), and a calendar (routines).
- **Hermes-Claude is the body parts.** Not an agent loop — Claude Code IS the agent loop.
- **Dashboard is read-mostly + targeted writes.** It surfaces state and exposes a handful of write actions (broadcast, run-routine, pause-all).

---

## 4. Orchestration Topology

### The shape

The Telegram channel session is an **orchestrator**, NOT a team-lead. It spawns multiple **independent project-leads** (each is its own Claude Code background session), and each project-lead creates its own team. This sidesteps the Claude Code constraint "one team per lead / no nested teams."

```
TELEGRAM CHANNEL SESSION (orchestrator — no teams of its own)
   │  Maintains registry: project_name → agentId
   │  Uses project-orchestrator-mcp to manage projects
   │
   ├──► PROJECT-LEAD-A: "web-scraper-lead"
   │       cwd: ~/projects/web-scraper/
   │       team: "web-scraper-team"
   │       ├── teammate: database-engineer
   │       ├── teammate: backend-engineer
   │       └── teammate: playwright-engineer
   │
   ├──► PROJECT-LEAD-B: "llm-app-lead"
   │       team: "llm-app-team"
   │       ├── teammate: model-eval-engineer
   │       ├── teammate: prompt-engineer
   │       └── teammate: ui-engineer
   │
   └──► PROJECT-LEAD-C: "server-app-lead"
           team: "server-app-team"
           ├── teammate: api-engineer
           ├── teammate: db-engineer
           └── teammate: ops-engineer
```

### Communication channels (all native)

| From → To | Mechanism |
|---|---|
| Telegram → any project-lead | `SendMessage(to: <agentId>, ...)` |
| Project-lead → its teammates | `SendMessage(to: "database-engineer", ...)` (by name within team) |
| Project-lead → Telegram orchestrator | `SendMessage(to: <orch-agentId>, ...)` |
| Teammate ↔ teammate (same team) | `SendMessage` by name |
| Anything → user | Channel reply OR `PushNotification` to mobile |

### Three control surfaces (all on one Max account)

| Surface | Controls |
|---|---|
| Phone, Telegram chat | The orchestrator (high-level: spawn / list / message / kill projects) |
| Phone, Claude mobile app via Remote Control | Any specific project-lead or teammate, by attaching to its RC URL |
| Laptop, dashboard at claude.mayankgupta.in/admin | All of the above + live state + bulk operations |

### Project templates

`spawn-project` takes a `type` argument that picks defaults:

| `type=` | Default cwd | Default teammates | Default MCPs | Default skills |
|---|---|---|---|---|
| `web-scraper` | `~/projects/<name>/` | database, backend, playwright | filesystem, web-fetch, playwright-mcp | bs-scraping, async-helpers |
| `llm-app` | `~/projects/<name>/` | model-eval, prompt-engineer, ui | filesystem, web-fetch, huggingface-mcp | claude-api, ai-sdk |
| `server-app` | `~/projects/<name>/` | api-engineer, db-engineer, ops | filesystem, postgres-mcp, docker-mcp | server-patterns, nextjs |
| `agentic-coding` | `~/projects/<name>/` | (none — lead with on-demand subagents) | filesystem, web-fetch | superpowers:* |
| `custom` | user-specified | user-specified | user-specified | user-specified |

Templates live in `/opt/hermes-claude/templates/projects/<type>.json`.

### Constraints to respect

1. **RAM budget on OCI 24 GB**: ~6-8 concurrent project sessions. Orchestrator caps at 6.
2. **Max credit burn scales with concurrency** — each teammate is a full session.
3. **Permission inheritance**: orchestrator stays in `default`; project-leads start in `acceptEdits`; user can promote to `auto` from Telegram on demand.
4. **Cold start cost**: ~10-30s for a new project-lead to be ready.
5. **Restart resilience**: registry survives reboots; on systemd restart of channel, channel reads registry and respawns project-leads from saved cwds; auto-memory carries state.

---

## 5. V1 Scope (Frozen)

### IN

```
Infrastructure
  - OCI Ubuntu ARM VPS bootstrapped
  - claude installed, CLAUDE_CODE_OAUTH_TOKEN minted (Max)
  - Caddy reverse proxy + auto-HTTPS for claude.mayankgupta.in
  - systemd units (channel, api, frontend, caddy)
  - Health check + idle reaper systemd timers
  - Log rotation

Claude Code plugin (hermes-claude)
  Skills:
    - codex-image-gen        (delegate image gen to Codex CLI)
    - schedule-routine       (wrap RemoteTrigger API)
    - respond-with-voice     (TTS reply path)
    - voice-action           (orchestrate voice intake)
    - spawn-project          (instantiate project-lead by type)
    - list-projects          (enumerate active project-leads)
    - message-project        (SendMessage to a project-lead by name)
    - kill-project           (graceful shutdown)
    - project-status         (status digest from registry + task list)
    - portfolio-status       (what am I working on summary)
    - usage-report           (surface /usage in chat format)

  Subagents:
    - content-drafter        (social/blog content)
    - tool-builder           (scaffold new MCP servers)
    - project-lead           (template for all project-lead spawns)

  Hooks:
    - SessionStart           (load AI/ML project context)
    - UserPromptSubmit       (detect voice meta → call STT MCP)
    - PostToolUse            (write activity to log for dashboard SSE)

  MCP servers (Python stdio):
    - voice_stt              (whisper-large-v3-turbo wrapper)
    - voice_tts              (piper en_US-ryan-medium wrapper)
    - project_orchestrator   (spawn/list/message/kill + SQLite registry)
    - hermes_api             (read-only state + write actions for dashboard)

  Project templates:
    - web-scraper.json
    - llm-app.json
    - server-app.json
    - agentic-coding.json
    - custom.json

  Setup wizard:
    - hermes_claude_init.py  (clean VPS → working system in 30 min)

Dashboard (claude.mayankgupta.in)
  Public:
    - Animated landing
    - Live anonymized stats (messages today, active projects, decisions)
    - Architecture diagram
    - Thesis paragraphs
    - Demo video placeholder
  Admin (single-user GitHub OAuth):
    - /admin              overview + live SSE activity
    - /admin/projects     react-flow project tree, click → RC URL
    - /admin/conversations Telegram thread browser
    - /admin/routines     list, run, delete (create via Telegram only in V1)
    - /admin/usage        Recharts burn chart (interactive + Agent SDK buckets)
    - /admin/memory       read-only MEMORY.md inspector
    - /admin/logs         basic activity feed

Backend
  - FastAPI hermes-claude-api with REST + SSE endpoints
  - Auth.js v5 + GitHub OAuth + handle allowlist
  - Bridge to channel session via hermes_api MCP over unix socket

Scheduled jobs (V1)
  - Weekday morning brief                  (RemoteTrigger routine)
  - Sunday memory consolidation             (RemoteTrigger routine)
  - Monthly OAuth expiry reminder           (RemoteTrigger routine)
  - Channel-session health check            (systemd timer + curl, 10min)
  - Dashboard cache refresh                 (systemd timer + python, 5min)
  - Daily usage snapshot                    (systemd timer + claude -p, 23:55)
  - Project idle reaper                     (systemd timer + python, 6h)
```

### OUT — Deferred (V1.5 / V2)

- All trading MCPs (Zerodha, Dhan, market data) — V2
- Memory inspector edit-in-place — V1.5
- Logs filter UI — V1.5
- Routine creation via dashboard form — V1.5
- Sandboxed try-it on landing page — V2
- Custom channels (WhatsApp, Signal, Email, Discord, iMessage) — V2
- Slack first-party integration — V1.5 (~30 min config when ready)
- Transcript FTS MCP — V1.5
- Always-on content-team / personal-team — V2
- HF training watch / paper-track / portfolio-content-drafter skills — V2
- OSS marketplace ship + blog post + demo video — V1.5
- Voice cloning, multi-voice TTS, emotion control — V2

### LOC budget

| Component | LOC est |
|---|---|
| Skills (11 in V1) | ~600 |
| Subagent templates (3) | ~250 |
| Hooks | ~100 |
| voice_stt MCP | ~150 |
| voice_tts MCP | ~150 |
| project_orchestrator MCP | ~400 |
| hermes_api MCP | ~400 |
| Project templates (5) | ~150 |
| Setup wizard | ~500 |
| FastAPI backend | ~600 |
| Next.js frontend | ~1500 |
| systemd units + Caddyfile + healthcheck.sh | ~200 |
| **Total** | **~5,000** |

### Week-by-week ship plan

| Week | Goal | Definition of done |
|---|---|---|
| 1 | OCI bootstrap + Telegram channel + voice STT + systemd unit | Send a voice memo on Telegram → see transcript become a Claude prompt → get text reply back. Survives reboot. |
| 2 | Voice TTS + Codex skill + project orchestrator MCP + spawn-project skill | Voice round-trip works. "Build me a web scraper" spawns a project-lead in own cwd, returns RC URL. |
| 3 | Dashboard skeleton (overview, project tree, conversations, routines, usage) + hermes_api MCP + Auth.js | Dashboard renders live state, OAuth gate works, SSE activity stream populated. |
| 4 | Showcase polish + setup wizard + scheduled jobs + idle reaper + memory page + logs feed | Public landing page shipped. `hermes_claude_init.py` brings a fresh VPS up cleanly. Steady-state burn stays inside Max quota. |

---

## 6. Deployment & Auth

### Host

Oracle Cloud Infrastructure Free Tier — Ampere A1 (ARM):
- 4 OCPU
- 24 GB RAM
- 50 GB block storage
- Always-on, no eviction concerns at this size
- Generous outbound bandwidth

### Auth

```bash
# One-time on M5 Max (or any browser-capable machine)
$ claude setup-token
# Browser OAuth flow → mints CLAUDE_CODE_OAUTH_TOKEN, valid 1 year

# On OCI VPS
$ sudo install -d -m 700 /etc/hermes-claude
$ echo 'CLAUDE_CODE_OAUTH_TOKEN=oat-...' | sudo tee /etc/hermes-claude/secrets.env
$ sudo chmod 600 /etc/hermes-claude/secrets.env
```

Same Max account on M5 Max and OCI. Anthropic permits this; usage from both adds against one Max quota.

### Process topology (systemd)

```
systemd
├─ hermes-claude-channel.service       persistent claude --channels session
├─ hermes-claude-api.service           uvicorn FastAPI :9000
├─ hermes-claude-frontend.service      node .next/standalone :3000
├─ caddy.service                       :443 reverse proxy + auto-HTTPS
│
├─ hermes-healthcheck.timer            every 10 min
├─ hermes-cache-refresh.timer          every 5 min
├─ hermes-usage-snapshot.timer         daily at 23:55
└─ hermes-idle-reaper.timer            every 6 hours
```

### Caddyfile

```
claude.mayankgupta.in {
    handle_path /api/* { reverse_proxy localhost:9000 }
    handle           { reverse_proxy localhost:3000 }
    encode gzip zstd
    log { output file /var/log/caddy/access.log }
}
```

### Channel launch command

```bash
claude --channels plugin:telegram@claude-plugins-official \
       --plugin-dir /opt/hermes-claude \
       --add-dir /home/ubuntu/hermes-work \
       --permission-mode default
```

Run inside a tmux pane managed by `hermes-claude-channel.service` for SSH-debuggability + systemd supervision.

### Permission baseline

```json
{
  "permissions": {
    "defaultMode": "default",
    "allow": [
      "Read(/home/ubuntu/hermes-work/**)",
      "Read(/home/ubuntu/projects/**)",
      "Bash(ls *)", "Bash(cat *)", "Bash(grep *)", "Bash(find *)",
      "Bash(piper *)", "Bash(whisper *)", "Bash(codex *)",
      "WebFetch(domain:*)",
      "Skill(*)",
      "Agent(*)"
    ],
    "ask": ["Bash(curl *)", "Bash(wget *)"],
    "deny": ["Read(/etc/**)", "Read(/root/**)", "Read(./secrets/**)"]
  }
}
```

Project-leads override `defaultMode` to `acceptEdits` via their own `.claude/settings.json` in their cwd.

### Deploy story (V1)

```bash
# Dev machine
git push origin main

# OCI VPS
cd /opt/hermes-claude
git pull
pnpm --filter frontend build
sudo systemctl restart hermes-claude-frontend hermes-claude-api
```

No CI/CD in V1. GitHub Action added V1.5.

---

## 7. The Plugin (`/opt/hermes-claude/`)

### Repo layout

```
hermes-claude/
├─ .claude-plugin/
│   ├─ plugin.json
│   └─ marketplace.json
│
├─ skills/
│   ├─ codex-image-gen/SKILL.md
│   ├─ schedule-routine/SKILL.md
│   ├─ respond-with-voice/SKILL.md
│   ├─ voice-action/SKILL.md
│   ├─ spawn-project/SKILL.md
│   ├─ list-projects/SKILL.md
│   ├─ message-project/SKILL.md
│   ├─ kill-project/SKILL.md
│   ├─ project-status/SKILL.md
│   ├─ portfolio-status/SKILL.md
│   └─ usage-report/SKILL.md
│
├─ agents/
│   ├─ content-drafter.md
│   ├─ tool-builder.md
│   └─ project-lead.md
│
├─ hooks/
│   └─ hooks.json
│
├─ mcp_servers/
│   ├─ voice_stt/
│   ├─ voice_tts/
│   ├─ project_orchestrator/
│   └─ hermes_api/
│
├─ templates/
│   └─ projects/
│       ├─ web-scraper.json
│       ├─ llm-app.json
│       ├─ server-app.json
│       ├─ agentic-coding.json
│       └─ custom.json
│
├─ wizard/
│   └─ hermes_claude_init.py
│
├─ frontend/                       # Next.js 16 dashboard
├─ api/                            # FastAPI backend
│
├─ scripts/
│   ├─ deploy.sh
│   ├─ healthcheck.sh
│   ├─ rotate-oauth.sh
│   └─ reaper.py
│
└─ docs/
    ├─ getting-started.md
    ├─ architecture.md
    └─ superpowers/
        └─ specs/
            └─ 2026-05-22-hermes-claude-design.md  ← this file
```

### Skill details (V1)

Each skill is ~30-120 LOC of YAML frontmatter + markdown body.

| Skill | Trigger | Calls |
|---|---|---|
| `codex-image-gen` | "draw / render / generate an image of X" | shell `codex --image --prompt "..." --output ...` |
| `schedule-routine` | "every X at Y, do Z" | `RemoteTrigger.create(...)` |
| `respond-with-voice` | Voice-in messages OR explicit user request | `voice_tts.synthesize`, then channel `reply_voice` |
| `voice-action` | UserPromptSubmit detects audio meta | `voice_stt.transcribe`, then continues normal handling |
| `spawn-project` | "Build me X" / "Set up a Y" | `project_orchestrator.spawn_project(name, type, brief)` |
| `list-projects` | "What's running?" | `project_orchestrator.list_projects()` |
| `message-project` | "Tell X to do Y" | `SendMessage(to: <project-agentId>, ...)` |
| `kill-project` | "Shut down X" | `project_orchestrator.kill_project(name)` |
| `project-status` | "Status of X" | `project_orchestrator.get_status(name)` |
| `portfolio-status` | "What am I working on?" | reads `~/.claude/projects/*/MEMORY.md` summaries |
| `usage-report` | "How much quota left?" | `claude -p '/usage' --output-format json` (yes, this hits Agent SDK bucket — very cheap, once on demand) |

### Subagent details (V1)

| Subagent | Used by | Purpose |
|---|---|---|
| `content-drafter` | Ad-hoc + future content-team | Drafts tweets, LinkedIn, Medium posts in user's voice |
| `tool-builder` | When user says "I need a new MCP for X" | Scaffolds a stdio MCP server skeleton with chosen tools |
| `project-lead` | Instantiated by `spawn-project` skill | Carries the per-type defaults; not invoked directly |

### Hook details (V1)

```json
{
  "hooks": {
    "SessionStart": [{
      "hooks": [{ "type": "command", "command": "/opt/hermes-claude/scripts/session_start_context.sh" }]
    }],
    "UserPromptSubmit": [{
      "if": "event.meta.audio_path != null",
      "hooks": [{ "type": "command", "command": "/opt/hermes-claude/scripts/voice_intake.sh" }]
    }],
    "PostToolUse": [{
      "matcher": ".*",
      "hooks": [{
        "type": "command",
        "command": "/opt/hermes-claude/scripts/log_activity.sh",
        "async": true
      }]
    }]
  }
}
```

- `session_start_context.sh` writes a context block listing active projects (from registry), recent commits in `~/Projects/llm/*`, mayankgupta.in build state.
- `voice_intake.sh` calls voice_stt and rewrites the prompt.
- `log_activity.sh` appends a JSON line to `~/.hermes-claude/activity.jsonl` for the dashboard SSE stream.

### MCP server details (V1)

#### `voice_stt`
- Tools: `transcribe(audio_path, language="auto") -> {text, language_detected, duration_seconds, confidence}`
- Backed by `whisper.cpp` with `ggml-large-v3-turbo.bin` (kept resident via warmup)

#### `voice_tts`
- Tools: `synthesize(text, voice="default") -> {audio_path, duration_seconds, voice_used}`
- Backed by `piper` with `en_US-ryan-medium` (warm)
- ffmpeg converts WAV → OGG/Opus for Telegram voice notes

#### `project_orchestrator`
- Tools:
  - `spawn_project(name, type, brief, permission_mode="acceptEdits") -> {agent_id, rc_url, cwd}`
  - `list_projects() -> [{name, agent_id, type, status, cwd, last_activity}]`
  - `send_to_project(name, message) -> {sent_at}`
  - `kill_project(name, archive=true) -> {killed_at}`
  - `get_status(name) -> {agent_id, recent_tasks, recent_activity, idle_for_seconds}`
- Persists registry in `/opt/hermes-claude/registry.sqlite`
- Spawns leads via `claude --bg --name <name> --add-dir <cwd> --permission-mode <mode> "<brief>"`
- Enables Remote Control on each spawn via env var or post-spawn `/remote-control` toggle

#### `hermes_api`
- Tools (exposed to channel session AND consumed by FastAPI over unix socket):
  - `list_active_sessions() -> [...]`
  - `read_transcripts(thread_id, limit=50) -> [...]`
  - `list_routines() -> [...]` (proxy to RemoteTrigger.list)
  - `read_memory(project) -> str`
  - `read_activity_log(since, limit) -> [...]`
  - `broadcast(message) -> {sent_at}` (asks orchestrator to post to Telegram)
  - `pause_all_projects() -> {paused_count}`
- This is the dashboard's bridge into Claude's runtime state.

### Project templates

Each `templates/projects/<type>.json` shape:

```json
{
  "type": "web-scraper",
  "cwd_root": "~/projects",
  "permission_mode": "acceptEdits",
  "default_brief": "You are the lead of a web scraper project...",
  "teammates": [
    { "name": "database-engineer", "agent": "general-purpose", "model": "sonnet" },
    { "name": "backend-engineer", "agent": "general-purpose", "model": "sonnet" },
    { "name": "playwright-engineer", "agent": "general-purpose", "model": "sonnet" }
  ],
  "mcp_servers": ["filesystem", "web-fetch", "playwright-mcp"],
  "skills": ["bs-scraping", "async-helpers"],
  "session_init_hooks": ["scripts/web_scraper_init.sh"]
}
```

The `project_orchestrator` MCP reads this template on `spawn_project` and uses it to compose the spawn command and post-spawn TeamCreate flow.

---

## 8. Voice Pipeline

### STT (incoming voice)

```
User sends voice memo on Telegram
   │
   ▼
telegram@claude-plugins-official downloads .ogg/.opus to /tmp/voice_X.ogg
   │
   ▼
Channel emits `notifications/claude/channel` with audio_path in meta
   │
   ▼
UserPromptSubmit hook fires `voice_intake.sh`:
   - Calls voice_stt MCP's transcribe(audio_path)
   - Rewrites the prompt to: "[voice transcript]: <text>"
   - Keeps original audio_path in meta for downstream reference
   │
   ▼
Claude processes normally
```

**Model**: `ggml-large-v3-turbo` — multilingual (handles Hindi mixed input), best lightweight quality, ~real-time on ARM Ampere A1, ~1.5 GB resident.

### TTS (outgoing voice)

```
Claude decides to reply with voice
   - Triggered by: voice-in message (default mirror), OR explicit user pref, OR
     short-plain-text reply that's voice-friendly
   - Suppressed for replies containing code blocks, tables, URLs
   │
   ▼
Invokes respond-with-voice skill
   │
   ▼
voice_tts MCP synthesize(text):
   - piper en_US-ryan-medium → WAV
   - ffmpeg → OGG/Opus
   - Returns audio_path
   │
   ▼
Channel reply_voice tool uploads to Telegram as voice note
```

**Voice**: `en_US-ryan-medium` — male, natural, fast (~0.5-1s per sentence).

### Latency budget (target)

| Stage | Latency |
|---|---|
| STT (whisper-large, 10s audio) | 3-5s |
| Claude processing | 3-10s |
| TTS (piper) | 1-3s |
| Telegram upload | <1s |
| **Voice → text reply** | ~5-15s |
| **Voice → voice reply** | ~7-20s |

Acceptable for personal assistant; not target for real-time conversation.

### File lifecycle

- `/tmp/voice_*` and `/tmp/tts_*` auto-deleted after 1h by cron
- Transcribed text preserved in Claude's session history → cross-turn voice context

---

## 9. Scheduling Strategy

### When to use which primitive

| Use case | Primitive | Bucket |
|---|---|---|
| Daily morning brief, weekly memory hygiene, monthly OAuth expiry | `/schedule` Routine (cloud) | Subscription usage |
| "Watch this PR until merged" inside a working session | `/loop` (session-scoped, 7d expiry) | Interactive |
| Channel-session health check | systemd timer + curl | Zero |
| Hourly local job needing files + MCPs | systemd timer + `claude -p --resume <id>` | Agent SDK bucket post-2026-06-15 |

### V1 scheduled jobs

| # | Job | Cadence | Primitive |
|---|---|---|---|
| 1 | Weekday morning brief (weather, calendar, GitHub overnight, news) → Telegram | Weekdays 08:00 IST | RemoteTrigger Routine |
| 2 | Memory consolidation review | Sundays 11:00 IST | RemoteTrigger Routine |
| 3 | OAuth token expiry warning | Monthly | RemoteTrigger Routine |
| 4 | Channel-session health check | Every 10 min | systemd timer + curl |
| 5 | Dashboard cache refresh | Every 5 min | systemd timer + python |
| 6 | Daily usage snapshot to local SQLite | 23:55 IST | systemd timer + `claude -p '/usage'` |
| 7 | Project-lead idle reaper | Every 6h | systemd timer + python |

### Idle reaper logic

```
For each project-lead in registry:
  if last_activity > 24h:
    if first_idle_hit:
      send shutdown_request (graceful)
      archive its ~/.claude/projects/<lead>/memory/ to /opt/hermes-claude/archive/
      remove from registry
      notify user via Telegram: "Hibernated dev-foo-lead. Tell me to revive anytime."
  elif last_activity > 7d:
    hard-delete from registry but preserve memory + cwd
```

---

## 10. Dashboard

### URLs

```
claude.mayankgupta.in
├── /              public landing
└── /admin/*       GitHub OAuth allowlist = ["techfreakworm"]
    ├── /admin                 overview
    ├── /admin/projects        react-flow project tree
    ├── /admin/conversations   Telegram thread browser
    ├── /admin/routines        list + run + delete
    ├── /admin/usage           Recharts burn chart
    ├── /admin/memory          read-only V1
    └── /admin/logs            basic feed V1
```

### Stack

| Layer | Tech |
|---|---|
| Framework | Next.js 16 App Router (TypeScript) |
| Styling | Tailwind v4 + shadcn/ui |
| Charts | Recharts |
| Project graph | react-flow |
| Animations | Framer Motion |
| Live updates | SSE via EventSource |
| Auth | Auth.js v5 + GitHub OAuth + handle allowlist |
| Hosting | Same OCI VPS (Caddy → Node standalone) |

### Backend ↔ Claude bridge

Dashboard calls FastAPI. FastAPI calls `hermes_api` MCP server over a unix socket. The `hermes_api` MCP runs inside the channel session, so it has direct access to Claude's runtime state.

Avoided alternatives:
- File-based hook bridges (fragile)
- Spawning `claude -p` per dashboard action (wasteful, hits Agent SDK bucket)

### Public landing

```
HERO
   "Hermes-Agent's value in 10% the code, by riding Claude Code's native rails."
   [Animated diagram: voice memo on Telegram → Claude → reply]

LIVE STATS (from /api/public/stats — anonymized)
   1,247 messages handled    8 active project-leads
   42 agent decisions today  ~12 hrs uptime today

ARCHITECTURE
   [Static interactive diagram]

THESIS
   • Why I didn't fork Hermes-Agent
   • What Claude Code already does that nobody talks about
   • The new build's surface: 4,000 LOC vs 27,000

DEMO
   [Video]

TECH STACK / SOURCE / NEXT STEPS
```

### Admin page details

- **/admin** — System health header, KPI cards, live SSE activity, quick-action buttons (Pause All, Broadcast, Spawn Project, Sync MEMORY)
- **/admin/projects** — react-flow force graph, orchestrator → project-leads → teammates → subagents, live status colors, click → RC URL
- **/admin/conversations** — Three-pane (thread list / message timeline / metadata)
- **/admin/routines** — List from RemoteTrigger API, "Run now" button per row, delete; create via Telegram only in V1
- **/admin/usage** — Daily burn area chart (Interactive vs Agent SDK buckets), per-project stacked bar, 30-day trend, ceiling projection
- **/admin/memory** — Tree of `~/.claude/projects/*/memory/`, read-only V1
- **/admin/logs** — Simple feed of recent tool calls, filter UI in V1.5

---

## 11. Cost & Risk

### Steady-state burn (estimate)

For Mayank's actual usage pattern (~few voice memos/day, ~1-2 active project-leads at a time, occasional teammate work, 3 daily routines):

- ~30-80 turns/day interactive bucket
- ~1 turn/day Agent SDK bucket (the usage snapshot)
- $0 extra spend beyond existing Max subscription

Max20 quota theoretical ceiling ≈ 4,800 turns/day. Comfortable safety margin.

### Guardrails

1. Dashboard `/admin/usage` alerts at 75% Max ceiling
2. Idle reaper prevents drifting projects
3. `/pause-all` emergency quick action
4. `schedule-routine` caps at 5 routines (delete to add more)
5. Orchestrator caps at 6 concurrent project-leads

### Risks watched

| Risk | Mitigation |
|---|---|
| Channels protocol shifts (research-preview) | Pin Claude Code version; abstract channel-facing logic |
| OAuth token expires (1-year cycle) | Monthly expiry reminder + 60-day pre-expiry alert |
| OCI free-tier eviction | $5/mo Hetzner ARM fallback documented; heartbeat monitor |
| 2026-06-15 Agent SDK bucket smaller than expected | Design uses interactive bucket primarily; daily snapshot monitors |
| Project-lead concurrency overwhelms RAM | 6-project hard cap + reaper |
| Claude Code version bumps break plugin | CI smoke test (V1.5); pin major version |

---

## 12. OSS Positioning (Phase 2)

### README narrative

> **Hermes-Claude** is a Claude Code plugin that gives Claude a body. Telegram for messaging, voice in and voice out, persistent project-leads with their own agent teams, all reachable from your phone — and a dashboard at claude.mayankgupta.in that shows it running live.
>
> It's a self-conscious response to [Hermes-Agent](https://github.com/NousResearch/hermes-agent) by Nous Research — a 27,000-line platform for self-improving messaging agents. After studying it, I asked: *what if your engine is Claude Code? How much do you actually need to build?* The answer turned out to be: ~4,000 lines. Channels, cron, agent teams, memory, MCP, hooks, mobile push, remote control — all native to Claude Code if you know where to look. The plugin fills the last 5%: a voice pipeline, a project orchestrator, a dashboard, and curated workflows.
>
> No API keys. Runs on a single Oracle Cloud free-tier VPS. Authed via Claude Max subscription. Code in this repo, design notes in `docs/`.

### License

MIT — matches hermes-agent's license, lowers OSS adoption friction.

### `marketplace.json` (for V1.5 OSS ship)

```json
{
  "name": "hermes-claude",
  "version": "0.1.0",
  "description": "Hermes-Agent's value in 10% the code — by riding Claude Code's native rails.",
  "author": "Mayank Gupta",
  "repository": "https://github.com/techfreakworm/hermes-claude",
  "homepage": "https://claude.mayankgupta.in",
  "license": "MIT",
  "plugins": [{ "name": "hermes-claude", "path": "." }]
}
```

Adopt-as-user command: `/plugin marketplace add techfreakworm/hermes-claude`

### Demo materials (V1.5)

- 60-sec Loom: voice memo on Telegram → spawn web-scraper project → live in dashboard
- Big architecture PNG embedded in README
- Blog post on mayankgupta.in: "Why I built Hermes-Claude instead of forking Hermes-Agent"
- Pinned X thread

---

## 13. Open Questions & Future Work

### V1.5

- Memory inspector edit-in-place
- Logs filter UI
- Routine creation from dashboard
- Transcript FTS MCP (FTS5 over `~/.claude/projects/*/transcripts/`)
- Slack channel integration (~30 min config)
- GitHub Action for CI deploy

### V2

- All trading MCPs (Zerodha, Dhan, market data) + trading-focused skills
- Custom channels: WhatsApp, Signal, Email, Discord, iMessage
- Always-on content-team
- Always-on personal-team (calendar, email triage)
- HF training watch, paper-track, portfolio-content-drafter skills
- Voice cloning (Coqui XTTS), multi-voice TTS, emotion control
- Sandboxed try-it on landing page
- Multi-language voice support (Hindi TTS via piper Hindi voices)

### Research questions (during V1 implementation)

1. Confirm RemoteTrigger routine credit bucket post-2026-06-15 — Interactive or new Agent SDK?
2. Verify channel-session can use Remote Control simultaneously with `--channels` mode
3. Verify SendMessage works cross-team (from orchestrator to project-lead in different team)
4. Test concurrent teammate spawn under tmux mode on Ampere A1 ARM
5. Confirm `claude --bg` spawned sessions can have their own permission modes independent of parent

---

## 14. Glossary

| Term | Meaning |
|---|---|
| **Orchestrator** | The Telegram channel session — manages projects, no team of its own |
| **Project-lead** | An independent background Claude Code session for one app/workstream |
| **Team** | A `TeamCreate`-instantiated set of named teammates under a project-lead |
| **Teammate** | A named full Claude Code session in a project-lead's team |
| **Subagent** | A transient `Agent` tool dispatch within a session, returns single result |
| **Routine** | A `/schedule`-created cloud-hosted scheduled job (`RemoteTrigger` API) |
| **Channel** | A Claude Code MCP capability that pushes external events into a running session |
| **Remote Control** | Native Claude Code feature making a local session reachable from claude.ai or mobile app |
| **Plugin** | A versioned bundle of skills/agents/hooks/MCP servers loaded via `--plugin-dir` |
| **Interactive bucket** | Max-subscription credit pool funding interactive Claude Code sessions |
| **Agent SDK bucket** | (Post-2026-06-15) Separate credit pool for `claude -p` and SDK programmatic calls |

---

*Spec written 2026-05-22. Implementation plan to follow via `writing-plans` skill.*
