# Project-leads inherit all MCPs + plugins EXCEPT telegram

2026-05-26. Leads used to spawn with `--setting-sources project,local`, which
excluded user scope (so they lacked `sequential-thinking` and user
skills/plugins) and the bot's `/opt/claude-soma/.mcp.json` (so they lacked
playwright etc.). Every lead needed hand-wired per-cwd `.mcp.json` bridges.
This is the systemic fix. (Pairs with the shared-playwright-auth work — see
[2026-05-26-shared-playwright-auth.md](2026-05-26-shared-playwright-auth.md).)

## Why it's now safe to load user scope

The original `project,local` was a telegram-poller-hijack mitigation (telegram
was a user-scope plugin; a lead loading user scope would start a second poller
and steal the bot's slot). That root cause is gone: telegram was moved OUT of
user scope — the bot opts in ONLY via `--settings .../channel-settings.json`
(`{"enabledPlugins": {"telegram@claude-plugins-official": true}}`), which leads
never receive. Verified 2026-05-26: neither `~/.claude.json` nor
`~/.claude/settings.json` has telegram in any `enabledPlugins`; `/home/ubuntu`
even lists it in `disabledMcpServers`.

## The two changes (spawner.py)

1. `--setting-sources` `project,local` → **`user,project,local`**. Leads now
   inherit user-scope settings, skills, agents, and plugins. Telegram does not
   load (not enabled in user scope; leads don't get the bot's `--settings`).
2. `--mcp-config <curated lead-mcp.json>` injects the bot's MCP **tool** servers
   (it's additive and independent of `--setting-sources`). Omitted if the file
   is absent (pre-deploy/CI) so spawn never fails over it.

## What leads get vs. don't

`config/claude/lead-mcp.json` = the bot's `.mcp.json` tool servers + an explicit
`sequential-thinking`, deliberately EXCLUDING two control-plane servers:

| Lead gets (via lead-mcp.json) | Excluded — why |
|---|---|
| playwright, -x, -x-article, -linkedin, -medium | **hermes-api** — `serve_blocking` does `unlink()`+bind on the FIXED `/tmp/claude-soma-api.sock`, so a lead instance would STEAL the bot's dashboard socket (a hijack-class bug). |
| voice-stt, voice-tts | **project-orchestrator** — shares `/opt/.../registry.sqlite` and would let a lead recursively spawn/kill leads + contend on the registry. |
| sequential-thinking (added explicitly) | **telegram** — the one mandated exclusion; bot-only via `--settings`. |

`sequential-thinking` is added to lead-mcp.json explicitly rather than relied on
from user scope: loading the top-level `~/.claude.json` mcpServers proved
cwd/timing-flaky under an explicit `--setting-sources` from an arbitrary cwd, so
`--mcp-config` guarantees it. A drift-guard test asserts lead-mcp.json stays =
`.mcp.json − {hermes-api, project-orchestrator}` + sequential-thinking.

Leads also keep the auth-driven claude.ai connectors (Canva/Gmail/GCal/GDrive)
via `~/.claude/.credentials.json` as before.

## Verified live (acceptance)

A `claude -p` run with the exact lead flags from a CLEAN empty cwd loaded:
`playwright (+4 variants), voice-stt, voice-tts, sequential-thinking,
claude_ai_{Canva,Gmail,Google_Calendar,Google_Drive}` — and **NOT** hermes-api,
project-orchestrator, or telegram. A real throwaway lead spawned through the
updated spawner did **not** start a telegram poller: `bun server.ts` count
unchanged across the spawn, channel unit untouched (the hijack guard).

## Reconciliation with shared-playwright-auth

The 5 playwright stanzas in lead-mcp.json carry the same
`--storage-state ~/.claude-pw/state-<platform>.json` the bot uses, so **leads
and the bot share one auth store**. Keeping that store logged-in (codified VNC
login + weekly refresh) is the shared-playwright-auth work.

## Operator / cleanup

- **Deploy needed:** the spawner change affects NEW spawns after the
  orchestrator/channel is redeployed+restarted; `lead-mcp.json` must be deployed
  to `/opt/claude-soma/config/claude/lead-mcp.json`. Do NOT restart the channel
  from here.
- **Now redundant** (safe to remove once deployed): the per-lead bridges
  `social-manager/.mcp.json`, `wan-manager/.mcp.json`,
  `mayank-portfolio/.mcp.json`, and the `.claude` symlinks.
- **To get user-scope plugins (superpowers, social-publish) in leads:** enable
  them in user scope (`~/.claude/settings.json` `enabledPlugins`) — currently
  empty, so they're not inherited yet. Operator step; affects the bot on
  restart too.
