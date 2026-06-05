#!/usr/bin/env node
// scripts/engagement-browse-linkedin.js — harvest the top N posts from the
// LinkedIn home feed using an exported playwright storageState.
//
// Root cause story (2026-06-05):
//   LinkedIn rewrote the feed to use CSS-modules-hashed class names
//   (_5734e67c, _91e727f1, etc) and a new virtualized container marked
//   data-testid="mainFeed" + data-component-type="LazyColumn". Every legacy
//   selector (.feed-shared-update-v2, .update-components-update,
//   .occludable-update) is dead. The new harvest path:
//     1. Wait + scroll to materialize posts in the LazyColumn.
//     2. Iterate `[data-testid="mainFeed"] > div` and filter by
//        innerText starting with "Feed post " — LinkedIn emits this
//        screen-reader prefix on every post card (stable in their A11y
//        contract).
//     3. From each card extract: first /in/<slug>/ profile link (author);
//        author display name from text head; visible body text. Direct
//        post-permalink anchors are NOT exposed in plain `<a href>` on
//        cards anymore — the dispatcher's downstream needs the permalink,
//        so we derive a stable browser URL from the author handle:
//        https://www.linkedin.com/in/<slug>/recent-activity/all/ — that
//        is what the engagement post helper navigates to anyway when
//        permalink resolution is needed, and it's enough for operator
//        review of the draft.
//
// OUTPUT (stdout, last line, machine-parseable):
//   RESULT:OK n=<count> auth=<bool> url=<final-url>
//   RESULT:NEEDS_REAUTH url=<final-url>      — storageState rejected /
//                                              login wall. Operator must
//                                              re-run pw-login on the
//                                              VNC desktop.
//   RESULT:ERROR <message>
//
// Each JSON-line object emitted before RESULT has the queue.jsonl-compatible
// schema: platform, source_author, source_permalink, source_post_excerpt.
// IMPORTANT: field is `source_permalink` (not `source_post_url`) so the
// downstream post helpers + queue can match by the same key.

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
        args: [
            "--no-sandbox",
            "--no-first-run",
            "--no-default-browser-check",
            // Mild stealth — proven to reach an authenticated /feed/ render.
            // Doesn't bypass LinkedIn's anti-bot for everything but lets
            // the LazyColumn hydrate and the storageState cookies stay live.
            "--disable-blink-features=AutomationControlled",
        ],
    });
    const context = await browser.newContext({
        storageState: STATE,
        viewport: { width: 1400, height: 2400 },
        userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    });
    await context.addInitScript(() => {
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        window.chrome = { runtime: {}, app: {} };
    });
    const page = await context.newPage();
    try {
        await page.goto("https://www.linkedin.com/feed/", {
            waitUntil: "domcontentloaded",
            timeout: 60000,
        });
        await page.waitForTimeout(8000);

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
            console.log(`RESULT:NEEDS_REAUTH url=${finalUrl}`);
            await browser.close();
            return;
        }

        // Also detect "authenticated but feed completely empty" — that's
        // the FI-ENGAGEMENT-LI-HEADLESS pre-fix symptom. If mainFeed never
        // materializes after settle + scroll, the session is either
        // shadow-banned or auth is partially valid. Treat as NEEDS_REAUTH
        // so the operator gets an explicit signal.
        await page.evaluate(() => window.scrollBy(0, window.innerHeight * 0.7));
        await page.waitForTimeout(3000);
        for (let i = 0; i < 6; i++) {
            await page.evaluate(() => window.scrollBy(0, window.innerHeight * 0.7));
            await page.waitForTimeout(2500);
        }
        // Settle.
        await page.waitForTimeout(4000);

        const mainFeedExists = await page.evaluate(
            () => !!document.querySelector('[data-testid="mainFeed"]')
        );
        if (!mainFeedExists) {
            // We're authenticated (no login wall) but mainFeed didn't
            // render — likely an anti-bot soft-block or a LinkedIn revision
            // that broke the testid contract. Flag distinctly.
            console.log(`RESULT:ERROR mainFeed-container-missing url=${finalUrl}`);
            await browser.close();
            return;
        }

        const posts = await page.evaluate(() => {
            const main = document.querySelector('[data-testid="mainFeed"]');
            if (!main) return [];
            const out = [];
            for (const card of main.children) {
                const text = (card.innerText || "");
                // LinkedIn marks every post card with "Feed post " as a
                // screen-reader prefix (a11y contract). Promotions /
                // suggested / connection-comment-on are still posts; the
                // operator filters quality at draft time, not here.
                if (!/^Feed post[\s\n]/.test(text)) continue;

                // First profile link is the author. Skip company-page
                // links — they're navigation to a company's posts list,
                // not the author of the visible post.
                const profileAnchor = [...card.querySelectorAll('a[href*="/in/"]')]
                    .find(a => /\/in\/[a-zA-Z0-9-]+\/?(\?|$)/.test(a.getAttribute("href") || ""));
                if (!profileAnchor) continue;
                const profileHref = profileAnchor.getAttribute("href");
                const handleMatch = profileHref.match(/\/in\/([a-zA-Z0-9-]+)/);
                if (!handleMatch) continue;
                const handle = handleMatch[1];

                // Author display name: first non-empty line after the
                // "Feed post " prefix that isn't a known noise token
                // ("Suggested", "Promoted", reaction labels, etc).
                const lines = text.replace(/^Feed post[\s\n]+/, "").split("\n").map(s => s.trim()).filter(Boolean);
                const noise = new Set(["Suggested", "Promoted", "Sponsored", "•", "3rd+", "2nd", "1st", "Follow"]);
                let author = "";
                for (const ln of lines) {
                    if (!noise.has(ln) && !ln.startsWith("•") && ln.length > 1 && ln.length < 80) {
                        author = ln;
                        break;
                    }
                }
                if (!author) author = handle;

                // Body excerpt: skip lines until we find the first long
                // line (>40 chars) that looks like post content rather
                // than header chrome (reactions, follower count, time, etc).
                let excerpt = "";
                for (const ln of lines) {
                    if (ln.length > 40 && !/^\d+ followers?\s*$/.test(ln) && !/^[0-9hdwm]+ ago\b/.test(ln)) {
                        excerpt = ln;
                        break;
                    }
                }
                if (!excerpt) {
                    // Fall back to the concatenation of the non-noise lines.
                    excerpt = lines.filter(ln => !noise.has(ln)).join(" ");
                }

                // Permalink: the post's actual urn isn't reliably exposed
                // in the rendered DOM. Use the author's recent-activity
                // page as a stable, browser-navigable permalink proxy —
                // the operator can find the specific post there, and the
                // post helper script accepts post URLs of either pattern.
                const permalink = `https://www.linkedin.com/in/${handle}/recent-activity/all/`;

                out.push({
                    platform: "linkedin",
                    source_author: author,
                    source_permalink: permalink,
                    source_post_excerpt: excerpt.slice(0, 280),
                });
            }
            return out;
        });

        // Dedup by author+excerpt-prefix (different posts from same author
        // produce different excerpts; same author's same post never gets a
        // double draft).
        const seen = new Set();
        const unique = [];
        for (const p of posts) {
            const key = p.source_permalink + "|" + (p.source_post_excerpt || "").slice(0, 60);
            if (seen.has(key)) continue;
            seen.add(key);
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
