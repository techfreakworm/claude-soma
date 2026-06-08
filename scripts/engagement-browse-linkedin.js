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
//        author display name from text head; visible body text.
//     4. Permalink resolution uses a layered strategy (see FI-LI-HARVEST-COPYLINK
//        2026-06-08):
//        Layer A — Copy-link menu (primary): click the card's control-menu
//          button, click "Copy link to post" in the dropdown, read clipboard
//          via navigator.clipboard.readText(), canonicalize via extractUrn.
//          Requires clipboard-read/write permissions granted to origin.
//        Layer B — Embedded URN (fallback): search outerHTML + data-urn /
//          data-id / data-activity-urn attributes for any urn:li:(activity|
//          share|ugcPost):<id> pattern. Some cards still embed it.
//        Skip with counter if both layers miss — not a real post the
//          operator can deep-link to via a comment.
//
//   IMPORTANT — selector validation: the control-menu button selectors
//   (button[aria-label*="control menu" i] etc) and the "Copy link to post"
//   menu item text match are UNVERIFIED against the live LinkedIn DOM.
//   They MUST be validated via social-manager's warm playwright-linkedin
//   MCP session before relying on Layer A. See LIVE VALIDATION NEEDED
//   comment block below for details.
//
// OUTPUT (stdout, last line, machine-parseable):
//   RESULT:OK n=<count> auth=<bool> url=<final-url> [skipped_no_permalink=<k>]
//   RESULT:NEEDS_REAUTH url=<final-url>      — storageState rejected /
//                                              login wall. Operator must
//                                              re-run pw-login on the
//                                              VNC desktop.
//   RESULT:ERROR <message>
//
// Each JSON-line object emitted before RESULT has the queue.jsonl-compatible
// schema: platform, source_author, source_permalink, source_excerpt.
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

// ---------------------------------------------------------------------------
// Pure helpers — module-scope, exported for unit tests.
// ---------------------------------------------------------------------------

// extractUrn(text) — given any string, return the FIRST match of
// urn:li:(activity|share|ugcPost):<id> as a canonical permalink URL,
// PRESERVING THE URN TYPE. Returns null if no match.
//
// Examples:
//   extractUrn("...urn:li:activity:123...")
//     => "https://www.linkedin.com/feed/update/urn:li:activity:123/"
//   extractUrn("...urn:li:share:456...")
//     => "https://www.linkedin.com/feed/update/urn:li:share:456/"
//   extractUrn("no urn here") => null
function extractUrn(text) {
    if (!text) return null;
    const m = String(text).match(/urn:li:(activity|share|ugcPost):(\d+)/);
    if (!m) return null;
    // m[1] = type (activity|share|ugcPost), m[2] = numeric id
    return `https://www.linkedin.com/feed/update/urn:li:${m[1]}:${m[2]}/`;
}

// canonicalizeLinkedInUrl(url) — given a copied LinkedIn URL (which may
// have query strings / tracking params / be a /posts/<slug>-<urn> form),
// strip query/fragment, run extractUrn on the full URL string, and return
// the canonical feed-update URL or null.
//
// Examples:
//   canonicalizeLinkedInUrl("https://www.linkedin.com/feed/update/urn:li:activity:123/?trackingId=abc")
//     => "https://www.linkedin.com/feed/update/urn:li:activity:123/"
//   canonicalizeLinkedInUrl("https://www.linkedin.com/posts/johndoe-urn:li:activity:123-activity-7199...")
//     => "https://www.linkedin.com/feed/update/urn:li:activity:123/"
//   canonicalizeLinkedInUrl("https://example.com/foo") => null
function canonicalizeLinkedInUrl(url) {
    if (!url) return null;
    // Must be a linkedin.com URL (loose check — strips query/fragment first).
    const s = String(url);
    if (!/linkedin\.com/i.test(s)) return null;
    // extractUrn works on the full URL string — it finds the first URN
    // regardless of where it appears (path, query, fragment, slug, etc).
    return extractUrn(s);
}

// ---------------------------------------------------------------------------
// LIVE VALIDATION NEEDED — selectors unverified against current LinkedIn DOM
// ---------------------------------------------------------------------------
//
// The following selectors are best-effort based on LinkedIn's known ARIA
// patterns as of 2026. LinkedIn frequently revises its SDUI/shadow-DOM feed.
// BEFORE relying on Layer A (Copy-link menu) in production:
//
//   1. Open social-manager's warm playwright-linkedin MCP session (the
//      authenticated session that bypasses the /checkpoint/challenge wall).
//   2. Navigate to https://www.linkedin.com/feed/
//   3. Inspect a feed card's control-menu button. Verify which aria-label
//      it uses from this candidate list:
//        - button[aria-label*="control menu" i]
//        - button[aria-label*="more actions" i]
//        - button[aria-label*="open control menu" i]
//   4. Open the menu dropdown. Find the "Copy link to post" item. Verify:
//        - Its visible text matches /copy link/i (case-insensitive)
//        - Its container selector (for the waitForSelector below)
//   5. Verify that navigator.clipboard.readText() returns the post URL
//      (not an empty string) after clicking "Copy link to post".
//   6. Update MENU_BUTTON_SELECTORS and COPY_LINK_TEXT_RE below if needed.
//
// UPDATE THESE if live validation finds different selectors:
const MENU_BUTTON_SELECTORS = [
    'button[aria-label*="control menu" i]',
    'button[aria-label*="more actions" i]',
    'button[aria-label*="open control menu" i]',
];
const COPY_LINK_TEXT_RE = /copy link/i;
// Selector for the dropdown container that appears after clicking the menu
// button. Used as a waitForSelector target. LinkedIn uses role="listbox" or
// a div with a class — this catches both common patterns:
const DROPDOWN_SELECTOR = '[role="listbox"], [role="menu"], .artdeco-dropdown__content';
// ---------------------------------------------------------------------------

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

