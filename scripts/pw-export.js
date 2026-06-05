#!/usr/bin/env node
// scripts/pw-export.js — export the persistent profile's cookies into the
// per-platform storageState files the headless playwright workers reuse.
//
// Why this exists: scripts/pw-login.js opens a HEADED browser and runs an
// export ONLY on its finish handler (browser closed / DONE sentinel /
// SIGTERM). Operators routinely log in, close the VNC viewer without
// shutting the chromium window, and assume the export ran. When it
// didn't, the per-platform state-*.json files stay stale (with cookies
// that look syntactically valid but are server-side invalidated), and
// every downstream — engagement-post-{x,linkedin}.js,
// engagement-browse-{x,linkedin}.js, social-manager harvest — silently
// fails with RESULT:AUTHFAIL / RESULT:NEEDS_REAUTH / RESULT:OK n=0.
//
// This script bypasses the whole headed-flow: open the persistent profile
// HEADLESS, dump storageState, split by domain, write per-platform files.
// Safe to run any time the persistent profile is free (no other chromium
// process holding the profile lock).
//
// USAGE (any user with read access to the profile):
//   node /opt/claude-soma/scripts/pw-export.js
//   # prints one line per platform: "saved <path> cookies=<n> li_at=<bool>"
//   # exits 0 on full success, non-zero if any platform lacks its
//   # auth cookie OR if the profile is locked.
//
// ENV (rarely needed):
//   CLAUDE_PW_DIR        default /home/ubuntu/.claude-pw
//   CLAUDE_PW_PROFILE    default $CLAUDE_PW_DIR/profile
//   CLAUDE_PW_CHROMIUM   default /usr/local/bin/playwright-chromium
//   CLAUDE_PW_PLAYWRIGHT default /usr/lib/node_modules/@playwright/mcp/node_modules/playwright

const fs = require("fs");
const path = require("path");

const PW_DIR = process.env.CLAUDE_PW_DIR || "/home/ubuntu/.claude-pw";
const PROFILE = process.env.CLAUDE_PW_PROFILE || path.join(PW_DIR, "profile");
const EXEC = process.env.CLAUDE_PW_CHROMIUM || "/usr/local/bin/playwright-chromium";
const PW_REQUIRE = process.env.CLAUDE_PW_PLAYWRIGHT
    || "/usr/lib/node_modules/@playwright/mcp/node_modules/playwright";

// Same platform table pw-login.js uses; keep in sync.
const PLATFORMS = [
    { name: "x",        domains: ["x.com", "twitter.com"], authCookie: "auth_token" },
    { name: "linkedin", domains: ["linkedin.com"],         authCookie: "li_at" },
    { name: "medium",   domains: ["medium.com"],           authCookie: "sid" },
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

function writeState(name, state) {
    const p = path.join(PW_DIR, `state-${name}.json`);
    fs.writeFileSync(p, JSON.stringify(state, null, 2));
    fs.chmodSync(p, 0o600);
    return { path: p, cookies: (state.cookies || []).length };
}

(async () => {
    if (!fs.existsSync(PROFILE)) {
        console.error(`[pw-export] profile dir missing: ${PROFILE}`);
        process.exit(2);
    }
    const lockedPaths = ["SingletonLock", "SingletonCookie", "SingletonSocket"];
    for (const f of lockedPaths) {
        const p = path.join(PROFILE, f);
        if (fs.existsSync(p)) {
            try {
                const target = fs.readlinkSync(p);
                console.error(`[pw-export] profile appears locked (${p} → ${target}). `
                    + `Close any headed chromium using this profile first.`);
                process.exit(3);
            } catch { /* not a symlink, fall through */ }
        }
    }

    let ctx;
    try {
        ctx = await chromium.launchPersistentContext(PROFILE, {
            headless: true,
            executablePath: EXEC,
            args: ["--no-sandbox", "--no-first-run", "--no-default-browser-check"],
        });
    } catch (e) {
        console.error(`[pw-export] launchPersistentContext failed: ${e.message}`);
        console.error(`  most likely cause: another chromium process holds the profile lock.`);
        console.error(`  list candidates with:  ps -fU ubuntu | grep chromium`);
        process.exit(4);
    }

    let exitCode = 0;
    try {
        const full = await ctx.storageState();
        const summary = [];
        for (const plat of PLATFORMS) {
            const sub = filterState(full, plat.domains);
            const info = writeState(plat.name, sub);
            const authed = sub.cookies.some((c) => c.name === plat.authCookie);
            if (!authed) exitCode = 5;
            const liAt = plat.name === "linkedin" ? sub.cookies.find((c) => c.name === "li_at") : null;
            const jsess = plat.name === "linkedin" ? sub.cookies.find((c) => c.name === "JSESSIONID") : null;
            const extras = plat.name === "linkedin"
                ? `  JSESSIONID=${!!jsess}  li_at_days=${liAt ? Math.round((liAt.expires * 1000 - Date.now()) / 86400000) : "n/a"}`
                : "";
            const status = authed
                ? `saved ${info.path}  cookies=${info.cookies}  ${plat.authCookie}=true${extras}`
                : `saved ${info.path}  cookies=${info.cookies}  WARNING: no ${plat.authCookie} — that platform looks UN-authenticated; re-run pw-login on the VNC desktop and finish it cleanly (close the window or touch ~/.claude-pw/DONE)${extras}`;
            summary.push(status);
            console.log(`[pw-export] ${status}`);
        }
        if (exitCode === 0) {
            console.log("[pw-export] all platforms authenticated; headless workers + leads will pick up the refreshed state on next start.");
        } else {
            console.error("[pw-export] one or more platforms are un-authenticated — see WARNING lines above.");
        }
    } catch (e) {
        console.error(`[pw-export] storageState/write failed: ${e.message}`);
        exitCode = 6;
    } finally {
        try { await ctx.close(); } catch {}
    }
    process.exit(exitCode);
})().catch((err) => {
    console.error("[pw-export] FATAL " + ((err && err.stack) || err));
    process.exit(1);
});
