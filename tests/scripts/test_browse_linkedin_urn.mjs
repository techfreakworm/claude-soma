// tests/scripts/test_browse_linkedin_urn.mjs
// Unit tests for the pure helper functions extractUrn and canonicalizeLinkedInUrl
// exported from scripts/engagement-browse-linkedin.js.
//
// Run: node --test tests/scripts/test_browse_linkedin_urn.mjs
// Requires Node >= 20 (node:test built-in).

import { createRequire } from "node:module";
import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);

// Load the helpers by requiring the script as a module (not as main).
const scriptPath = path.resolve(__dirname, "../../scripts/engagement-browse-linkedin.js");
const { extractUrn, canonicalizeLinkedInUrl } = require(scriptPath);

// ---------------------------------------------------------------------------
// extractUrn tests
// ---------------------------------------------------------------------------

test("extractUrn: activity URN returns canonical activity URL", () => {
    const result = extractUrn("some text urn:li:activity:7199000000000000000 more text");
    assert.equal(result, "https://www.linkedin.com/feed/update/urn:li:activity:7199000000000000000/");
});

test("extractUrn: share URN returns canonical share URL", () => {
    const result = extractUrn("data-id=\"urn:li:share:6900000000000000000\"");
    assert.equal(result, "https://www.linkedin.com/feed/update/urn:li:share:6900000000000000000/");
});

test("extractUrn: ugcPost URN returns canonical ugcPost URL", () => {
    const result = extractUrn("tracking?id=urn:li:ugcPost:7100000000000000000&src=x");
    assert.equal(result, "https://www.linkedin.com/feed/update/urn:li:ugcPost:7100000000000000000/");
});

test("extractUrn: share URN is NOT rewrapped as activity", () => {
    const result = extractUrn("urn:li:share:6900000000000000000");
    // Must preserve 'share', not 'activity'
    assert.ok(result !== null, "should return a URL, not null");
    assert.ok(
        result.includes("urn:li:share:"),
        `expected URL to contain 'urn:li:share:' but got: ${result}`
    );
    assert.ok(
        !result.includes("urn:li:activity:"),
        `share URN must not be rewrapped as activity, got: ${result}`
    );
});

test("extractUrn: ugcPost URN is NOT rewrapped as activity", () => {
    const result = extractUrn("urn:li:ugcPost:7100000000000000000");
    assert.ok(result !== null);
    assert.ok(result.includes("urn:li:ugcPost:"), `expected ugcPost in URL, got: ${result}`);
    assert.ok(!result.includes("urn:li:activity:"), `ugcPost must not be rewrapped as activity, got: ${result}`);
});

test("extractUrn: returns null for garbage string", () => {
    assert.equal(extractUrn("this is just garbage text with no urn"), null);
});

test("extractUrn: returns null for empty string", () => {
    assert.equal(extractUrn(""), null);
});

test("extractUrn: returns null for null input", () => {
    assert.equal(extractUrn(null), null);
});

test("extractUrn: returns null for undefined input", () => {
    assert.equal(extractUrn(undefined), null);
});

test("extractUrn: returns null for a string with a partial/invalid URN", () => {
    // 'urn:li:activity:' with no digits should not match
    assert.equal(extractUrn("urn:li:activity: no digits here"), null);
});

test("extractUrn: picks first URN when multiple are present", () => {
    // share comes first — should return share, not the activity that follows
    const result = extractUrn("urn:li:share:111 then urn:li:activity:222");
    assert.equal(result, "https://www.linkedin.com/feed/update/urn:li:share:111/");
});

// ---------------------------------------------------------------------------
// canonicalizeLinkedInUrl tests
// ---------------------------------------------------------------------------

test("canonicalizeLinkedInUrl: strips query string from activity feed URL", () => {
    const url = "https://www.linkedin.com/feed/update/urn:li:activity:7199000000000000000/?trackingId=abc123&refId=xyz";
    const result = canonicalizeLinkedInUrl(url);
    assert.equal(result, "https://www.linkedin.com/feed/update/urn:li:activity:7199000000000000000/");
});

test("canonicalizeLinkedInUrl: strips tracking params from share URL", () => {
    const url = "https://www.linkedin.com/feed/update/urn:li:share:6900000000000000000/?lipi=urn%3Ali%3Apage%3Ad_flagship3_feed%3Babc";
    const result = canonicalizeLinkedInUrl(url);
    assert.equal(result, "https://www.linkedin.com/feed/update/urn:li:share:6900000000000000000/");
});

test("canonicalizeLinkedInUrl: handles /posts/<slug>-urn:li:activity:<id> style URL", () => {
    // LinkedIn sometimes generates /posts/ URLs with the URN embedded in the slug.
    const url = "https://www.linkedin.com/posts/johndoe_some-post-title-urn:li:activity:7199000000000000000-activity-7199000000000000000";
    const result = canonicalizeLinkedInUrl(url);
    assert.equal(result, "https://www.linkedin.com/feed/update/urn:li:activity:7199000000000000000/");
});

test("canonicalizeLinkedInUrl: handles /posts/ URL with ugcPost URN in slug", () => {
    const url = "https://www.linkedin.com/posts/janedoe_topic-urn:li:ugcPost:7100000000000000000-ugcPost-7100000000000000000";
    const result = canonicalizeLinkedInUrl(url);
    assert.equal(result, "https://www.linkedin.com/feed/update/urn:li:ugcPost:7100000000000000000/");
});

test("canonicalizeLinkedInUrl: returns null for non-LinkedIn URL", () => {
    assert.equal(canonicalizeLinkedInUrl("https://example.com/foo/bar"), null);
});

test("canonicalizeLinkedInUrl: returns null for unparseable / no-URN LinkedIn URL", () => {
    // A valid linkedin.com URL but no embedded URN
    assert.equal(canonicalizeLinkedInUrl("https://www.linkedin.com/in/johndoe/"), null);
});

test("canonicalizeLinkedInUrl: returns null for empty string", () => {
    assert.equal(canonicalizeLinkedInUrl(""), null);
});

test("canonicalizeLinkedInUrl: returns null for null input", () => {
    assert.equal(canonicalizeLinkedInUrl(null), null);
});
