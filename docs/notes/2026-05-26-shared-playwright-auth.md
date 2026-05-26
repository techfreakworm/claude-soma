# Shared, persistent Playwright auth (X / LinkedIn / Medium)

2026-05-26. Implements the user-approved `PLAN-shared-playwright-auth.md`. Goal:
the user logs into each platform by hand, infrequently (~monthly), via VNC; that
auth then persists and is reused by ALL Playwright sessions — the bot AND project
leads, headless included — with no per-session login. Pairs with
[2026-05-26-leads-inherit-all-mcps.md](2026-05-26-leads-inherit-all-mcps.md):
that change gives leads the 5 playwright MCP servers; this one keeps their shared
auth logged-in.

## Architecture (hybrid: persistent profile -> per-platform storageState)

- **Source of truth: one persistent Chromium profile** at `~/.claude-pw/profile`.
  The monthly headed VNC login happens here, so the login persists across runs
  and any cookie refresh during use is written back in place.
- **Read path: per-platform storageState** `~/.claude-pw/state-<platform>.json`.
  The 5 playwright MCP servers run `--isolated --storage-state state-<p>.json`
  (read-only seed), so each headless session — bot or lead — starts already
  authed without contending on the single-writer profile. The SAME files back
  the bot and every lead (leads get the servers via `config/claude/lead-mcp.json`).
- **Keep-warm: a weekly job** re-opens the profile headless, visits each authed
  page (the platform reissues rolling-session cookies into the profile), and
  re-exports the per-platform states. This is what makes the monthly cadence
  hold between manual logins.

## The scripts

- `scripts/pw-login.js` — the monthly headed login (run via VNC). Opens one
  window with a login tab per platform on the persistent profile; on finish
  (close window / `touch ~/.claude-pw/DONE` / SIGTERM) exports a per-platform,
  domain-filtered storageState (chmod 600) and warns if a platform's session
  cookie is missing (looks not-logged-in).
- `scripts/pw-refresh.js` — the headless keep-warm. Run by the timer. Decides
  authed-ness by the platform's **session cookie** (`auth_token`/`li_at`/`sid`),
  NOT the landing URL (logged-out x.com/home and medium.com/me don't reliably
  redirect to a /login URL, which gave false "authed"). **Only re-exports a
  platform that is still authed**; an expired one is left untouched and gets a
  `~/.claude-pw/NEEDS_REAUTH-<platform>` sentinel — it never overwrites good auth
  with an auth-less state. (Verified: against a logged-out profile it writes
  sentinels for all three and preserves a pre-existing state file.)

Paths are env-overridable (`CLAUDE_PW_DIR`, `CLAUDE_PW_PROFILE`,
`CLAUDE_PW_CHROMIUM`, `CLAUDE_PW_PLAYWRIGHT`, `CLAUDE_PW_NAV_TIMEOUT_MS`) for
testing; defaults match this VPS.

## Monthly VNC auth flow (what the user does)

1. VNC into the desktop (existing tailnet VNC; see `scripts/setup_vnc.sh`).
2. In a terminal on the VNC desktop:
   ```
   node /opt/claude-soma/scripts/pw-login.js
   ```
   It opens one Chromium window with a tab per platform at its login page.
3. Log into each tab by hand (do 2FA there). Then **close the window** (or, from
   another terminal, `touch ~/.claude-pw/DONE`).
4. pw-login prints `saved .../state-<platform>.json cookies=N` for each — confirm
   none warns "no <cookie> -- not logged in?". Done: every headless session and
   project-lead now reuses this auth.

Re-run only when a `NEEDS_REAUTH-<platform>` sentinel appears (or a post fails on
a login wall).

## Security

`~/.claude-pw/` holds live session cookies. It is OUTSIDE the git repo and must
stay so. Dir `chmod 700`, every `state-*.json` `chmod 600`, owned by `ubuntu`.
Anyone who can read these can impersonate the user on those platforms.

## Surfacing "needs re-auth"

pw-refresh writes `~/.claude-pw/NEEDS_REAUTH-<platform>` and logs to the service
journal when a session has died. v1 surfacing = the sentinel + journal. Follow-up
(not in this change): have the healthcheck or a bot routine notice the sentinels
and DM the user "X needs re-auth — VNC in and run pw-login".

## Rollout (operator — do NOT self-deploy / do NOT restart the channel)

- Deploy the repo so `scripts/pw-{login,refresh}.js` land in `/opt/claude-soma/scripts/`.
- Install the timer:
  ```
  sudo install -m644 systemd/claude-soma-pw-refresh.{service,timer} /etc/systemd/system/
  sudo systemctl daemon-reload && sudo systemctl enable --now claude-soma-pw-refresh.timer
  ```
- The playwright MCP `.mcp.json` stanzas are unchanged (they already point at
  `state-<platform>.json`), so the bot needs no change for this. Leads pick up
  the servers via the separate leads-inherit-MCPs change (its own deploy note).
- First-time auth: the user runs the monthly VNC flow above once the infra is
  deployed.
