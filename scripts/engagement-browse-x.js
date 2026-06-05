#!/usr/bin/env node
// scripts/engagement-browse-x.js — harvest the top N posts from the X
// home feed using an exported playwright storageState. Mirror of
// engagement-post-x.js's launch+context pattern, which is the proven
// authenticated-browser path (the playwright MCP is hard-gated in
// subagent/dispatched contexts — see post script header for why).
//
// USAGE:
//   node scripts/engagement-browse-x.js [--n 25] [--out <path>] [--state <path>]
//
// ENV:
//   HERMES_PW_X_STATE       — path to state-x.json (default ~/.claude-pw/state-x.json)
//   HERMES_PW_EXEC          — chromium executable (default /usr/local/bin/playwright-chromium)
//   HERMES_PW_NODE_MODULE   — playwright module dir (default the bundled MCP path)
//   HERMES_ENGAGEMENT_BROWSE_DEPTH — overrides --n (default 25)
//   HERMES_ENGAGEMENT_BROWSE_OUT_DIR — directory for the auth-proof screenshot
//                                       (default /var/log/claude-soma)
//
// OUTPUT (stdout, last line, machine-parseable):
//   RESULT:OK n=<count> auth=<bool> url=<final-url>
//   RESULT:NEEDS_REAUTH url=<final-url>      — storageState rejected / login wall;
//                                              operator must re-run pw-login on the
//                                              VNC desktop. Distinct from a generic
//                                              error so the operator sees an
//                                              actionable signal, not silence.
//   RESULT:ERROR <message>
//
// SIDE EFFECTS:
//   - prints one JSON object per harvested post on stdout BEFORE the RESULT line.
//     Each object has the QUEUE.JSONL-compatible schema:
//       platform, source_author, source_permalink, source_excerpt
//       (engagement.v1 schema; see docs/engagement-schema.md)
//     IMPORTANT: the field is `source_permalink` (not `source_post_url`) so the
//     downstream post helpers (engagement-post-x.js) can find the URL by the
//     same key the queue.jsonl + post helpers expect.
//   - writes an auth-proof PNG to ${HERMES_ENGAGEMENT_BROWSE_OUT_DIR}/engagement-browse-x-proof.png
//     so the operator can confirm the loaded session by eye.

const fs = require("fs");
const path = require("path");
const os = require("os");

const PW = process.env.HERMES_PW_NODE_MODULE
    || "/usr/lib/node_modules/@playwright/mcp/node_modules/playwright";
const EXEC = process.env.HERMES_PW_EXEC || "/usr/local/bin/playwright-chromium";
const DEFAULT_STATE = path.join(os.homedir(), ".claude-pw", "state-x.json");
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
const STATE = flag("--state", process.env.HERMES_PW_X_STATE || DEFAULT_STATE);
const PROOF_PNG = flag("--proof", path.join(DEFAULT_OUT_DIR, "engagement-browse-x-proof.png"));

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
    const context = await browser.newContext({
        storageState: STATE,
        viewport: { width: 1280, height: 2400 },
    });
    const page = await context.newPage();
    try {
        await page.goto("https://x.com/home", {
            waitUntil: "domcontentloaded",
            timeout: 60000,
        });
        await page.waitForTimeout(5000);

        // Auth proof: try the proof screenshot regardless; if writing fails
        // (permissions, FS full), we don't bail — the JSON harvest is still
        // the primary deliverable.
        try {
            fs.mkdirSync(path.dirname(PROOF_PNG), { recursive: true });
            await page.screenshot({ path: PROOF_PNG, fullPage: false });
        } catch (e) {
            console.error(`proof screenshot failed: ${e.message}`);
        }

        const finalUrl = page.url();
        const bodyHead200 = (await page.evaluate(() => document.body.innerText)).slice(0, 200);
        const isLogin = /\/(login|i\/flow\/login|i\/flow\/signup)\b/.test(finalUrl)
            || /Log in|Sign in to X/i.test(bodyHead200);
        if (isLogin) {
            console.log(`RESULT:NEEDS_REAUTH url=${finalUrl}`);
            await browser.close();
            return;
        }

        // Scroll a few times so the virtual list materializes more articles.
        for (let i = 0; i < Math.ceil(N / 8); i++) {
            await page.evaluate(() => window.scrollBy(0, window.innerHeight * 0.9));
            await page.waitForTimeout(1500);
        }

        // X home renders posts as <article data-testid="tweet">. Each
        // article has a status anchor with /<author>/status/<id>.
        const posts = await page.$$eval(
            'article[data-testid="tweet"]',
            (articles) => {
                const out = [];
                for (const a of articles) {
                    const linkEl = a.querySelector('a[href*="/status/"]');
                    if (!linkEl) continue;
                    const href = linkEl.getAttribute("href");
                    const url = href.startsWith("http") ? href : `https://x.com${href}`;
                    const authorMatch = href.match(/^\/([^/]+)\/status\//);
                    const author = authorMatch ? `@${authorMatch[1]}` : "";
                    const textEl = a.querySelector('[data-testid="tweetText"]');
                    const text = textEl ? textEl.innerText.replace(/\s+/g, " ").trim() : "";
                    if (!text) continue;
                    out.push({
                        platform: "x",
                        source_author: author,
                        // Field MUST be `source_permalink` to match the
                        // queue.jsonl schema + engagement-post-x.js arg —
                        // a prior version emitted `source_post_url` and
                        // every harvested draft was un-postable because
                        // the post helper looked for the wrong key.
                        source_permalink: url,
                        // FI-ENGAGEMENT-SCHEMA-V1 (2026-06-06): v1
                        // canonical name is `source_excerpt`. The legacy
                        // `source_post_excerpt` is renamed here. The
                        // renderer reads both for one schema version
                        // (v1→v2 grace); producers go v1-only.
                        source_excerpt: text.slice(0, 280),
                    });
                }
                return out;
            }
        );

        // Dedup by permalink, cap to N.
        const seen = new Set();
        const unique = [];
        for (const p of posts) {
            if (seen.has(p.source_permalink)) continue;
            seen.add(p.source_permalink);
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
