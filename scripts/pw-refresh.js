#!/usr/bin/env node
// scripts/pw-refresh.js -- headless keep-warm for the shared platform auth.
//
// Run on a timer (claude-soma-pw-refresh.timer). Opens the PERSISTENT profile
// headlessly, visits each platform's authed page (which makes the server reissue
// rolling-session cookies the profile captures), and re-exports the per-platform
// storageState the MCP servers / leads read. This is what makes the monthly
// manual `pw-login` cadence actually hold between logins.
//
// SAFETY: a platform that has expired (its authed page bounces to a login wall)
// is NOT re-exported -- we must never overwrite a good state-<name>.json with a
// logged-out one. Instead we drop a NEEDS_REAUTH-<name> sentinel so the operator
// is told to VNC in and re-run pw-login. Authed platforms clear their sentinel.
//
// No emoji. Paths/timeouts overridable via env for testing.

const fs = require("fs");
const path = require("path");

const PW_DIR = process.env.CLAUDE_PW_DIR || "/home/ubuntu/.claude-pw";
const PROFILE = process.env.CLAUDE_PW_PROFILE || path.join(PW_DIR, "profile");
const EXEC = process.env.CLAUDE_PW_CHROMIUM || "/usr/local/bin/playwright-chromium";
const PW_REQUIRE = process.env.CLAUDE_PW_PLAYWRIGHT
  || "/usr/lib/node_modules/@playwright/mcp/node_modules/playwright";
const NAV_TIMEOUT = Number(process.env.CLAUDE_PW_NAV_TIMEOUT_MS || 45000);

// authCookie is the platform's logged-in SESSION cookie -- present only when
// authenticated. We decide authed-ness by its presence, NOT by the landing URL:
// logged-out x.com/home and medium.com/me don't reliably redirect to a /login
// URL, so URL checks gave false "authed" (and risked exporting an empty,
// auth-less state over a good one). An auth cookie can't be faked by anonymous
// browsing.
const PLATFORMS = [
  { name: "x", authedUrl: "https://x.com/home", domains: ["x.com", "twitter.com"], authCookie: "auth_token" },
  { name: "linkedin", authedUrl: "https://www.linkedin.com/feed/", domains: ["linkedin.com"], authCookie: "li_at" },
  { name: "medium", authedUrl: "https://medium.com/me", domains: ["medium.com"], authCookie: "sid" },
];

const { chromium } = require(PW_REQUIRE);

function matchesDomain(host, domains) {
  const h = String(host || "").replace(/^\./, "").toLowerCase();
  return domains.some((d) => h === d || h.endsWith("." + d));
}
function filterState(full, domains) {
  const cookies = (full.cookies || []).filter((c) => matchesDomain(c.domain, domains));
  const origins = (full.origins || []).filter((o) => {
    try { return matchesDomain(new URL(o.origin).hostname, domains); }
    catch { return false; }
  });
  return { cookies, origins };
}
function sentinel(name) { return path.join(PW_DIR, `NEEDS_REAUTH-${name}`); }

(async () => {
  if (!fs.existsSync(PROFILE)) {
    console.error(`[pw-refresh] no profile at ${PROFILE} -- run pw-login (VNC) first.`);
    process.exit(0);  // nothing to refresh yet; not a failure
  }
  const context = await chromium.launchPersistentContext(PROFILE, {
    headless: true,
    executablePath: EXEC,
    args: ["--no-sandbox", "--no-first-run", "--no-default-browser-check"],
  });

  // Visit each authed page first so the platform reissues rolling-session
  // cookies into the profile (best-effort; navigation errors are non-fatal).
  for (const plat of PLATFORMS) {
    const page = await context.newPage();
    try {
      await page.goto(plat.authedUrl, { waitUntil: "domcontentloaded", timeout: NAV_TIMEOUT });
    } catch (e) {
      console.error(`[pw-refresh] ${plat.name}: navigation failed: ${e && e.message}`);
    }
    await page.close().catch(() => {});
  }

  // Export the refreshed profile state ONCE, then per platform: re-export ONLY
  // if the platform's session cookie is present (authed). Otherwise leave the
  // existing state-<name>.json untouched and drop a NEEDS_REAUTH sentinel --
  // never overwrite good auth with an auth-less state.
  const full = await context.storageState();
  const status = {};
  for (const plat of PLATFORMS) {
    const sub = filterState(full, plat.domains);
    const authed = sub.cookies.some((c) => c.name === plat.authCookie);
    if (authed) {
      const p = path.join(PW_DIR, `state-${plat.name}.json`);
      fs.writeFileSync(p, JSON.stringify(sub, null, 2));
      fs.chmodSync(p, 0o600);
      try { fs.unlinkSync(sentinel(plat.name)); } catch {}
      status[plat.name] = "authed";
      console.log(`[pw-refresh] ${plat.name}: refreshed (${sub.cookies.length} cookies)`);
    } else {
      fs.writeFileSync(sentinel(plat.name), new Date().toISOString() + "\n");
      status[plat.name] = "needs-reauth";
      console.error(`[pw-refresh] ${plat.name}: NEEDS RE-AUTH (no ${plat.authCookie} cookie) -- `
        + `left state untouched, wrote ${sentinel(plat.name)} (VNC in and run pw-login).`);
    }
  }

  await context.close().catch(() => {});
  console.log("[pw-refresh] done: " + JSON.stringify(status));
})().catch((err) => {
  console.error("[pw-refresh] FATAL " + ((err && err.stack) || err));
  process.exit(1);
});
