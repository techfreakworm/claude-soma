# Telegram plugin: vendored fork + reply-to context

## Why this exists

The Telegram channel runs the upstream `telegram` plugin from
`anthropics/claude-plugins-official`. That plugin dropped the **quoted message**
on a reply: when a user quote-replies to an earlier message, Telegram delivers
only the *new* text in `message.text` and puts the quoted message in
`message.reply_to_message`. The upstream `handleInbound` only forwarded
`message.text`, so the session never saw the context the user was responding to.

To fix it in a way that stays upstream-mergeable, we **fork-and-own**:

- Fork: `https://github.com/techfreakworm/claude-plugins-official` (a real fork of
  `anthropics/claude-plugins-official`).
- The fork is vendored into this repo as a **git submodule** at
  `external/claude-plugins-official`, pinned to the exact commit `a822c13` (which
  lives on the fork's `fix/reply-to-context` branch). `.gitmodules` records no
  `branch=`, so `submodule update` checks out that fixed SHA, NOT the moving
  branch head — a force-push to the branch cannot silently drift deploys (a
  redeploy moves only when the pinned SHA in this repo is bumped).
- The channel loads the telegram plugin **from the submodule** (not from the
  upstream GitHub marketplace), so our patch is what runs.

## The patch

One isolated, cherry-pickable commit on the fork branch `fix/reply-to-context`
(`external_plugins/telegram/server.ts`):

- A `replyContext(ctx)` helper that, when `message.reply_to_message` is present,
  builds a delimited block `[in reply to @user (message N): "..."]`. The quoted
  body is scrubbed of the `<>[];` + newline tag-delimiter chars (same scrub as
  the existing `safeName`, because it lands inside the `<channel>` notification)
  and capped at 2000 chars. A quoted message that carried media but no text is
  noted as `[photo]`/`[document]`/etc.
- `handleInbound` prepends that block to the channel `content`. The quoted
  context travels in `content` ONLY — there is intentionally **no** extra
  notification meta key (see the gotcha below).

The patch is fail-safe: no `reply_to_message` -> behaviour is byte-for-byte the
old behaviour (`content = text`).

## Gotcha: never add a `reply_to_message_id` (or other reply) meta key

The first cut (`0df2472`, deployed 2026-06-15) also set
`meta.reply_to_message_id` for threading. That **silently dropped every
quote-reply**: Claude Code's channel layer discards an inbound
`notifications/claude/channel` notification that carries `reply_to_message_id`
(it treats the reply as a continuation it must correlate to a tracked outbound
message and drops it on a miss). Replies to the bot's own messages vanished
entirely; plain messages were unaffected. Confirmed via the CC-side
received-notifications debug log
(`~/.cache/claude-cli-nodejs/-opt-claude-soma/mcp-logs-plugin-telegram-telegram/`):
no reply-formatted notification ever appeared there. The fix (`a822c13`) carries
the quoted context in `content` only and adds no meta key — so a reply is
ingested exactly like any normal message. **Do not re-introduce a reply meta
key** without first proving CC accepts it on a live channel.

## Load-path wiring (how the channel resolves our copy)

The channel already loads claude-soma's own plugin via `--plugin-dir /opt/claude-soma`
(`scripts/channel-claude.sh`), which consumes `.claude-plugin/marketplace.json`.
We added the telegram plugin to **that** marketplace, pointing at the submodule:

- `.claude-plugin/marketplace.json` -> `{ "name": "telegram", "path": "external/claude-plugins-official/external_plugins/telegram" }`
- `config/claude/channel-settings.json` -> `enabledPlugins."telegram@claude-soma": true`
- `scripts/channel-claude.sh` -> `--channels plugin:telegram@claude-soma`

So the reference is `telegram@claude-soma`, NOT `telegram@claude-plugins-official`.
This is deliberate:

- It uses the **existing, trusted** `--plugin-dir` mechanism — no global
  `~/.claude/plugins` marketplace surgery (no `marketplace add/remove`).
- The different marketplace name avoids the version cache: the old
  `claude-plugins-official/telegram/0.0.6` cache entry can never be reused for a
  `@claude-soma` reference, so a stale cached copy can't shadow the patch.
- Leads never receive `--plugin-dir /opt/claude-soma` or `channel-settings.json`,
  so they still cannot load (and hijack) the telegram poller — the isolation from
  docs/KNOWN_BUGS.md #1 is preserved.

The plugin's MCP server boots via its `.mcp.json`:
`bun run --cwd ${CLAUDE_PLUGIN_ROOT} ... start`, and `start` is
`bun install --no-summary && bun server.ts` — so it **self-installs** its
`node_modules` on first boot from the submodule path. No committed `node_modules`.

## Deploy

```bash
git -C /opt/claude-soma pull --ff-only
git -C /opt/claude-soma submodule update --init --recursive
```

`submodule update` checks out the pinned fork commit at
`/opt/claude-soma/external/claude-plugins-official`. It is a no-op when the
pointer has not moved.

## Activate (operator-gated restart)

A moved telegram submodule only takes effect when the channel restarts:

```bash
sudo systemctl restart claude-soma-channel.service
```

This is **operator-gated** — the bot cannot restart itself. On restart the plugin
runs `bun install` from the submodule then `bun server.ts` (our patched copy).

## Verify (REQUIRED — this is a supervised redeploy; test before trusting)

Static (the on-disk plugin is the patched, meta-less copy):

```bash
grep -c 'in reply to' /opt/claude-soma/external/claude-plugins-official/external_plugins/telegram/server.ts   # expect >= 1
grep -c 'reply_to_message_id' /opt/claude-soma/external/claude-plugins-official/external_plugins/telegram/server.ts  # expect 1 (comment only, never in the emitted meta)
```

Live — do this IMMEDIATELY after the restart, with the rollback below armed:
quote-reply to one of the **bot's own** messages and send new text. Then confirm
the channel actually ingested it (objective check — a reply that was dropped will
NOT appear here):

```bash
ls -t ~/.cache/claude-cli-nodejs/-opt-claude-soma/mcp-logs-plugin-telegram-telegram/*.jsonl | head -1 \
  | xargs grep -a 'in reply to' | tail -3   # expect your quoted reply, proving CC accepted it
```

If that grep stays empty after a quote-reply, the reply is still being dropped —
roll back immediately (below).

## Rollback

If anything misbehaves after the restart, revert the load-path to the upstream
marketplace and restart:

1. `git -C /opt/claude-soma revert <soma-side commit>` (restores
   `channel-settings.json` + `channel-claude.sh` to `telegram@claude-plugins-official`
   and drops the marketplace telegram entry), then
   `git -C /opt/claude-soma pull --ff-only` on the next deploy; OR for an immediate
   manual rollback, edit those two files back to `telegram@claude-plugins-official`.
2. `sudo systemctl restart claude-soma-channel.service`.

The upstream `telegram@claude-plugins-official` plugin is still installed in the
plugin cache, so rollback needs no network.

## Upstream PR / staying mergeable

The reply-to fix is a single clean commit on `fix/reply-to-context` so it can be
proposed upstream. Our fork's PAT is read-only on `anthropics`, so the PR is
opened from the GitHub UI via the compare URL:

```
https://github.com/anthropics/claude-plugins-official/compare/main...techfreakworm:fix/reply-to-context?expand=1
```

To pull future upstream changes into the fork: `git fetch upstream && git rebase
upstream/main fix/reply-to-context` (the telegram plugin rarely changes — at fork
time it was byte-identical across 653 upstream commits), then bump the submodule
pointer here and redeploy. If/when upstream merges the fix, we can drop the fork
divergence and point the submodule back at a tagged upstream release.
