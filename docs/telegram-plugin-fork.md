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
  `external/claude-plugins-official`, pinned to the exact commit `0df2472` (which
  lives on the fork's `fix/reply-to-context` branch). `.gitmodules` records no
  `branch=`, so `submodule update` checks out that fixed SHA, NOT the moving
  branch head — a future force-push to the branch cannot silently drift deploys.
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
- `handleInbound` prepends that block to the channel `content` and adds
  `meta.reply_to_message_id` for threading.

The patch is fail-safe: no `reply_to_message` -> behaviour is byte-for-byte the
old behaviour (`content = text`, no extra meta).

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

## Verify

Static (the on-disk plugin is the patched one):

```bash
grep -c reply_to_message_id /opt/claude-soma/external/claude-plugins-official/external_plugins/telegram/server.ts
# expect: >= 1
```

Live (the running channel actually relays the quote): from Telegram, reply/quote
an earlier message and send something new. The bot should now see the quoted text
(it can reference what you replied to). Before the fix it only saw the new text.

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
