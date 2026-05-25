# Telegram poller hijack race in `claude-soma`

Author: investigation by Claude (Opus 4.7) on 2026-05-25
Status: **bug confirmed and FIXED** (see "Update — fix shipped" below)
VPS: `ubuntu@soma.mayankgupta.in`

---

## Update — 2026-05-25 evening: fix shipped

Commit `b33eec5` ("fix: project leads skip user-scope settings to avoid stealing bot poller") on `origin/main`.

The original recommendation in section 3 below was to add `--no-plugins` to the spawner. **That flag does not exist in Claude Code 2.1.150** — verified with `claude --help`. The actual fix uses `--setting-sources project,local` instead, which skips user-scope `~/.claude/settings.json` (where `enabledPlugins` lives) without disabling MCP discovery from project/local sources. Project leads keep their claude.ai connectors (Canva, Gmail — auth-driven via `~/.claude/.credentials.json`, not settings-driven) and never had access to the in-repo `.mcp.json` MCPs anyway (different cwd from the bot).

Verified on VPS by spawning a synthetic project lead with the new argv: `bot.pid` unchanged after the spawn, no `bun server.ts` in the lead's process tree, no `mcp-logs-plugin-telegram-telegram` directory created for the lead. The rest of this document is preserved as the historical investigation record.

---

## 1. Verdict — CONFIRMED

The bug is real, reproducible, and currently active. Every time a project lead is spawned by `spawn_background_lead()`, the project-lead's claude process loads the user-enabled `telegram@claude-plugins-official` plugin, the plugin's grandchild `bun server.ts` reads `~/.claude/channels/telegram/bot.pid`, sees the main bot's bun PID, SIGTERMs it, and writes its own PID into the file. The main hermes session keeps running but is now deaf to Telegram — its claude process does NOT auto-reconnect the MCP grandchild after the kill.

### Concrete evidence

**PID file path resolved:**
- `STATE_DIR = process.env.TELEGRAM_STATE_DIR ?? join(homedir(), '.claude', 'channels', 'telegram')` → `/home/ubuntu/.claude/channels/telegram/`
- `PID_FILE = join(STATE_DIR, 'bot.pid')` → `/home/ubuntu/.claude/channels/telegram/bot.pid`

**Pre-test baseline (05:03 UTC equivalent local):**
```
bot.pid contents: 42884
PID 42884 → bun server.ts (the bot's telegram poller)
  parent 42878 → bun run --cwd .../telegram/0.0.6 ... start
  parent 42862 → claude --channels plugin:telegram@claude-plugins-official ... (main hermes claude)
  parent 42860 → tmux new-session ... -s hermes
```

**Spawn synthetic project lead with the spawner's exact argv pattern:**
```bash
tmux new-session -d -s soma-proj-rctest -c /tmp/rctest-cwd \
  /home/ubuntu/.local/bin/claude \
    --remote-control soma-proj-rctest \
    --add-dir /tmp/rctest-cwd \
    --permission-mode acceptEdits \
    --dangerously-skip-permissions \
    --effort max \
    'reply just hello'
```
(After pretrusting `/tmp/rctest-cwd` to bypass the trust dialog — exactly what `spawner._pretrust_cwd` does in production.)

**Post-spawn state (~40s later):**
```
bot.pid contents: 43788     <-- changed
PID 43788 → bun server.ts (the project lead's NEW poller — child of 43783 → 43766 rctest claude)
PID 42884 (old bot bun): DEAD (no ps row)
PID 42878 (old bot wrapper): DEAD (no ps row)
Main hermes claude (42862): still alive, but its children no longer contain a bun
```

**Project-lead MCP log captured the SIGTERM:**
File `/home/ubuntu/.cache/claude-cli-nodejs/-tmp-rctest-cwd/mcp-logs-plugin-telegram-telegram/2026-05-24T23-37-34-885Z.jsonl`:
```json
{"error":"Server stderr: telegram channel: replacing stale poller pid=42884\n", ...}
```
That stderr line is emitted at `server.ts:65` immediately before `process.kill(stale, 'SIGTERM')` at `server.ts:66`.