// resolvePermalinkLayerA — try the Copy-link menu for the card at index i.
// Returns canonical permalink string or null on any failure.
async function resolvePermalinkLayerA(page, cardLocator) {
    try {
        // Find the menu button within this card. Try each selector candidate.
        let menuBtn = null;
        for (const sel of MENU_BUTTON_SELECTORS) {
            const btn = cardLocator.locator(sel).first();
            const visible = await btn.isVisible().catch(() => false);
            if (visible) {
                menuBtn = btn;
                break;
            }
        }
        if (!menuBtn) return null;

        await menuBtn.click({ timeout: 3000 });

        // Wait for the dropdown to appear.
        await page.waitForSelector(DROPDOWN_SELECTOR, { timeout: 2000 });

        // Find the "Copy link to post" menu item by visible text.
        const menuItems = page.locator(`${DROPDOWN_SELECTOR} [role="option"], ${DROPDOWN_SELECTOR} [role="menuitem"], ${DROPDOWN_SELECTOR} li`);
        const count = await menuItems.count();
        let copyLinkItem = null;
        for (let j = 0; j < count; j++) {
            const item = menuItems.nth(j);
            const txt = await item.textContent().catch(() => "");
            if (COPY_LINK_TEXT_RE.test(txt || "")) {
                copyLinkItem = item;
                break;
            }
        }
        if (!copyLinkItem) {
            await page.keyboard.press("Escape");
            return null;
        }

        await copyLinkItem.click({ timeout: 2000 });
        // Brief wait for clipboard write to complete.
        await page.waitForTimeout(400);

        const clipText = await page.evaluate(() => navigator.clipboard.readText()).catch(() => null);
        await page.keyboard.press("Escape");

        if (!clipText) return null;
        return canonicalizeLinkedInUrl(clipText);
    } catch (_) {
        // Best-effort: close any open menu before returning.
        await page.keyboard.press("Escape").catch(() => {});
        return null;
    }
}

// resolvePermalinkLayerB — search the card's outerHTML and data attributes
// for any urn:li:(activity|share|ugcPost):<id> pattern.
// cardMeta comes from the page.evaluate harvest and already contains
// outerHTML + dataAttrs as plain strings.
function resolvePermalinkLayerB(cardMeta) {
    // Try outerHTML first (most likely to contain the URN).
    const fromHtml = extractUrn(cardMeta.outerHTML || "");
    if (fromHtml) return fromHtml;
    // Try each data attribute value.
    for (const val of Object.values(cardMeta.dataAttrs || {})) {
        const fromAttr = extractUrn(val);
        if (fromAttr) return fromAttr;
    }
    return null;
}

