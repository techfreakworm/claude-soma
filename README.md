# Claude Soma

> A body for Claude. Hermes-Agent's value in ~10% the code, by riding Claude Code's native rails.

**[claude.mayankgupta.in](https://claude.mayankgupta.in)** · MIT · By [Mayank Gupta](https://mayankgupta.in)

Claude Soma (Greek *soma*: "body") wraps Claude Code with a Telegram channel, voice in/out, a project orchestrator that spawns persistent independent agent teams per workstream, and a showcase dashboard. Authed via Claude Max subscription only — **no Anthropic API keys**. Runs on a single Oracle Cloud Ubuntu ARM free-tier VPS.

---

## The thesis

[Hermes-Agent](https://github.com/NousResearch/hermes-agent) by Nous Research is a remarkable 27,000-line platform for self-improving messaging agents: channels, cron, skills, memory curation, sandbox backends, trajectory tooling. After studying it, the question this project answers is:

> What if your engine is Claude Code? How much do you actually need to build?

The answer is **~4,000 lines.** Channels (Telegram, Discord, iMessage, custom), agent teams, server-hosted scheduled routines, Remote Control, mobile push, MCP, hooks, plugins, and auto-memory are all native to Claude Code if you know where to look. The platform layer collapses. Hermes-Claude — sorry, *Claude Soma* — is the missing 5%: a voice pipeline, a project orchestrator, a dashboard, and curated workflows.

---

## What you do with it

```
You (phone, Telegram)              Claude Soma                    Outcomes
────────────────────              ──────────────                  ────────
"What am I working                 channel → portfolio-status     Voice or text reply
 on?"  (voice memo)                skill                          listing repos + active
                                                                  project-leads

"Build me a scraper                channel → spawn-project        Background claude --bg
 for the F1 standings              skill → orchestrator MCP →     session "f1-scraper-lead"
 that tweets on change"            claude --bg + TeamCreate       owns its own cwd, its
                                                                  own team (db engineer,
                                                                  playwright engineer,
                                                                  backend engineer), its
                                                                  own Remote Control URL.

"Tell f1-scraper-lead              channel → message-project →    SendMessage forwarded
 to use httpx not                  SendMessage(to: <agentId>)     into the project-lead's
 requests"                                                        session.

"Draw me a diagram of              channel → codex-image-gen      Image generated via
 the system architecture"          → shells out to Codex CLI      Codex CLI (consumes
                                                                  your ChatGPT sub, not
                                                                  your Max sub) and
                                                                  returned as a Telegram
                                                                  photo.

"Every weekday at 8am IST,         channel → schedule-routine     Cloud-hosted routine
 send me a morning brief"          → RemoteTrigger.create()       fires on Anthropic
                                                                  infrastructure — no
                                                                  local machine needed
                                                                  while you sleep.
```

You can also attach to any project-lead directly from your phone (Claude mobile app) or laptop (claude.ai/code) via its Remote Control URL, chat with it, watch its tool calls live, then go back to talking to the orchestrator on Telegram. **Three control surfaces, one Max account.**

---

## Architecture

```
                              EXTERNAL WORLD
              ┌────────────────────────────────────────────────┐
              │  Telegram (V1)                                  │
              │  Discord / iMessage / WhatsApp / Signal / Email │
              │  / Slack — V2                                   │
              └────────────────────────┬───────────────────────┘
                                       │
                                       ▼
              ┌────────────────────────────────────────────────┐
              │  Oracle Cloud Ubuntu ARM VPS (4 vCPU / 24 GB)   │
              │                                                 │
              │  systemd: claude-soma-channel.service           │
              │  └─ tmux: claude --channels plugin:telegram@... │
              │              --plugin-dir /opt/claude-soma      │
              │  (authed via CLAUDE_CODE_OAUTH_TOKEN — Max)     │
              │                                                 │
              │  /opt/claude-soma/.mcp.json wires:              │
              │  ├─ voice-stt    (whisper.cpp ARM)              │
              │  ├─ voice-tts    (piper ARM → ffmpeg → opus)    │
              │  ├─ project-orchestrator  (spawns project-leads)│
              │  └─ hermes-api   (state bridge to dashboard)    │
              │                                                 │
              │  systemd: claude-soma-{api,frontend}.service    │
              │  └─ Caddy :443 → /api/* → :9000 (FastAPI)       │
              │             → / → :3000 (Next.js standalone)    │
              └────────────────────────────────────────────────┘

         ┌──────────────────────────────────────────────────────┐
         │  ANTHROPIC-HOSTED (drawn from Max plan, no machine)   │
         │   • /schedule Routines — cloud cron                   │
         │   • Claude on Web — for V2 cloud sessions             │
         │   • Mobile push via PushNotification + Remote Control │
         └──────────────────────────────────────────────────────┘
```

The Telegram channel session is an **orchestrator**, not a team-lead. It spawns multiple independent project-leads as `claude --bg` background sessions, each in its own cwd, each running its own agent team via `TeamCreate`. This sidesteps Claude Code's "one team per lead / no nested teams" constraint while still giving you per-project specialization.

Full design: [`docs/superpowers/specs/2026-05-22-hermes-claude-design.md`](docs/superpowers/specs/2026-05-22-hermes-claude-design.md).
Full implementation plan (49 tasks, ~4 weeks): [`docs/superpowers/plans/2026-05-22-hermes-claude-v1.md`](docs/superpowers/plans/2026-05-22-hermes-claude-v1.md).

---

## Status

**In development.** This is a personal-use-first build that will become a portfolio showcase once V1 is stable.

| Phase | State |
|---|---|
| Spec + plan | ✅ Done |
| **Week 1 — Plugin skeleton + voice STT + Telegram channel + hooks + systemd** | ✅ Code complete (`week-1-code-complete` tag) |
| **Week 2 — Voice TTS + Codex skill + project orchestrator + 11 skills + 1 agent + 5 templates** | ✅ Code complete (`week-2-code-complete` tag) |
| Week 3 — Dashboard (FastAPI + Next.js + Auth.js + 7 admin pages + landing) | ⏳ Pending |
| Week 4 — Setup wizard + reaper + scheduled jobs + README polish + marketplace publish | ⏳ Pending |
| **User-action: OCI VPS provisioning + OAuth tokens + Telegram bot pairing + systemd installs** | ⏳ Pending |

What's runnable today: every test passes locally (skipping voice tests where whisper/piper aren't installed). The Telegram bridge will come online as soon as the user-action checklist in [`NEXT.md`](NEXT.md) is executed.

---

## Repository layout

```
claude-soma/
├─ .claude-plugin/                  Claude Code plugin metadata
│   └─ plugin.json
├─ .mcp.json                        wires voice-stt + voice-tts + project-orchestrator
├─ pyproject.toml                   Python package config (claude-soma 0.1.0)
│
├─ src/claude_soma/
│   ├─ mcp_servers/
│   │   ├─ voice_stt/server.py      whisper.cpp wrapper, 60s/300s timeouts
│   │   ├─ voice_tts/server.py      piper + ffmpeg → opus, 60s timeouts
│   │   ├─ project_orchestrator/    Registry + spawner + templates + server
│   │   └─ hermes_api/              (stub; impl lands in Week 3)
│   ├─ api/                         (stub; Week 3 FastAPI lands here)
│   └─ wizard/                      (stub; Week 4 setup wizard lands here)
│
├─ skills/                          11 Claude Code skills
│   ├─ codex-image-gen              delegates image-gen to Codex CLI
│   ├─ respond-with-voice           TTS reply path
│   ├─ voice-action                 voice-intent routing
│   ├─ spawn-project, list-projects, message-project,
│   │   kill-project, project-status      orchestration suite
│   ├─ schedule-routine             wraps RemoteTrigger API
│   ├─ portfolio-status             "what am I working on" summary
│   └─ usage-report                 surface /usage in chat
│
├─ agents/
│   └─ project-lead.md              subagent template for spawned project-leads
│
├─ templates/projects/              5 project templates
│   └─ web-scraper.json, llm-app.json, server-app.json,
│      agentic-coding.json, custom.json
│
├─ hooks/hooks.json                 SessionStart + UserPromptSubmit + PostToolUse
├─ scripts/
│   ├─ voice_intake.sh              env-var path passing (no Python injection)
│   ├─ session_start_context.sh     loads active projects + recent commits
│   ├─ log_activity.sh              fire-and-forget activity logger
│   └─ deploy.sh                    rsync to OCI + venv bootstrap
├─ systemd/
│   └─ claude-soma-channel.service  tmux-wrapped persistent claude session
│
├─ tests/                           29 tests across 4 MCP servers
│
└─ docs/superpowers/
    ├─ specs/2026-05-22-hermes-claude-design.md      design (frozen)
    └─ plans/2026-05-22-hermes-claude-v1.md          49-task implementation plan
```

---

## Quick install (when V1 is shipped)

The setup wizard isn't implemented yet (Week 4). Until then, follow [`NEXT.md`](NEXT.md) for the manual checklist.

Once `hermes-init` lands:

```bash
# On a fresh Ubuntu ARM VPS
sudo apt install -y git python3.12 python3.12-venv
git clone https://github.com/techfreakworm/claude-soma.git /opt/claude-soma
cd /opt/claude-soma
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
sudo $(which soma-init)   # interactive wizard
```

---

## What you'll need

| Requirement | Why |
|---|---|
| Claude Max subscription | The engine. The whole project is designed around Max-OAuth auth. |
| Codex CLI subscription (optional but recommended) | Image generation delegates here so it doesn't burn Max credits. |
| Oracle Cloud account (or any Linux ARM/x86 VPS) | Always-on host. OCI free tier is sized for this. |
| A Telegram account + Telegram bot via @BotFather | Messaging surface. |
| GitHub OAuth app | Single-user auth gate for the dashboard. |
| A domain (e.g. `claude.yourdomain.com`) | Public dashboard URL with auto-HTTPS via Caddy. |

---

## Contributing

Personal-use-first; not actively soliciting contributions during V1. After V1.5 polish, contributions welcome — see the [implementation plan](docs/superpowers/plans/2026-05-22-hermes-claude-v1.md) for the roadmap.

If you're forking for your own use: the entire project is MIT-licensed.

---

## Acknowledgments

- [Nous Research](https://nousresearch.com) for [Hermes-Agent](https://github.com/NousResearch/hermes-agent) — the inspiration, and the comparison point that made the "ride the native rails" thesis crisp.
- [Anthropic](https://www.anthropic.com) for the platform this rides on.
- The [Superpowers](https://github.com/anthropics/claude-plugins-official) skill suite for the spec → plan → subagent-driven-development workflow that built it.

---

## License

MIT — see [LICENSE](LICENSE) (lands in Week 4 along with the marketplace publish).