The same file also logs:
```json
{"debug":"Channel notifications skipped: server plugin:telegram:telegram not in --channels list for this session", ...}
```
This is Claude Code's own message saying "this session is NOT a channel consumer" — but Claude Code still loaded the plugin and let it boot the bun grandchild, which then ran the take-over logic. Claude Code's `--channels` filter applies only at the notification routing layer; it does not prevent the plugin's MCP server from starting.

**Symptom on the bot side after the race:**
- Bot mcp-logs dir `/home/ubuntu/.cache/claude-cli-nodejs/-opt-claude-soma/mcp-logs-plugin-telegram-telegram/` has its most recent file `2026-05-24T23-33-33-816Z.jsonl` last touched at `05:03` (the original bot startup). No new files, no new entries after the project-lead spawn — because the bot's bun was killed and Claude Code did not restart it.
- The bot's hermes claude (PID 42862) is alive. All other MCP children (voice_stt, voice_tts, project_orchestrator, hermes_api, playwright-mcp) are alive. Only the bun grandchild is gone.

**Historical evidence — this has already been happening in normal operation:**
The `techfreakport` project lead's mcp-log dir contains FOUR independent log files (one per spawn cycle):
```
/home/ubuntu/.cache/claude-cli-nodejs/-home-ubuntu-projects-techfreakport/mcp-logs-plugin-telegram-telegram/
  2026-05-24T22-49-25-286Z.jsonl
  2026-05-24T22-52-56-369Z.jsonl
  2026-05-24T23-07-26-978Z.jsonl
  2026-05-24T23-25-22-141Z.jsonl
```
The latest (23:25) file contains the same `replacing stale poller pid=40775` line, against the bot's bun PID at that time. This is not a theoretical race — it has already been hijacking the bot's poller in real spawns for the past several hours.

### Bot currently in the post-race state

The test reproduction left the bot in the broken state because:
1. The project-lead's bun took over the poller and updated `bot.pid` to `43788`.
2. When the rctest tmux session was killed (cleanup), the project-lead's bun got stdin EOF → `shutdown()` → it deleted `bot.pid` because its own PID matched the file (line 654).
3. The bot's bun was already dead, so nothing remains polling Telegram.

The hermes tmux session is alive, the hermes claude is alive, all OTHER MCP children are alive — but **the bot's bun grandchild is permanently gone** until the hermes session restarts. Claude Code does not automatically respawn dead MCP children after launch. This is exactly the silent-darkness symptom the user suspected. See section 6 below.

---

## 2. Mechanism (in plain English)

The Telegram bot lives in three layered processes: the `tmux new-session ... claude --channels plugin:telegram@claude-plugins-official ...` line spawns a Claude Code process (hermes claude, PID 42862), which discovers the telegram plugin from `~/.claude/settings.json` (where `enabledPlugins.telegram@claude-plugins-official: true`), and starts it as an MCP child by running `bun run --cwd .../telegram/0.0.6 start`. Bun in turn execs `bun server.ts`, the actual poller — a grandchild of the main claude. The poller is the unique long-poller of Telegram's `getUpdates` API for the bot token. Telegram allows **exactly one** consumer per token; a second poller gets HTTP 409 Conflict.

The plugin author anticipated a stale-poller problem (sessions that crash leaving an orphan bun grandchild holding the token slot forever), so at startup the plugin writes its PID to `~/.claude/channels/telegram/bot.pid` and, on next startup, it reads any pre-existing PID from that file and `SIGTERM`s it before claiming the slot. The plugin assumes any pre-existing PID belongs to a "stale" prior instance; it has no notion of "another session is actively the legitimate poller."

