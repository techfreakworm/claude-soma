# FAR-FETCHED.md — parked, do not autonomously pick up

Items in this doc are EXPLICITLY out-of-scope until the user names them.
Do NOT include them in: next-round summaries, dependency analysis, parallel-wave plans, "what's pending?" lists.
Only when the user explicitly says "let's do <item> now" does an item leave this doc and re-enter BUGS_PLAN with fresh scope.

If the lead believes an item belongs back in the active queue, the lead MUST surface the suggestion via a `NEEDS_INPUT` FI-NOTIFY event — never autonomously re-promote.

---

## FI-PW — Playwright cookie store hardening

**DEFERRED INDEFINITELY (user pref 2026-05-31)**

Originally scoped P2 / leverage 2 / effort M; re-scoped to M-L after the Wave 3 S15 STOP-AND-SURFACE
finding. User opted out of pursuing this further per the new FAR-FETCHED.md doctrine.

### STOP-AND-SURFACE finding from Wave 3 S15 attempt (2026-05-31)

`.mcp.json` passes `--storage-state /home/ubuntu/.claude-pw/state-<name>.json` directly to
`/usr/bin/playwright-mcp` for 4 platforms (`linkedin`, `x`, `x-article`, `medium`). Encrypting those
paths in-place would break all 4 MCP servers (they expect plaintext JSON `{cookies, origins}`). The
`pw-refresh.js` / `pw-login.js` scripts themselves never read state files back — they use
`launchPersistentContext` and only WRITE state via `context.storageState()` — so the original brief's
"decrypt at use time, re-encrypt on write" has no target inside those two scripts.

### Widened scope required (if ever un-deferred)

Add `scripts/pw-decrypt-to-shm.sh` + `scripts/pw-encrypt-from-shm.sh` wrappers, update `.mcp.json`
`--storage-state` values to `/dev/shm/pw-state-<name>.json`, wire decrypt/re-encrypt around each
playwright MCP server launch (either systemd `ExecStartPre`/`ExecStopPost` hooks or a wrapper script
around `/usr/bin/playwright-mcp`). ~2x LOC, ~3x risk surface (systemd hook ordering + tmpfs
lifecycle). Effort: M-L.

### Alternate path (not selected)

fs-level `ecryptfs` on `~/.claude-pw/` — OS-transparent decrypt for any reader. Zero changes to
`.mcp.json` or scripts. ~30 LOC for setup helper, BUT requires kernel module + cleanup on unmount +
tricky on a remote-managed VPS.

### Forward-compat note (carry-over from earlier scoping)

S17 FI-PLAT Bluesky code shipped 2026-05-31 (`d721e33`) introduced the `encrypted` key sentinel
pattern at `~/.claude-pw/bluesky.json`. The Bluesky agents themselves are now inert (user opt-out per
2026-05-31), but the sentinel-pattern design is still useful: any future encrypt-existing script can
use the same `encrypted: true` JSON-level marker to detect unencrypted credentials and migrate them.

### Mitigation today (status quo)

Filesystem-level access control — `chmod 600` on each `state-*.json` and `chmod 700` on
`~/.claude-pw/`. Single-tenant VPS; threat model is "operator compromise" not "co-tenant exfil".

### Un-defer protocol

If the user later opts back in, the lead must:
1. Re-read this doc + the STOP findings file at `/tmp/S15-FI-PW-INTEGRATION-STOP.md` (subagent
   transcript).
2. Pick a path: (a) widened-scope tmpfs wrappers, (b) fs-level ecryptfs, or (c) different approach.
3. Surface via NEEDS_INPUT for user confirmation of the chosen path before re-entering BUGS_PLAN.

---

## "More Bluesky scope" — additional Bluesky platform features beyond what shipped 2026-05-31

**DEFERRED INDEFINITELY (user pref 2026-05-31: "Forget bluesky completely. Not interested in that
at all").**

### Context

Bluesky agents + scripts shipped 2026-05-31 as part of FI-PLAT S17 (commit `d721e33`):
- `agents/social-bluesky-writer.md`
- `agents/social-bluesky-poster.md`
- `scripts/bluesky-login.sh`
- `scripts/bluesky-post.py` (urllib AT Protocol — `createSession` + `uploadBlob` + `createRecord`)
- `skills/social-publish/SKILL.md` originally included Bluesky in the active platform list (removed
  in `53f3ca6`)
- `tests/test_bluesky_poster.py` (11 tests, all green at ship time)

User opted out of Bluesky entirely on 2026-05-31. The shipped scaffolding stays on disk for
reference (deletion was explicitly NOT requested), but the active platform list in
`skills/social-publish/SKILL.md` no longer includes Bluesky.

### Scope of this FAR-FETCHED entry

Any additional Bluesky-related features are out-of-scope. Specifically:
- Bluesky-specific thread analytics or post-hoc metrics
- Bluesky engagement loops (likes/reposts/replies tracking)
- Bluesky-list management
- AT Protocol depth (post-graph traversal, custom feeds, lexicons beyond `app.bsky.feed.post`)
- Bluesky-based notifications back to FI-NOTIFY
- Cross-posting from/to Bluesky from other platforms
- Any new Bluesky-specific agents or skills

### Future FI-PLAT iterations

FI-PLAT (still in BUGS_PLAN inventory) should pick Mastodon, Threads, or similar — NOT add more
Bluesky scope.

### Un-defer protocol

If the user later opts back in to Bluesky, the existing scaffolding at `d721e33` is the starting
point. The lead must surface the request via NEEDS_INPUT before re-promoting any of the above scope
items.