async function _main() {
    const browser = await chromium.launch({
        headless: true,
        executablePath: EXEC,
        args: [
            "--no-sandbox",
            "--no-first-run",
            "--no-default-browser-check",
            // Mild stealth — proven to reach an authenticated /feed/ render.
            "--disable-blink-features=AutomationControlled",
        ],
    });
    const context = await browser.newContext({
        storageState: STATE,
        viewport: { width: 1400, height: 2400 },
        userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    });

    // Grant clipboard permissions so Layer A's navigator.clipboard.readText()
    // works after the "Copy link to post" menu action.
    await context.grantPermissions(["clipboard-read", "clipboard-write"], {
        origin: "https://www.linkedin.com",
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

        // Scroll the LazyColumn just enough to materialize a workable
        // batch of posts. LinkedIn's anti-bot triggers an interstitial
        // /checkpoint/challenge/ flow when the scroll is too aggressive
        // (live witness: bumping this to 24 scrolls tripped the challenge
        // and the script returned NEEDS_REAUTH).
        //
        // FI-LI-HARVEST-ROBUSTNESS (2026-06-05): keep the 6-scroll cadence
        // (the original safe baseline), but ADD a retry that re-checks for
        // mainFeed after a longer wait if it didn't materialize the first
        // pass. The retry doesn't scroll more — it just gives LinkedIn's
        // async render another beat.
        await page.evaluate(() => window.scrollBy(0, window.innerHeight * 0.7));
        await page.waitForTimeout(3000);
        for (let i = 0; i < 6; i++) {
            await page.evaluate(() => window.scrollBy(0, window.innerHeight * 0.7));
            await page.waitForTimeout(2500);
        }
        await page.waitForTimeout(4000);

        let mainFeedExists = await page.evaluate(
            () => !!document.querySelector('[data-testid="mainFeed"]')
        );
        // Retry without more scrolling — the LazyColumn sometimes lags.
        for (let r = 0; r < 2 && !mainFeedExists; r++) {
            await page.waitForTimeout(5000);
            mainFeedExists = await page.evaluate(
                () => !!document.querySelector('[data-testid="mainFeed"]')
            );
        }
        if (!mainFeedExists) {
            console.log(`RESULT:ERROR mainFeed-container-missing url=${finalUrl}`);
            await browser.close();
            return;
        }

        // Phase 1: metadata harvest via page.evaluate.
        // Returns one object per post-card child of mainFeed, tagged with
        // its zero-based childIndex so Node-side can re-locate it.
        // outerHTML and dataAttrs are captured here for Layer B fallback.
        const cardMetas = await page.evaluate(() => {
            const main = document.querySelector('[data-testid="mainFeed"]');
            if (!main) return [];
            const out = [];
            const children = Array.from(main.children);
            for (let idx = 0; idx < children.length; idx++) {
                const card = children[idx];
                const text = (card.innerText || "");
                // LinkedIn marks every post card with "Feed post " as a
                // screen-reader prefix (a11y contract).
                if (!/^Feed post[\s\n]/.test(text)) continue;

                // First profile link is the author.
                const profileAnchor = [...card.querySelectorAll('a[href*="/in/"]')]
                    .find(a => /\/in\/[a-zA-Z0-9-]+\/?(\?|$)/.test(a.getAttribute("href") || ""));
                if (!profileAnchor) continue;
                const profileHref = profileAnchor.getAttribute("href");
                const handleMatch = profileHref.match(/\/in\/([a-zA-Z0-9-]+)/);
                if (!handleMatch) continue;
                const handle = handleMatch[1];

                // Author display name.
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

                // Body excerpt.
                let excerpt = "";
                for (const ln of lines) {
                    if (ln.length > 40 && !/^\d+ followers?\s*$/.test(ln) && !/^[0-9hdwm]+ ago\b/.test(ln)) {
                        excerpt = ln;
                        break;
                    }
                }
                if (!excerpt) {
                    excerpt = lines.filter(ln => !noise.has(ln)).join(" ");
                }

                // Collect data attributes for Layer B fallback.
                const dataAttrs = {};
                for (const attr of card.attributes) {
                    if (attr.name.startsWith("data-")) {
                        dataAttrs[attr.name] = attr.value;
                    }
                }

                out.push({
                    childIndex: idx,
                    author,
                    excerpt: excerpt.slice(0, 280),
                    outerHTML: card.outerHTML,
                    dataAttrs,
                });
            }
            return out;
        });

        // Phase 2: Node-side per-card permalink resolution.
        // For each card, try Layer A (Copy-link menu) then Layer B (embedded URN).
        const posts = [];
        let skippedNoPermalink = 0;
        const feedCardLocator = page.locator('[data-testid="mainFeed"] > *');

        for (const meta of cardMetas) {
            if (posts.length >= N) break;

            const cardLocator = feedCardLocator.nth(meta.childIndex);

            // Layer A: Copy-link menu (primary).
            let permalink = await resolvePermalinkLayerA(page, cardLocator);

            // Layer B: embedded URN in outerHTML / data attributes (fallback).
            if (!permalink) {
                permalink = resolvePermalinkLayerB(meta);
            }

            if (!permalink) {
                skippedNoPermalink++;
                continue;
            }

            posts.push({
                platform: "linkedin",
                source_author: meta.author,
                source_permalink: permalink,
                source_excerpt: meta.excerpt,
            });
        }

        // Dedup by permalink + excerpt-prefix.
        const seen = new Set();
        const unique = [];
        for (const p of posts) {
            const key = p.source_permalink + "|" + (p.source_excerpt || "").slice(0, 60);
            if (seen.has(key)) continue;
            seen.add(key);
            unique.push(p);
            if (unique.length >= N) break;
        }
        for (const p of unique) {
            process.stdout.write(JSON.stringify(p) + "\n");
        }
        const skippedSuffix = skippedNoPermalink > 0 ? ` skipped_no_permalink=${skippedNoPermalink}` : "";
        console.log(`RESULT:OK n=${unique.length} auth=true url=${finalUrl}${skippedSuffix}`);
    } catch (e) {
        console.log("RESULT:ERROR " + (e && e.message));
    } finally {
        await browser.close().catch(() => {});
    }
}

// Export pure helpers for unit testing. Only launch the live harvester
// when run directly as a CLI — importing the module (e.g. from the unit
// tests) must NOT open a browser or touch LinkedIn.
module.exports = { extractUrn, canonicalizeLinkedInUrl };
if (require.main === module) {
    _main();
}