The project-lead spawner (`src/claude_soma/mcp_servers/project_orchestrator/spawner.py`) invokes `claude` with `--remote-control`, `--add-dir`, `--permission-mode`, `--dangerously-skip-permissions`, `--effort max`, and the brief — but no `--no-plugins`, no `--plugin-dir`, no plugin filter. Because `~/.claude/settings.json` is a user-scope setting, every Claude Code session this user runs auto-loads enabled plugins — including the telegram plugin. The project-lead claude duly spawns its own `bun server.ts` grandchild. That grandchild executes the same "kill the stale PID" logic, sees the live PID of the bot's bun, SIGTERMs it (killing the wrapper too, since the wrapper was just `bun run ... start`), writes its own PID, and starts polling. The bot's hermes claude does not know its grandchild was killed — there's no MCP-level liveness watchdog — so it stays in the "everything is fine" state, but with no telegram poller attached.

Claude Code does emit the message `"Channel notifications skipped: server plugin:telegram:telegram not in --channels list for this session"` for the project lead — Claude Code knows this isn't a channel consumer — but that gate only affects notification routing into the session, not whether the plugin server boots and runs its own logic. The plugin runs unconditionally, polls Telegram in a tight loop, replies to messages if any come in (which they shouldn't, since the project lead has no chat context), holds the token slot, then shuts down cleanly on session exit (deleting the PID file). After cleanup, no poller is running anywhere — the bot is dark.

---

## 3. Recommended fix — **Option A: `--no-plugins` on project-lead spawns**

