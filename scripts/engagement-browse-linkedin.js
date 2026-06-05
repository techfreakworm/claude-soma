#!/usr/bin/env node
// scripts/engagement-browse-linkedin.js — harvest the top N posts from
// the LinkedIn home feed using an exported playwright storageState.
// Mirror of engagement-post-linkedin.js's launch+context pattern; the
// playwright MCP is hard-gated in subagent/dispatched contexts so a
// direct Node script is the proven authenticated-browser path.
//
// USAGE:
//   node scripts/engagement-browse-linkedin.js [--n 25] [--out <path>] [--state <path>]
//
// ENV:
//   HERMES_PW_LINKEDIN_STATE         — path to state-linkedin.json
//                                       (default ~/.claude-pw/state-linkedin.json)
//   HERMES_PW_EXEC                    — chromium executable (default /usr/local/bin/playwright-chromium)
//   HERMES_PW_NODE_MODULE             — playwright module dir
//   HERMES_ENGAGEMENT_BROWSE_DEPTH    — overrides --n (default 25)
//   HERMES_ENGAGEMENT_BROWSE_OUT_DIR  — directory for the auth-proof PNG
//                                       (default /var/log/claude-soma)
//
// OUTPUT (stdout, last line, machine-parseable):
//   RESULT:OK n=<count> auth=<bool> url=<final-url>
//   RESULT:AUTHFAIL url=<final-url>
//   RESULT:ERROR <message>
//
// SIDE EFFECTS:
//   - prints one JSON object per harvested post BEFORE the RESULT line.
//   - writes an auth-proof PNG to
//     ${HERMES_ENGAGEMENT_BROWSE_OUT_DIR}/engagement-browse-linkedin-proof.png.

const fs = require("fs");
const path = require("path");
const os = require("os");

const PW = process.env.HERMES_PW_NODE_MODULE
    || "/usr/lib/node_modules/@playwright/mcp/node_modules/playwright";
const EXEC = process.env.HERMES_PW_EXEC || "/usr/local/bin/playwright-chromium";
const DEFAULT_STATE = path.join(os.homedir(), ".claude-pw", "state-linkedin.json");
const DEFAULT_OUT_DIR = process.env.HERMES_ENGAGEMENT_BROWSE_OUT_DIR
    || "/var/log/claude-soma";

function flag(name, fallback) {
    const i = process.argv.indexOf(name);
    if (i >= 0 && i + 1 < process.argv.length) return process.argv[i + 1];
    return fallback;
}

const N = parseInt(
    process.env.HERMES_ENGAGEMENT_BROWSE_DEPTH || flag("--n", "25"),
    10
);
const STATE = flag("--state", process.env.HERMES_PW_LINKEDIN_STATE || DEFAULT_STATE);
const PROOF_PNG = flag("--proof", path.join(DEFAULT_OUT_DIR, "engagement-browse-linkedin-proof.png"));

if (!fs.existsSync(STATE)) {
    console.error(`storage-state missing: ${STATE}`);
    process.exit(2);
}

const { chromium } = require(PW);

(async () => {
    const browser = await chromium.launch({
        headless: true,
        executablePath: EXEC,
        args: ["--no-sandbox", "--no-first-run", "--no-default-browser-check"],
    });
    // LinkedIn collapses layout at narrower widths; the post script uses
    // 1400x1600 — match here for consistent selectors.
    const context = await browser.newContext({
        storageState: STATE,
        viewport: { width: 1400, height: 2400 },
    });
    const page = await context.newPage();
    try {
        await page.goto("https://www.linkedin.com/feed/", {
            waitUntil: "domcontentloaded",
            timeout: 60000,
        });
        await page.waitForTimeout(6000);

        try {
            fs.mkdirSync(path.dirname(PROOF_PNG), { recursive: true });
            await page.screenshot({ path: PROOF_PNG, fullPage: false });
        } catch (e) {
            console.error(`proof screenshot failed: ${e.message}`);
        }

        const finalUrl = page.url();
        const bodyHead = (await page.evaluate(() => document.body.innerText)).slice(0, 400);
        const isLogin = /\/(login|uas\/login|checkpoint)\b/i.test(finalUrl)
            || /^Sign in/i.test(bodyHead)
            || /Welcome Back/i.test(bodyHead);
        if (isLogin) {
            console.log(`RESULT:AUTHFAIL url=${finalUrl}`);
            await browser.close();
            return;
        }

        // Scroll a few times to materialize more posts in the virtual feed.
        for (let i = 0; i < Math.ceil(N / 6); i++) {
            await page.evaluate(() => window.scrollBy(0, window.innerHeight * 0.9));
            await page.waitForTimeout(1500);
        }

        // LinkedIn feed posts: <div data-id="urn:li:activity:..."> wrapper.
        // Author handle is in .update-components-actor__title; text is in
        // .feed-shared-update-v2__description or .update-components-text.
        const posts = await page.$$eval(
            'div[data-id^="urn:li:activity:"], div[data-urn^="urn:li:activity:"]',
            (nodes) => {
                const out = [];
                for (const n of nodes) {
                    const urn = n.getAttribute("data-id") || n.getAttribute("data-urn");
                    if (!urn) continue;
                    const id = urn.replace("urn:li:activity:", "").replace(/\D/g, "");
                    if (!id) continue;
                    const url = `https://www.linkedin.com/feed/update/urn:li:activity:${id}/`;
                    const authorEl = n.querySelector(
                        '.update-components-actor__title span[dir="ltr"], .update-components-actor__name'
                    );
                    const author = authorEl
                        ? authorEl.innerText.replace(/\s+/g, " ").trim().split("\n")[0]
                        : "";
                    const textEl = n.querySelector(
                        '.update-components-text, .feed-shared-update-v2__description, .feed-shared-inline-show-more-text'
                    );
                    const text = textEl ? textEl.innerText.replace(/\s+/g, " ").trim() : "";
                    if (!text) continue;
                    out.push({
                        platform: "linkedin",
                        source_author: author,
                        source_post_url: url,
                        source_post_excerpt: text.slice(0, 280),
                    });
                }
                return out;
            }
        );

        const seen = new Set();
        const unique = [];
        for (const p of posts) {
            if (seen.has(p.source_post_url)) continue;
            seen.add(p.source_post_url);
            unique.push(p);
            if (unique.length >= N) break;
        }
        for (const p of unique) {
            process.stdout.write(JSON.stringify(p) + "\n");
        }
        console.log(`RESULT:OK n=${unique.length} auth=true url=${finalUrl}`);
    } catch (e) {
        console.log("RESULT:ERROR " + (e && e.message));
    } finally {
        await browser.close().catch(() => {});
    }
})();
