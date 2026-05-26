#!/usr/bin/env node
// scripts/pw-login.js -- the once-a-month, run-via-VNC platform login.
//
// Opens a HEADED Chromium on a PERSISTENT profile so the user logs into each
// platform by hand ONCE. The profile keeps you logged in across runs (so you
// rarely re-login); on finish we export a per-platform Playwright storageState
// that the headless MCP servers (`playwright-x`, `-linkedin`, `-medium`, all
// `--storage-state ~/.claude-pw/state-<platform>.json`) and project-leads reuse.
//
// Usage (on the VNC desktop):
//   node /opt/claude-soma/scripts/pw-login.js     # opens one window, one tab per platform
//   ...log into each tab by hand (handle 2FA), then close the window
//                                                  (or: touch ~/.claude-pw/DONE)
//
// Finish is signalled by the user (close window / DONE sentinel / SIGTERM); we
// then export per-platform state and exit. Deliberately NO periodic export --
// that opened utility tabs mid-login and disrupted it (see prior login_session.js).
//
// No emoji. Paths overridable via env for testing.

const fs = require("fs");
const path = require("path");

const PW_DIR = process.env.CLAUDE_PW_DIR || "/home/ubuntu/.claude-pw";
const PROFILE = process.env.CLAUDE_PW_PROFILE || path.join(PW_DIR, "profile");
const DONE = path.join(PW_DIR, "DONE");
const EXEC = process.env.CLAUDE_PW_CHROMIUM || "/usr/local/bin/playwright-chromium";
const PW_REQUIRE = process.env.CLAUDE_PW_PLAYWRIGHT
  || "/usr/lib/node_modules/@playwright/mcp/node_modules/playwright";

// One window, one tab per platform. domains[] is used to split the combined
// storageState into per-platform files so each state-<name>.json holds only
// that platform's cookies/origins.
// authCookie is the platform's logged-in session cookie -- used only to warn if
// a tab looks un-logged-in when we export (so the user notices a missed login).
const PLATFORMS = [
  { name: "x", loginUrl: "https://x.com/login", domains: ["x.com", "twitter.com"], authCookie: "auth_token" },
  { name: "linkedin", loginUrl: "https://www.linkedin.com/login", domains: ["linkedin.com"], authCookie: "li_at" },
  { name: "medium", loginUrl: "https://medium.com/m/signin", domains: ["medium.com"], authCookie: "sid" },
];

const { chromium } = require(PW_REQUIRE);

function matchesDomain(host, domains) {
  const h = String(host || "").replace(/^\./, "").toLowerCase();
  return domains.some((d) => h === d || h.endsWith("." + d));
}

// Split a full storageState into one platform's subset.
function filterState(full, domains) {
  const cookies = (full.cookies || []).filter((c) => matchesDomain(c.domain, domains));
  const origins = (full.origins || []).filter((o) => {
    try { return matchesDomain(new URL(o.origin).hostname, domains); }
    catch { return false; }
  });
  return { cookies, origins };
}

function writeState(name, state) {
  const p = path.join(PW_DIR, `state-${name}.json`);
  fs.writeFileSync(p, JSON.stringify(state, null, 2));
  fs.chmodSync(p, 0o600);
  return { path: p, cookies: (state.cookies || []).length };
}

(async () => {
  if (!process.env.DISPLAY) {
    console.error("[pw-login] DISPLAY is not set -- run this on the VNC desktop "
      + "(headed browser needs an X display).");
    process.exit(2);
  }
  fs.mkdirSync(PW_DIR, { recursive: true });
  try { fs.chmodSync(PW_DIR, 0o700); } catch {}

  const context = await chromium.launchPersistentContext(PROFILE, {
    headless: false,
    executablePath: EXEC,
    viewport: null,
    args: ["--no-sandbox", "--no-first-run", "--no-default-browser-check", "--start-maximized"],
  });

  const existing = context.pages();
  const first = existing.length ? existing[0] : await context.newPage();
  await first.goto(PLATFORMS[0].loginUrl, { waitUntil: "domcontentloaded" }).catch(() => {});
  for (let i = 1; i < PLATFORMS.length; i++) {
    const p = await context.newPage();
    await p.goto(PLATFORMS[i].loginUrl, { waitUntil: "domcontentloaded" }).catch(() => {});
  }

  console.log(`[pw-login] OPEN  display=${process.env.DISPLAY}  profile=${PROFILE}`);
  console.log(`[pw-login] Log into each tab (${PLATFORMS.map((p) => p.name).join(", ")}), `
    + `then close the window (or: touch ${DONE}).`);

  let finishing = false;
  async function finish(reason) {
    if (finishing) return;
    finishing = true;
    try {
      const full = await context.storageState();
      for (const plat of PLATFORMS) {
        const sub = filterState(full, plat.domains);
        const info = writeState(plat.name, sub);
        const authed = sub.cookies.some((c) => c.name === plat.authCookie);
        const warn = authed ? "" : `  (WARNING: no ${plat.authCookie} cookie -- not logged in?)`;
        console.log(`[pw-login] saved ${info.path}  cookies=${info.cookies}${warn}`);
      }
      console.log(`[pw-login] done (${reason}). Headless sessions + leads will now `
        + `reuse this auth.`);
    } catch (e) {
      console.error("[pw-login] storageState error: " + (e && e.message));
    }
    try { await context.close(); } catch {}
    process.exit(0);
  }

  process.on("SIGTERM", () => finish("SIGTERM"));
  process.on("SIGINT", () => finish("SIGINT"));
  context.on("close", () => finish("browser-closed"));
  setInterval(() => {
    if (fs.existsSync(DONE)) { try { fs.unlinkSync(DONE); } catch {} finish("sentinel"); }
  }, 3000);

  await new Promise(() => {}); // keep the browser open until the user finishes
})().catch((err) => {
  console.error("[pw-login] FATAL " + ((err && err.stack) || err));
  process.exit(1);
});