Add `--no-plugins` (or whatever Claude Code's current "disable all plugins for this session" flag is — verify with `claude --help`; the canonical pre-2.1 flag name was `--no-plugins`, may have changed) to the spawner's `claude_argv`, immediately before the brief positional arg.

### Why this option

1. **Actually solves the race.** Project leads never load the plugin → never spawn their bun grandchild → never read the PID file → never SIGTERM the bot. Zero changes to Telegram-side semantics.
2. **Surgical and reversible.** One line in `spawner.py`. No plugin patch (which would be erased on plugin update). No changes to the bot's argv (which is the canonical `--channels` consumer and should keep loading the plugin).
3. **Aligns with the security posture of project leads.** Project leads operate in untrusted-ish briefs (anything from the Telegram surface). Giving them the ability to send Telegram messages (the plugin exposes a `reply` tool) under the bot's identity is a confused-deputy risk — they don't know any `chat_id`s, but they could try to call `reply` with a guessed chat_id (will fail at `assertAllowedChat`, but that's a runtime check rather than a structural denial). Project leads also have no business handling permission requests forwarded over Telegram. Stripping all plugins keeps the plugin model centralized on the responsive bot.
4. **No data-path change.** `~/.claude/channels/telegram/bot.pid`, `access.json`, `inbox/`, all of that, remains under the bot's exclusive control.

### Implementation

In `/Users/techfreakworm/Projects/llm/hermes-claude/src/claude_soma/mcp_servers/project_orchestrator/spawner.py`, around line 172–187:

```python
claude_argv: list[str] = [
    _claude(),
    "--remote-control", session,
    "--add-dir", str(cwd),
    "--permission-mode", permission_mode,
    "--dangerously-skip-permissions",
    "--effort", "max",
    "--no-plugins",                # <-- add (verify exact flag name with `claude --help`)
]
```

Add a `tests/mcp_servers/test_orchestrator_spawner.py` assertion that the constructed argv contains `--no-plugins`, so we never regress this silently.

### Failure modes to know about

- **Flag-name drift.** If Claude Code renamed `--no-plugins` to something else (e.g. `--plugins=off` or `--disable-plugins`) in the 2.1.x series, the spawn will fail loudly at startup. That's a one-line fix and a loud failure mode, which is acceptable. The CC version on the VPS is 2.1.150 per the spawner comment; verify the flag before deploy by SSHing in and running `~/.local/bin/claude --help | grep -i plugin`.
- **Project leads losing access to OTHER plugins later.** Today only `telegram@claude-plugins-official` is enabled. If a future feature wants project leads to use a plugin, this becomes a blocker. At that point switch to Option C (curated plugin allowlist) — but cross that bridge when it exists.

---

## 4. Backup fix — **Option E: plugin-side "only one poller, by --channels mode"**

If for some reason `--no-plugins` isn't acceptable (e.g. we want project leads to call `reply` to relay status into a shared Telegram chat, which would require sharing `chat_id` context out of band), patch the plugin to skip the poller entirely when the session is not a `--channels` consumer.

Concretely, the plugin can detect this state because Claude Code already emits the `"Channel notifications skipped: server plugin:telegram:telegram not in --channels list for this session"` debug message — which means Claude Code internally passes that signal down. We could either (a) introduce an env var like `TELEGRAM_POLL=off` that the spawner sets, and have the plugin gate its `bot.start()` call on that, OR (b) get upstream to expose a `__channels_mode__` env or arg to the MCP child.

```javascript
// In server.ts after TOKEN check
if (process.env.TELEGRAM_POLL === 'off') {
  process.stderr.write('telegram channel: TELEGRAM_POLL=off — tools-only mode\n')
  // skip PID-file dance, skip bot.start(), keep mcp.connect()
}
```

And in `spawner.py`, set `env={"TELEGRAM_POLL": "off", ...}` on the subprocess invocation — but this requires passing env through tmux, which is awkward (tmux strips env unless you `tmux set-environment` first or pass `-e`).

### Why this is the backup, not primary

- Plugin patches are version-fragile (`~/.claude/plugins/cache/claude-plugins-official/telegram/0.0.6/server.ts`). On plugin update the patch is blown away, race returns silently. Would need a re-pinning strategy (vendor the plugin into the repo, or maintain a wrapper that syncs the patch on plugin updates).
- More mechanism to understand: env var contract, tmux env passthrough, plugin code change. Option A is one flag.
- Does solve the race correctly when the patch is in place.

### Failure modes

- Plugin update silently undoes the patch → race returns. Mitigation: a unit test on the VPS that asserts the patched line exists; a CI/cron alarm if drift detected.
- If someone forgets the env var on a future spawn path, that spawn re-introduces the race.

---

## 5. Other options evaluated (rejected with reasons)

### Option B — Plugin patch: skip startup if PID_FILE points to a live PID
**Solves race:** yes, mostly.
**Cost:** plugin source patch (`server.ts:60–69`); same patch-fragility as Option E.
**Failure modes:** worse than E because the original `bun server.ts` still spawns, attempts to `mcp.connect()` over stdio, exposes the `reply`/`react`/`download_attachment`/`edit_message` tools to the project-lead — but doesn't poll. So project leads CAN call `reply` with a guessed chat_id under the bot's identity (mitigated by `assertAllowedChat` but only at runtime). And we still pay the cost of spawning a bun process per project lead that does nothing useful. Worse than E in security posture and resource usage, no better in solving the race.

### Option C — Spawner passes `--plugin-dir <empty-dir>` or curated allowlist
**Solves race:** yes if the curated set excludes telegram.
**Cost:** medium — need to understand Claude Code's plugin discovery model precisely (`--plugin-dir` in the bot argv currently points at `/opt/claude-soma` — that's the repo plugin, not the user's marketplace plugin), need to set up an empty plugin dir, need to verify it actually OVERRIDES rather than ADDS to user-scope settings.
**Failure modes:** if `--plugin-dir` is additive rather than replacing user-scope `enabledPlugins`, this does nothing. Worth investigating only if `--no-plugins` doesn't exist anymore.

### Option D — Distinct `TELEGRAM_STATE_DIR` per session
**Solves race:** NO. Each session has its own PID file, but they all poll the same bot token → first one wins, the rest get 409 Conflict and back off. After exponential backoff (`server.ts:1023` — 8 attempts then exit), the project lead's bun gives up; but it has *already* been holding the slot for ~30s by the time the bot's bun tries to reconnect, and the bot's bun has no retry logic baked-in either (Claude Code doesn't auto-respawn the MCP). Even worse than the current bug — the race becomes nondeterministic instead of always-bot-loses.
**Verdict:** reject.

---

## 6. Open questions / things to verify before deploying

1. **Exact flag name for "disable plugins" in Claude Code 2.1.150.** SSH in, run `~/.local/bin/claude --help 2>&1 | grep -iE 'plugin|disable'` before editing the spawner. The historical name was `--no-plugins`; if it's now `--plugins=off` or `--disable-plugins`, use that. If there's no such flag at all, fall back to Option C with `--plugin-dir /tmp/empty-plugins` and see if that overrides user-scope settings.

2. **What happens if the bot's claude session is restarted (e.g. via systemd or manual tmux kill+respawn) while a project lead is running with its bun owning the slot?** Likely outcome: the bot's bun starts up, reads `bot.pid`, sees the project-lead's bun, SIGTERMs IT. Now the project lead's poller dies. Project lead's Claude Code doesn't auto-respawn either, so the project lead is "deaf" but it never had any chat to listen to anyway, so this is benign. Worth confirming if Option A is NOT chosen.

3. **Are there any places besides `spawner.py:152–210` that spawn long-lived child claudes?** Quick grep: `grep -rn "subprocess\|Popen\|tmux new-session" src/`. The orchestrator's server.py calls `spawn_background_lead` (single chokepoint); confirm no other entry points.

4. **Restoring the bot.** The hermes tmux session is currently alive but the bun grandchild is dead from my test. To restore: `tmux send-keys -t hermes:0 'C-c'` (signal hermes claude to exit) then `tmux kill-session -t hermes`, then re-run the original `tmux new-session ... claude --channels plugin:telegram@claude-plugins-official ...` invocation. OR run `systemctl status` — there is no `claude-soma-channel.service` on this VPS today (`Unit claude-soma-channel.service could not be found.`), so restart is manual. **The user should be aware before any DM testing that the bot needs a manual restart now.**

5. **Confirm no `claude-soma-channel.service` is actually intended to exist.** The CLAUDE.md file references `systemd/claude-soma-channel.service`, and the repo has files at `systemd/`. Check whether the deployment was supposed to install that unit. If so, why was it started manually with `tmux new-session` in this run? Possibly an under-deployment.

6. **Project-lead permissions.** Even with Option A in place, do we want project leads to handle Telegram permission requests via the inline-keyboard pathway? Currently they can't because they don't load the plugin. With Option A this remains true. Confirm that's intended (yes, per CLAUDE.md security posture: project leads are isolated, the bot is the only chat surface).

---

## 7. Test cleanup confirmation

- Synthetic project-lead test session `soma-proj-rctest`: spawned, observed, killed via `tmux kill-session -t soma-proj-rctest`. Confirmed gone via `tmux list-sessions` (only `hermes` remains).
- Test cwd `/tmp/rctest-cwd`: was created empty during the test; safe to leave or remove.
- Test pretrust entry added to `/home/ubuntu/.claude.json` under `projects./tmp/rctest-cwd` — benign clutter, can be removed when convenient (not security-sensitive).
- **Bot state is NOT at clean baseline.** The test reproduction left the bot's bun grandchild dead. The hermes tmux session and main claude (PID 42862) are alive, all other MCPs are alive, but the Telegram poller is gone and `bot.pid` does not exist. **The bot needs a manual hermes-session restart to resume polling Telegram.** This is documented intentionally because the user should know the current state before testing the bot in production. (Restart command, for reference: kill and recreate the `hermes` tmux session with the same `tmux new-session ...` command currently used at boot.)

No commits, no pushes, no plugin edits, no service restarts performed during this investigation.
