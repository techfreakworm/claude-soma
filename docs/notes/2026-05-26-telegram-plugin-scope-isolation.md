# Telegram plugin scope isolation: the root fix for the poller hijack

Date: 2026-05-26
Status: implemented on branch fix/telegram-poller-hijack-hardening; deploy needs a
maintenance window (see below)

## Problem recap

The telegram@claude-plugins-official plugin loads in ANY claude session that finds it
enabled in a settings scope the session loads. It was enabled in USER scope
(~/.claude/settings.json -> enabledPlugins), so every session the ubuntu user runs --
the bot, manual shells, AND Agent/Task subagents -- loaded it, and each new load SIGTERMs
the live bot's poller (docs/KNOWN_BUGS.md #1). The shipped wrapper (scripts/claude-safe.sh)
covers manual shells, but the bot's own Agent/Task subagents are not launched by us, so we
cannot add `--setting-sources` to them. We needed a fix that does not depend on controlling
each subagent's argv.

## Key facts established by isolated probing (2026-05-26)

All probes ran with a fake TELEGRAM_BOT_TOKEN + a throwaway TELEGRAM_STATE_DIR + a throwaway
`tmux -L` socket, so they could not touch the live bot. The live bot.pid (88627) was
unchanged before/after every probe.

1. `claude --channels plugin:telegram@... --setting-sources project,local` (telegram NOT in
   the loaded scopes) prints the "Listening for channel messages" banner but does NOT boot
   the plugin -- no bun, no bot.pid, no mcp-log. So `--channels` is routing only; the poller
   boots only when telegram is enabled in a LOADED scope.
2. `claude --channels plugin:telegram@... --setting-sources project,local --settings F`
   where F = {"enabledPlugins":{"telegram@claude-plugins-official":true}} DOES boot the
   plugin (bun spawns, writes bot.pid). So `--settings` is a valid bot-only opt-in.
3. With DEFAULT --setting-sources (user scope, which really has telegram) plus
   `--settings <file WITHOUT telegram>`, telegram still loads. So `--settings` MERGES
   additively with the scope chain -- it does not replace it. The bot therefore keeps its
   other user-scope settings (skipDangerousModePermissionPrompt, extraKnownMarketplaces,
   theme, ...) while gaining telegram from the --settings file.

## The fix

Stop enabling telegram in any scope a non-bot session loads; have the bot opt in explicitly.

1. Remove `telegram@claude-plugins-official` from user-scope enabledPlugins
   (scripts/disable-user-telegram-plugin.sh -- idempotent, backs up settings.json, removes
   only that one key). After this, no session loads the plugin from the default
   user/project/local chain.
2. The bot re-acquires the plugin by adding
   `--settings /opt/claude-soma/config/claude/channel-settings.json`
   (= telegram-only enabledPlugins) to its launch. The argv lives in
   scripts/channel-claude.sh (single source for the service ExecStart + the healthcheck
   in-pane respawn).

### Why this covers subagents (the previously-uncovered vector)

A subagent loads either (a) the default user/project/local scopes, or (b) whatever scope it
inherits from the bot. In both, telegram is now absent:
- user scope: cleaned by step 1;
- project/local of any cwd: never had telegram;
- the bot's --settings file: a per-session flag the bot passes; subagents are not launched
  with it (see residual below).

It also makes the manual-shell wrapper a belt-and-suspenders defense rather than the sole
guard, and removes the need to reason about whether subagents inherit `--setting-sources`.

## Residual to verify in the maintenance window

The one way a subagent could still load telegram is if Claude Code propagates the parent's
`--settings` flag to Agent/Task subagents. `--settings` is a "for this session" flag, so it
most likely is NOT inherited -- but this must be confirmed, because if it is, subagents
would read channel-settings.json and re-introduce the hijack.

Deploy + verify, in one window (the bot will restart, so coordinate -- a restart is what we
otherwise avoid):

1. Deploy the branch to /opt/claude-soma (scripts/, config/, systemd/).
2. Run `scripts/disable-user-telegram-plugin.sh`.
3. `sudo systemctl restart claude-soma-channel.service`.
4. VERIFY THE BOT STILL POLLS: a bun server.ts is a child of the bot's claude, bot.pid points
   to it, and a Telegram DM gets a reply. (Confirms --settings loaded the plugin and merged
   with user scope, i.e. no dangerous-mode hang.)
5. VERIFY THE SUBAGENT VECTOR IS CLOSED: from the bot, dispatch one trivial background Agent
   and watch for ~30s. Expected: NO second bun server.ts appears and bot.pid stays the bot's.
   If a second bun appears (subagent inherited --settings), STOP -- pivot to a mechanism that
   is provably not inherited (e.g. route heavy work to orchestrator leads via
   system_prompts/responsive_bot.md, which are already plugin-skipped + cgroup-isolated).

Rollback: restore the settings.json.bak.* that step 2 created and restart the channel.

## Relationship to the other shipped pieces

- scripts/claude-safe.sh (manual-shell wrapper): kept as defense-in-depth.
- scripts/channel-claude.sh + healthcheck in-pane respawn: independent; makes a missing-bun
  recovery non-destructive.
- fix/project-lead-cgroup-isolation (separate branch): blast-radius mitigation if any hijack
  still slips through.
