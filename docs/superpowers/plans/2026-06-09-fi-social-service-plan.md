# FI-SOCIAL-SERVICE — E2E Plan (product, v2 — 2026-06-09)

> **Status: PROPOSED — for operator review BEFORE any code.**
> **Scope: claude-soma PRODUCT component** (revised from the earlier
> VPS-local framing). It qualifies as product because it is pure clean
> **Graph-API** integration — NO Playwright, NO browser automation, NO
> scraping, NO VNC. Normal product shape: `src/claude_soma/mcp_servers/
> social/`, committed `claude-soma-social.service` (+ refresh timer),
> `.mcp.json` entry, `tests/`, Caddy `social.mayankgupta.in`.
>
> **In scope (clean Graph API only):** OAuth + token vault + auto-refresh
> (IG / FB Page / Threads); webhook receiver (comments/mentions/messages,
> hub.challenge + HMAC) → product draft+approval surface, never auto-post;
> publish + engage API (post/reel/story/trial-reel, reply+moderate
> OWN-content comments, insights) + per-platform adapters; inbound-DM
> Messenger auto-reply (reply-only, 24h window, operator toggle); IG
> Business Discovery fetch → draft.
>
> **Out of scope (stays VPS-local/operational):** ANY Playwright/headless/
> scraping, VNC/pw-login/cookie wrangling, the X/LinkedIn engagement
> pipeline (FI-ENGAGEMENT-MIGRATE handles that), the Playwright IG/FB
> discovery fallback.
>
> **Key deliverable beyond code:** a committed `docs/social-provisioning.md`
> — the USER performs the one-time OAuth/token setup by hand; the product
> reads the resulting config + bootstraps its vault. No provisioning
> automation (no VNC/Playwright) in the product.
>
> Produced via a superpowers brainstorm→spec→plan pass building on the
> prior VPS-local draft. The never-auto-POST rule is baked in; inbound-DM
> auto-reply is the one nuance (a 1:1 in-window reply MAY auto-send, but
> the design exposes an operator toggle — see the spec). No code until
> this plan is approved. Implementation: sonnet + --effort max +
> sequential-thinking, each phase surfaced for consent.

---

## Part 1 — Brainstorm (product packaging, provisioning doc, inbound-DM, Business Discovery)

### product-packaging

**Recommended Product Shape**

FI-SOCIAL-SERVICE is a **single-process FastAPI + APScheduler** MCP-driven service deployed under claude-soma as a committed component. The service binds localhost (127.0.0.1:8000), exposes three public routes via Caddy reverse proxy at `social.mayankgupta.in`, and owns a persistent SQLite vault at `/var/lib/claude-soma/social.sqlite` (0600, bootstrapped once from user-provided env config). No separate HTTP API process or queue worker; webhook verification, publish/draft gates, and token refresh all coexist in the single app via async background tasks (APScheduler thread, MVP; migration path to dedicated timer unit documented but not implemented in Phase 1).

**Layout:**
- Code: `src/claude_soma/mcp_servers/social/server.py` + `__init__.py` (FastMCP pattern, `@mcp.tool()` decorators mirroring voice_stt/project_orchestrator).
- Systemd: `systemd/claude-soma-social.service` (binds 127.0.0.1:8000) + `systemd/claude-soma-social-refresh.timer` (daily token refresh; optional first phase).
- MCP entry: `.mcp.json` stanza with Python 3.12 args `["-m", "claude_soma.mcp_servers.social.server"]`.
- Vault: `/var/lib/claude-soma/social.sqlite` (WAL mode, 0600, ubuntu-owned), bootstrapped from user-provisioned `~/.config/social-manager/meta-tokens.env` at first startup.
- Caddy: `social.mayankgupta.in` block whitelists exactly four paths (`/oauth/callback`, `/api/webhooks/meta`, `/health`, plus optional `/api/status`); all others 404.
- Tests: `tests/mcp_servers/test_social_*.py` (pytest, ruff 100, mypy strict, subprocess try/except pattern).

**Key Tradeoffs & Design Decisions**

1. **Single FastAPI process vs. separate queue/worker:** The VPS-local prior draft (FI-SOCIAL-SERVICE-PLAN) proposed APScheduler thread for MVP simplicity. That design stands: one systemd unit, one deployment artifact, webhook verification + publish + refresh run in co-located background tickers. Alternative (separate `social-manager-worker.timer`) is documented as a Phase 2 migration if observability/scaling demands it.

2. **Token vault in SQLite vs. env file:** The prior draft correctly identified that SQLite allows atomic refresh writes + per-token metadata (issued_at, expires_at, last_refreshed) without non-atomic sed edits. Keeping the env file as bootstrap-only (read once at startup, never modified by the service) decouples secret provisioning (user's 1Password → env file → service reads) from runtime state (vault is the single source of truth). This aligns with claude-soma's pattern: no committed secrets.

3. **Product vs. Operational:** FI-SOCIAL-SERVICE is now a **product component** (committed to claude-soma, tests + systemd unit shipped). Prior confusion was that it *used to* be VPS-local only. The SCOPE CHANGE (operator 2026-06-09) clarifies: Graph API integration + webhook + publish + engagement drafts are pure API — no Playwright, no scraping, no VNC — so they belong in the product. The VPS-local operational pieces (manual token provisioning, Playwright-based IG/FB discovery fallback, the engagement pipeline waiting for FI-ENGAGEMENT-MIGRATE) stay separate.

**Top 3 Risks & Mitigations**

1. **Token expiry + refresh race (CRITICAL):** IG + Threads tokens expire 60d; if the daily refresh worker dies or is delayed, tokens expire and no publish is possible. *Mitigation:* (a) Refresh on day 50 (7-day buffer; cron logs success/failure to journalctl); (b) Pre-publish health check in the MCP shim calls `/api/tokens/status` to surface expiry and refuse publish if <24h to expiry; (c) Operator sets calendar reminder to verify refresh health 72h before expiry via the health endpoint or CLI check; (d) Manual refresh endpoint (`POST /api/tokens/refresh?platform=`) allows operator-triggered refresh if the automated run fails.

2. **Webhook signature verification bypass or meta API format drift (HIGH):** If HMAC-SHA256 verification is skipped or implemented incorrectly, attackers can forge engagement drafts; if Meta changes webhook payload format, drafts may silently fail to parse and engagement is lost. *Mitigation:* (a) HMAC verification is mandatory (`400 Bad Request` if missing/invalid header); constant-time compare via `hmac.compare_digest`; tests include known-good + tampered payloads; (b) All webhook payloads are logged verbatim to a `webhooks_queue.payload_json` column (durable, never auto-deleted) before parsing; if the parser breaks, the operator can re-process once fixed; (c) Meta's webhook docs are re-verified quarterly against the implementation (test harness documents the assertion date).

3. **Engagement draft approval surface coupling to VPS migration (MEDIUM):** The prior FI-SOCIAL-SERVICE plan assumed the review-gate/approval queue lived VPS-local (in the social-manager operational code). Now that this is a product service, it owns its own draft + approval surface; however, FI-ENGAGEMENT-MIGRATE (running in parallel) will eventually migrate the *overall* engagement pipeline out to VPS-local. *Mitigation:* (a) Product owns webhook→draft→approve→publish chain for **engagement-sourced** replies (comments, mentions via webhook); (b) Design the MCP tools (`list_drafts`, `approve_draft`) so they are **agnostic to the approval orchestrator** — they just return draft state; (c) Operational code (FI-ENGAGEMENT-MIGRATE) can call the same MCP tools to surface drafts alongside other platforms (X, LinkedIn); no re-implementation of approval logic needed; (d) Webhook-sourced drafts are never auto-posted, only scheduled/manual posts bypass the gate.

**Provisioning Deliverable**

Instead of encoding first-time setup in the code, produce a **committed** `docs/social-provisioning.md` guide with step-by-step instructions for the USER to:
1. Create the Meta developer app(s) and generate long-lived tokens (via Graph API Explorer + server-side exchange).
2. Populate `~/.config/social-manager/meta-tokens.env` with the tokens (bootstrap material, user-owned, never committed).
3. Install the systemd unit + timer via the deploy script (handles vault creation, directory perms, Caddy config).

The service reads the env file exactly once on startup, seeds the vault, and takes over all token mutation from that point. This keeps the product "clean" (no VNC automation, no Playwright provisioning) and puts the boundary exactly where it should be: the user performs one-time OAuth; the product automates the rest.

### provisioning-doc

**Recommended Approach**

Commit a step-by-step user-facing provisioning guide (`docs/social-provisioning.md`) that walks the operator through a one-time OAuth/token setup flow entirely by hand — no automation, no VNC, no Playwright. The user creates the Meta app, configures scopes + redirect URIs (new: `https://social.mayankgupta.in/oauth/callback`), generates tokens via the platform's native OAuth dialogs (Instagram.com, Threads.net, Graph API Explorer for Facebook), and pastes the resulting 60-day long-lived tokens + non-expiring Facebook Page token into a committed config file template.

The **service reads this config once at startup**, bootstraps a local SQLite vault (`/var/lib/claude-soma/social.sqlite`), and owns all token refresh/expiry logic thereafter. The config file itself (e.g., `config/social-provisioning.env` in the product repo, 0600 on disk) contains no secrets post-bootstrap—only the initial long-lived tokens + app IDs. Once the service has populated the vault, the config is consumed but not re-read.

**What the Doc Covers**

- Account prerequisites (convert IG to Business/Creator, link to a Facebook Page if desired)
- Meta App creation (create the app, add Products: Instagram/Threads/Facebook Login, note App ID/Secret)
- **Three parallel token flows** (one per platform):
  - **Instagram (Instagram-Login variant):** scopes `instagram_business_basic`, `instagram_business_content_publish`, `instagram_business_manage_comments`, `instagram_business_manage_insights`; authorize at `instagram.com/oauth/authorize`; code → short-lived → long-lived 60-day exchange (endpoints: `api.instagram.com`, then `graph.instagram.com`)
  - **Threads:** scopes `threads_basic`, `threads_content_publish`, `threads_manage_replies`, `threads_manage_insights`; authorize at `threads.net/oauth/authorize`; code → short-lived → long-lived 60-day exchange (endpoint: `graph.threads.net`)
  - **Facebook Page:** scopes `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `pages_manage_engagement`, `pages_manage_metadata`; use Graph API Explorer or manual code flow; derive non-expiring Page token from long-lived user token via `/me/accounts`
- **Redirect URI configuration:** `https://social.mayankgupta.in/oauth/callback` (the callback the service owns server-side; user must whitelist it in each platform's OAuth settings)
- Config file shape: `config/social-provisioning.env` template with `IG_APP_ID`, `IG_APP_SECRET`, `IG_LONG_LIVED_TOKEN`, `IG_USER_ID`, `THREADS_APP_ID`, `THREADS_APP_SECRET`, `THREADS_LONG_LIVED_TOKEN`, `THREADS_USER_ID`, `FB_APP_ID`, `FB_APP_SECRET`, `FB_PAGE_ACCESS_TOKEN`, `FB_PAGE_ID`, `WEBHOOK_VERIFY_TOKEN`
- Refresh/expiry guidance: IG + Threads tokens expire every 60 days; Facebook Page token is non-expiring. Refresh constraints: token must be >24h old to refresh (IG/Threads); service refreshes automatically on Day 50 of the 60-day window. No manual refresh needed by the user—service monitors and alerts if refresh fails.

**What the Service Automates**

- Reading the config file once at startup; inserting platform/account rows into the vault with issued_at/expires_at timestamps
- Token refresh logic: automatic daily cron, refreshes when `expires_at - now < 7 days` AND `issued_at ≥24h` (IG/Threads only; FB Page is skipped)
- Token expiry monitoring: surfaced via `/health` endpoint (public) and internal token-status API; alerts operator if refresh fails 3× or if expiry is <7 days
- Refresh failure recovery: if manual re-auth is needed (token expired past refresh window), service provides the OAuth start URL

**Key Tradeoffs**

1. **Manual provisioning (user-driven) vs fully automated setup (Playwright/VNC):** Manual is lower-risk (no headless session brittleness, no cookie management), verifiable by the user (they see each step), and complies with the "NO Playwright" hard rule. Tradeoff: 30–45 minutes of operator effort one-time; but this is acceptable for a product component because it only happens once and the service then owns all subsequent refresh/expiry logic for 60+ days before any operator re-engagement is needed.

2. **Config file in repo vs encrypted vault-only:** Storing initial tokens in a committed config file (0600 at rest) is simpler than requiring the user to interact with a vault API before the service starts. The file is consumed once, then the vault becomes the source of truth. Risk: if the repo is cloned to an insecure location, the config leaks; mitigation: the doc emphasizes 0600 permissions and the config is `.gitignore`d (not committed to git, only in the operator's local `/opt/claude-soma/` or `$HOME/.config/`).

3. **Separate provisioning doc vs baked-in API setup:** A committed doc decouples the service code from provisioning flow, making it testable (operator can provision → service starts → verify tokens work) and auditable (clear record of what the user did). If provisioning logic were in code/MCP, updates would require redeployment. Doc-first is operationally cleaner.

**Top 2–3 Risks + Mitigations**

1. **Token expiry cliff (60-day window → service can't refresh if operator misses the window):**
   - Risk: If the service is down or the refresh cron fails silently for >10 days before the 60-day deadline, tokens expire and cannot be refreshed (you cannot refresh an expired token). Publishing + engagement reply is blocked until the user re-runs the entire OAuth flow manually.
   - Mitigation: (a) Automatic daily refresh on Day 50 (10-day buffer); (b) `/health` endpoint surfaces token expiry date (public, no secrets leaked); (c) If refresh fails 3×, operator gets a Slack/email alert with a link to re-auth; (d) Log all refresh attempts to journalctl; (e) Operator sets a calendar reminder for Day 55 to manually check `/health` (takes 10 seconds).

2. **Redirect URI mismatch / operator typos in config:**
   - Risk: User pastes `https://social.mayankgupta.in/oauth/callback` in one platform and `https://social.mayankgupta.in/oauth/callback/` (trailing slash) in another; or forgets to save. Service callback handler doesn't recognize the platform's callback request → 400 Bad Request → token is never stored.
   - Mitigation: (a) Provisioning doc emphasizes exact redirect URI (with/without trailing slash per platform—IG has trailing, Threads/FB don't); (b) Template in config file includes both App IDs/Secrets + token fields, so operator can verify they've filled all three platforms; (c) Service `/health` endpoint reports which platforms have valid tokens (no secrets), so operator can immediately check if setup succeeded.

3. **App Secret or long-lived token leakage (committed to git, logs, error responses):**
   - Risk: User accidentally commits `config/social-provisioning.env` to the repo, or service logs the token value on error, or a failed refresh includes the token in the response body.
   - Mitigation: (a) Doc includes explicit `.gitignore` instruction to exclude `config/social-provisioning.env` and `~/.config/social-manager/`; (b) Service never logs token values, only redacted metadata (platform, issued_at, days_to_expiry); (c) Error responses for token operations return structured errors (e.g., `{status: 'token_expired', platform: 'instagram', refresh_required: true}`) with no token/secret in the response body; (d) Config file is read-only once (bootstrap), never re-read, so the risk window is small (just the first startup).

### inbound-dm-reply

**Recommended Approach**

Inbound DMs via the Instagram & Facebook Messenger APIs arrive at a dedicated webhook endpoint (`POST /webhooks/dm`), bypassing the public-engagement queue. Each DM is stored in a `dm_threads` table (platform, sender_id, conversation_id, last_message_ts, within_24h_window) and a `dm_messages` table (message_id, text, timestamp, direction). The service checks the **24-hour standard messaging window** policy at receipt time: `now() - message_timestamp <= 86400`. Within the window, an operator-authored reply (via MCP `draft_dm_reply()` tool) is **draft-gated by default** but carries an explicit **auto-reply toggle** per DM thread. If the operator enables auto-reply for that thread, the service immediately publishes the reply via the Messenger API (`POST /{conversation_id}/messages`); if disabled, the reply sits in `dm_drafts` (platform, conversation_id, text, status='pending_approval') pending explicit `approve_dm_reply()` call. This design honors the **never-auto-POST principle** for public content while recognizing that 1:1 DM replies within the window are policy-compliant (Meta explicitly permits them) and may auto-send if the operator trusts the reply. The toggle is per-thread and surfaced in the MCP tool's response, allowing the operator to set a blanket "auto-reply all DMs" policy in their configuration or decide thread-by-thread.

**Rate limits & scopes:** Both platforms require the `instagram_manage_messages` / `pages_manage_messages` scopes respectively; IG grants 250 replies/24h (pooled with thread replies), FB has no documented ceiling. The DM webhook shares the same signature-verification path as comments/mentions (`X-Hub-Signature-256` HMAC). Replies must complete within the 24h window; the drain worker checks expiry at publish time and marks failed drafts `failed_24h_window_expired` if the window closes between draft creation and approval.

**Scope requirements:** `instagram_manage_messages` (IG), `pages_manage_messages` (FB Page). Both are **Advanced Access** permissions exercisable in Development mode by app role users. No app review required.

---

**Top 2-3 Risks & Mitigations**

1. **24-hour window race & expiry enforcement (HIGH)**
   - *Risk:* Operator creates a draft at hour 23, approves it at hour 25 (after the 24h window closes). The service publishes the reply, violating Meta's policy and risking DM suspension for the account.
   - *Mitigation:* (a) Store message arrival timestamp in `dm_messages`; check `now() - arrival_ts` before every publish attempt. (b) If publish is >23.5h from receipt, emit a warning but allow it (gives operator 30 min buffer). (c) If publish is >24h, reject with `failed_24h_window_expired` + immediate alert. (d) Surface the window-close timestamp in draft responses (MCP tool shows "window closes at YYYY-MM-DD HH:MM UTC"). (e) Log all window-expiry rejections to audit trail.

2. **Auto-reply toggle misconfig → unintended mass-reply (MEDIUM)**
   - *Risk:* Operator enables "auto-reply all DMs" globally, then a spam bot sends 100 DMs in 30 seconds; the service auto-publishes 100 identical replies, hitting rate limits and appearing spammy.
   - *Mitigation:* (a) Auto-reply toggle is per-thread, not global; operator must explicitly call `enable_auto_reply(conversation_id)` for each thread. (b) Rate-limit replies: enforce 1 reply per thread per 60 seconds (prevent accidental fire-and-forget loops). (c) Log all auto-published replies with sender_id + thread_id + timestamp to a `dm_audit_log` table. (d) Provide an MCP query tool `list_recent_dm_replies()` to let operator see what was auto-published in the last hour.

3. **Token expiry during approve flow → reply fails silently (MEDIUM)**
   - *Risk:* IG token expires on day 60; operator approves a DM reply after expiry; the service calls the Messenger API with an expired token; reply fails with 401; draft status = `failed` but no proactive alert.
   - *Mitigation:* Same as the public-engagement flow: (a) refresh tokens on day 50 (7-day buffer). (b) Before each DM publish, check token expiry from the vault. (c) If <1h to expiry, refresh proactively. (d) If already expired, reject with `failed_token_expired` + Slack/log alert. (e) Surface token expiry in the MCP `status()` endpoint so the operator can pre-check health before approving drafts.

### business-discovery

**Recommended Approach**

IG Business Discovery (read-only Graph API fetch by username) becomes an **approval-gated intake** within the FI-SOCIAL-SERVICE product architecture. The flow is: (1) operator provides a known-pro-account username via MCP tool; (2) service fetches media + basic engagement metrics from `GET /{user_id}/business_discovery` (IG only; no Playwright); (3) fetched posts are surfaced as **product-owned drafts** with `status='discovery_pending_review'` in the draft table; (4) operator manually approves + edits proposed reply/engagement text; (5) publish_drain executes. **Strict line vs Playwright fallback:** this product service handles **authenticated, API-only reads**. The read-only Playwright discovery fallback (liking, following, browsing follower lists) stays operational/VPS-local — never migrated here.

**Key Design Decisions**

- **Integration point:** Business Discovery queries attach to the existing OAuth token vault and platform-adapter contract. Each platform's adapter implements a `discover_media(username: str) -> list[DiscoveryPost]` method (IG only; Threads/FB have no equivalent API). No new token type — uses the same long-lived IG token.
- **Draft surface (product-owned):** New `discovery_source` column in the `drafts` table distinguishes API-discovered posts (`discovery_source='business_discovery'`) from webhook-sourced engagement (`discovery_source='webhook'`). Same approval gate, same MCP tools (`list_drafts`, `approve_draft`). Operator sees all engagement in one surface, mixed.
- **Known pro-accounts list:** operator maintains a simple text file (e.g., `/var/lib/claude-soma/social/discovery-accounts.txt`, line-separated usernames) or passes via MCP `discover_accounts(accounts: list[str])` endpoint. Service does **not** iterate a hardcoded list — the operator drives who to monitor. Rate-limit: Business Discovery has no documented per-call limit, but BUC formula applies; each call costs impressions against the IG app's daily quota. Recommended: batch no more than 1–2 accounts per discovery run (operator's choice via MPC scheduling).
- **Media becomes draft:** `GET /{ig_user_id}/business_discovery?username={pro_account}&fields=media{id,media_type,media_product_type,timestamp}` yields media list. Each media becomes a `drafts` row with `status='discovery_pending_review'`, `source_text=<media_caption>`, `target_id=<media_id>`, `commenter=<pro_account>`, `proposed_text=<empty>` (operator fills reply). No auto-reply proposed text — the operator must author engagement intent.

**Tradeoffs & Risks**

1. **Rate-limit cliff vs real-world usage (MEDIUM)**  
   Business Discovery is cheap (~1 call per account) but shares the 4800 × impressions BUC pool with publish/comment operations. If operator monitors 10+ accounts daily, impressions spike, potentially throttling own-content operations. *Mitigation:* (a) surface BUC usage in `GET /health` per platform; (b) scope discovery to a single scheduled run per day (e.g., 02:00 UTC via a separate MCP `trigger_discovery` tool, not continuous); (c) log BUC spend per call; (d) operator monitors quota via `GET /{ig_user_id}/content_publishing_limit` endpoint.

2. **Stale discovery drafts pile up (LOW–MEDIUM)**  
   Unlike webhook-sourced engagement (time-sensitive replies), discovery posts are stale media. If operator doesn't review drafts for weeks, they accumulate with zero actionability. *Mitigation:* (a) `drafts` table includes `created_at`; MCP `list_drafts` filters/sorts by age; (b) old `discovery_pending_review` drafts can be auto-rejected after 7 days (configurable); (c) mark rejected drafts with `status='discovery_expired'` (audit trail, not deleted).

3. **Playwright fallback boundary is vague (MEDIUM)**  
   The spec says "read-only Playwright discovery" stays operational — but what exactly? If operator wants to like/follow discovered accounts, is that Playwright? Yes. If operator wants to read comment counts on a pro account's media before engaging, is that Playwright? Also yes (not in Business Discovery API). *Mitigation:* (a) document the boundary: Business Discovery API = username→media+captions; everything else (follow, like, comment on others' posts, follower lists) is Playwright-only; (b) the product service's `discover_media` returns *only* media list + captions — no engagement metrics (comment count, like count, follower count) to avoid Playwright competition; (c) if operator needs metrics for decision-making, they run the Playwright fallback separately and paste the data into the draft's `proposed_text` field.

**Top 2 Risks & Mitigations**

1. **Token expired mid-discovery run (LOW but data-loss)**  
   Operator triggers `discover_accounts(['account1', 'account2'])` at 02:00 UTC. First account succeeds, second fails with 401 (token expired 1 hour ago, refresh worker was down). Drafts from account1 are created; account2 is skipped. *Mitigation:* (a) pre-flight check in the discovery handler: if token expires within 24h, refuse the run and alert operator to refresh now; (b) always wrap discovery calls in try/except; on 401, mark all pending drafts for that account as `status='discovery_failed_token_expired'` + skip; (c) return a summary report: `{success: 1, failed: 1, created_drafts: N, errors: [...]}`.

2. **Business Discovery username resolution ambiguity (LOW)**  
   Operator passes `@techfreakworm`, but the API expects the numeric user_id. If the username is private/changed/doesn't exist, `GET /business_discovery?username=...` returns 404 or error. *Mitigation:* (a) accept both username and numeric id in the MPC tool (`discover_accounts(accounts: list[str | int])`); (b) if username given, do a pre-flight `GET /{ig_user_id}/business_discovery?username={name}&fields=id` to resolve it; on failure, return error in the summary report instead of crashing; (c) cache resolved username→id pairs in the vault for 30 days to avoid repeated lookups.

---

---

## Part 2 — Spec

# FI-SOCIAL-SERVICE — Spec (product, v2)

> **Status: PROPOSED — operator review BEFORE any code. Each phase re-surfaced for consent.**
> **Scope: claude-soma PRODUCT component** (REVISES the VPS-local call of the 2026-06-09 draft *for this component only*). Rationale: this surface is pure, clean Graph-API integration with **no** Playwright/browser/scraping, so it follows the normal claude-soma product shape — code under `src/claude_soma/mcp_servers/social/`, committed systemd units, a `.mcp.json` stanza, pytest tests, public Caddy at `social.mayankgupta.in`.
> **Reuses verbatim from the prior draft** (FI-SOCIAL-SERVICE-PLAN-2026-06-09.md): the SQLite token-vault schema, the three-token-space OAuth exchange + refresh flows, the webhook HMAC contract, the per-platform adapter contract, and the FI-NO-POST-WITHOUT-APPROVAL gate. What changes here is the **packaging** (VPS-local op dir → product repo), the **paths** (`~/.config/social-manager` + `/var/lib/social-manager` → `/etc/claude-soma` + `/opt/claude-soma` product paths), the **process shape** (FastAPI subprocess managed by a `claude-soma-social.service` unit; MCP shim is an in-repo FastMCP server), and the addition of **inbound-DM auto-reply** and **IG Business Discovery** as first-class capabilities, plus a **product-owned draft+approval surface** (the legacy VPS engagement queue is leaving via FI-ENGAGEMENT-MIGRATE).
> **Verified Graph API facts** (from `meta-automation-setup.md` + `meta-walkthrough-live.md`): three independent token spaces (IG App ID `1662457504971901`, Threads App ID `1790145195303919`, FB App ID `1375342191160828`); IG `IG_USER_ID=26101391282871017` on `graph.instagram.com`; FB Page `1042685982267843` non-expiring; Threads `THREADS_USER_ID=36190611627220761`; Graph `v25.0`; container publish pattern; IG 50 posts/24h, Threads 250 posts + 1000 replies/24h, FB BUC; webhook GET `hub.challenge` echo + POST `X-Hub-Signature-256` HMAC; 24h standard messaging window for DMs; Business Discovery read-only by username. App stays Development + Unpublished + self-as-tester → no app review.

---

## 1. Goals & Non-Goals

### Goals

1. Ship, **as a committed claude-soma product component**, a single-process Python 3.12 FastAPI + APScheduler service (`claude-soma-social.service`) that owns all Meta Graph API interaction across the **three independent token spaces** — Instagram (Instagram-Login, `graph.instagram.com`), Threads (`graph.threads.net`), Facebook Page (`graph.facebook.com/v25.0`) — behind one clean `PlatformAdapter` protocol.
2. **OAuth + vault + automated refresh**: server-side code→short→long-lived exchange on `social.mayankgupta.in/oauth/callback`; a SQLite vault as the single runtime source of truth; daily refresh of IG/Threads (60-day, `≥24h`-old constraint) with a 7-day buffer; FB Page treated as non-expiring.
3. **Webhook receiver** for Meta comments/mentions/messages: GET `hub.challenge` verify + POST HMAC-SHA256 verify → durable queue → product-owned approval-gated **drafts**. Public-content comments/mentions **NEVER auto-post** (FI-NO-POST-WITHOUT-APPROVAL).
4. **Publish + engage API**: publish post / reel / story / carousel / trial-reel; reply to + moderate comments on **own** content; read insights — via per-platform adapters.
5. **Inbound-DM auto-reply** via the Messenger API (IG + FB Page): reply-only, inside the 24h standard messaging window, policy-compliant — with an explicit, per-thread **operator toggle** between auto-send and draft-gated (§11).
6. **IG Business Discovery** fetch of known pro accounts (read-only, API) feeding the same product draft surface for the fetch→draft→manual-post engagement assist.
7. A **committed provisioning doc** (`docs/social-provisioning.md`, §13) the **user** follows once to do the OAuth/token setup by hand; the service reads the resulting config and bootstraps its vault. No provisioning automation in code.
8. Expose product-shape integration: a `social` MCP server (`@mcp.tool()` + `main()`), a `.mcp.json` stdio stanza, committed systemd units installed via `deploy-systemd.sh`, and a Caddy block imported through `/etc/caddy/conf.d/*.caddyfile`.

### Non-Goals (STRICT EXCLUDE — these never enter the repo / product; they stay VPS-local / operational)

- **ANY Playwright / headless browser / scraping / DOM drivers.** This product is API-only.
- **VNC login, `pw-login.js`, manual cookie/session wrangling.** (The walkthrough's VNC-driven token capture was a one-time operational act; it is not productized — the user re-provisions by hand per §13.)
- **The X / LinkedIn engagement pipeline** (Playwright-based; owned by FI-ENGAGEMENT-MIGRATE, separately).
- **The read-only-Playwright IG/FB discovery fallback** (liking, following, follower-list browsing, comment-count reads on others' media) — operational. This product's Business Discovery returns **only** media list + captions, never engagement metrics, so it does not compete with that fallback (§9.6).
- No Anthropic API key; no committed secrets; no LLM calls in-service — **reply/draft text is operator/MCP-authored, never server-generated.**
- No Meta app review / Live mode: app stays Development + Unpublished + self-as-tester.
- No browser approval UI; approval is via MCP tools. No analytics dashboard (insights are read-only pass-through). No multi-tenancy (single account per platform). Outside-the-24h-window DM messaging (tagged/template) is out of scope.

---

## 2. Product Topology

### 2.1 Source layout (`src/claude_soma/mcp_servers/social/`)

```
src/claude_soma/mcp_servers/social/
  __init__.py
  server.py            # FastMCP("social"): @mcp.tool() shims + main(); thin httpx → 127.0.0.1:8800. NO Graph calls, NO secrets.
  app.py               # FastAPI app factory `app` (uvicorn target); wires routers + APScheduler lifespan.
  config.py            # load /etc/claude-soma/social.env ONCE; per-space App ID/Secret, WEBHOOK_VERIFY_TOKEN, bootstrap token triples. Held in memory; never written to vault.
  vault.py             # SQLite open/migrate (§10), WAL, 0600; single-writer transaction helper; bootstrap seeding.
  scheduler.py         # APScheduler in-process: token_refresh / webhook_drain / publish_drain / dm_window_sweeper / discovery_run.
  webhooks.py          # raw-body HMAC verify (§12) + hub.challenge; enqueue only.
  drafts.py            # product-side draft+approval state machine (§10.4, §10.5).
  routes/
    __init__.py
    oauth.py           # GET /oauth/callback (public) + POST /api/oauth/start (internal)
    tokens.py          # /api/tokens/{status,refresh,reauth-url}
    publish.py         # POST /api/publish/{platform}, /api/jobs*
    drafts.py          # /api/drafts* (engagement + dm + discovery)
    dm.py              # /api/dm/* (threads, auto-reply toggle, audit)
    discovery.py       # POST /api/discovery/run, GET /api/discovery/accounts
    insights.py        # GET /api/insights/{platform}/{object_id}
    health.py          # GET /health, GET /api/status
    webhooks.py        # GET+POST /api/webhooks/meta
  adapters/
    __init__.py
    base.py            # PlatformAdapter Protocol + Token / PublishJob / DiscoveryPost dataclasses (§5)
    instagram.py
    threads.py
    facebook_page.py
tests/mcp_servers/
  test_social_oauth.py        test_social_vault.py        test_social_webhooks.py
  test_social_adapters.py     test_social_publish.py      test_social_drafts.py
  test_social_dm.py           test_social_discovery.py    test_social_server.py
```

- **Single process.** Webhook verify/enqueue, publish/draft/dm/discovery routes, OAuth exchange, and all background tickers run in the one FastAPI app via an APScheduler thread under the app lifespan (MVP). The split to a dedicated `claude-soma-social-refresh.timer`-driven worker is documented as a Phase-2 migration; the committed `claude-soma-social-refresh.timer` (§2.2) provides a belt-and-suspenders **external** daily refresh trigger from day one.
- **Sync handlers** backed by thread-pool I/O (httpx), mirroring `claude-soma-api`. Graph calls run in worker ticks / thread pool, never on the webhook request path.
- Tests follow the repo conventions: pytest (`asyncio_mode=auto`), ruff line-length 100, mypy strict, and the subprocess/httpx try/except → `RuntimeError(name + last-500-chars)` pattern.

### 2.2 systemd units (committed in `systemd/`, synced by `deploy-systemd.sh`)

`systemd/claude-soma-social.service` (shape mirrors `claude-soma-api.service`):

```ini
# systemd/claude-soma-social.service
[Unit]
Description=Claude Soma social (Meta Graph API) service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/claude-soma
EnvironmentFile=/etc/claude-soma/social.env
Environment=PATH=/opt/claude-soma/.venv/bin:/usr/local/bin:/usr/bin:/bin
Environment=HERMES_SOCIAL_DB=/opt/claude-soma/social.sqlite
ExecStartPre=/usr/bin/install -d -o ubuntu -g ubuntu /var/log/claude-soma
ExecStart=/opt/claude-soma/.venv/bin/uvicorn claude_soma.mcp_servers.social.app:app \
    --host 127.0.0.1 --port 8800 --no-server-header
UMask=0077
Restart=always
RestartSec=5
StandardOutput=append:/var/log/claude-soma/social.log
StandardError=append:/var/log/claude-soma/social.err.log

[Install]
WantedBy=multi-user.target
```

`systemd/claude-soma-social-refresh.timer` + `.service` (external daily refresh trigger; mirrors `channel-clear` oneshot + `cache-refresh` timer pairing — and `deploy-systemd.sh` auto-restarts changed *timers* only when the sibling `.service` is present in DEST):

```ini
# systemd/claude-soma-social-refresh.timer
[Unit]
Description=Daily Meta token refresh sweep (IG + Threads)
[Timer]
OnCalendar=*-*-* 03:30:00
Persistent=true
Unit=claude-soma-social-refresh.service
[Install]
WantedBy=timers.target
```

```ini
# systemd/claude-soma-social-refresh.service
[Unit]
Description=Trigger Claude Soma social token refresh (all platforms)
[Service]
Type=oneshot
User=ubuntu
ExecStart=/usr/bin/curl -fsS -X POST http://127.0.0.1:8800/api/tokens/refresh -H 'content-type: application/json' -d '{"platform":"all"}'
```

> `claude-soma-social.service` is **not** the running bot (`claude-soma-channel.service` is the hard-excluded one in `deploy-systemd.sh`), so it is safe under `--restart-services`. The timer just pokes the in-process scheduler externally — the in-process `token_refresh` tick remains the primary path; the timer is the watchdog.

### 2.3 `.mcp.json` stanza (append at repo root, following the existing stdio block pattern)

```json
"social": {
  "type": "stdio",
  "command": "/opt/claude-soma/.venv/bin/python",
  "args": ["-m", "claude_soma.mcp_servers.social.server"],
  "env": {
    "HERMES_SOCIAL_BASE_URL": "http://127.0.0.1:8800"
  },
  "alwaysLoad": true
}
```

The MCP server (`server.py`) is a thin stdio FastMCP shim: each `@mcp.tool()` makes one `httpx` call to `HERMES_SOCIAL_BASE_URL` and returns the JSON. No Graph calls, no secrets, no business logic cross the shim boundary (mirrors how the prior draft's "MCP shim" related to its HTTP service, now collapsed into the product package).

### 2.4 Caddy (committed block, installed to `/etc/caddy/conf.d/social.caddyfile`; picked up by `import /etc/caddy/conf.d/*.caddyfile` in the repo Caddyfile)

```
# /etc/caddy/conf.d/social.caddyfile
social.mayankgupta.in {
    @public path /oauth/callback /api/webhooks/meta /health /api/status
    handle @public { reverse_proxy 127.0.0.1:8800 }
    handle { respond 404 }
    encode gzip zstd
    log { output file /var/log/caddy/social.access.log }
}
```

Exactly four public paths; everything else 404. `/api/status` is an optional public, secret-free health/expiry summary. All other routes (publish, drafts, dm, discovery, tokens, insights, oauth/start) are **internal**, reachable only on `127.0.0.1:8800` — never reverse-proxied; ufw allows inbound 443 only.

---

## 3. Public API Surface (Caddy-exposed on `social.mayankgupta.in`)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/oauth/callback` | Receives `?code=&state=` from an IG/Threads/FB authorize redirect. Validate single-use `state` (CSRF nonce minted by `/api/oauth/start`), resolve the platform from the state row, run the server-side code→short→long-lived exchange (§5), write the token to the vault, `302` to a minimal confirmation page. App secret never leaves the server / appears in any log. Invalid/used `state` → `400`. |
| `GET` | `/api/webhooks/meta` | Subscription verify. If `hub.mode=="subscribe"` AND `hub.verify_token == WEBHOOK_VERIFY_TOKEN` → `200 text/plain` echoing `hub.challenge` verbatim; else `403`. |
| `POST` | `/api/webhooks/meta` | Event delivery (IG + Threads + FB; comments, mentions, **messages**). Read raw bytes first; resolve platform from payload `object` (`instagram`/`threads`/`page`); compute `sha256=HMAC_SHA256(raw, app_secret_for_object)`; constant-time compare to `X-Hub-Signature-256` (§12). On pass, `INSERT OR IGNORE` into `webhooks_queue` (idempotent on Meta entry/change id); return `200` immediately. No Graph call, no draft, no DM send inline. |
| `GET` | `/health` | Liveness + per-platform `{platform, token_present, expires_at, days_to_expiry, refresh_ok}`. No token values. |
| `GET` | `/api/status` *(optional public)* | Same secret-free summary as `/health` plus draft/queue depths and DM-window counts, for the operator's calendar-reminder check (§ risk R1). |

---

## 4. Internal API Surface (localhost `127.0.0.1:8800` only; the MCP shim is the sole caller)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/oauth/start` | `{platform}` → mint single-use `state`, persist, return the adapter `authorize_url`. |
| `GET` | `/api/tokens/status` | All platforms: `issued_at`, `expires_at`, `last_refreshed`, `days_to_expiry`, `last_refresh_error`. Pre-publish health check. |
| `POST` | `/api/tokens/refresh` | `{platform|"all"}`. Forces refresh now (respects `≥24h`-old for IG/Threads; no-op for FB Page). Used by the in-process tick **and** the external timer. |
| `GET` | `/api/tokens/reauth-url` | `{platform}` → OAuth authorize URL for unrecoverable (past-refresh) tokens. |
| `POST` | `/api/publish/{platform}` | `{kind: image\|video\|reel\|story\|carousel\|trial_reel\|text, media_url?, caption?, children?, scheduled_at?}` → create container, enqueue `publish_jobs` row, return `{job_id, creation_id, status}`. |
| `GET` | `/api/jobs` · `GET /api/jobs/{job_id}` | Publish queue status + container-24h watch + webhook-queue depth. |
| `GET` | `/api/drafts` | List drafts; filter by `status` and by `source` (`webhook\|business_discovery\|dm`). |
| `GET` | `/api/drafts/{id}` | Full draft: source event, commenter, `target_id`, `source_text`, `proposed_text`, window-close ts (DM), status. |
| `POST` | `/api/drafts/{id}/approve` | `{reply_text?}` (operator may edit). `pending_approval → approved`; the relevant drain posts via the adapter. Double-post guard: refuse if same `target_id` actioned in the last 5 min. |
| `POST` | `/api/drafts/{id}/reject` | `→ rejected`; no API call. |
| `GET` | `/api/dm/threads` | List DM threads with `within_24h_window`, `window_closes_at`, `auto_reply_enabled`. |
| `POST` | `/api/dm/threads/{conversation_id}/auto-reply` | `{enabled: bool}` — per-thread operator toggle (§11). |
| `POST` | `/api/dm/threads/{conversation_id}/reply` | `{text, mode?: "auto"\|"draft"}` — author a DM reply; `mode` overrides the thread toggle for this one reply. |
| `GET` | `/api/dm/audit` | Recent DM sends (auto + manual) for the last N hours (`dm_audit_log`). |
| `POST` | `/api/discovery/run` | `{accounts: list[str\|int]}` — IG Business Discovery fetch → discovery drafts (§9.6). Returns `{success, failed, created_drafts, errors[]}`. |
| `GET` | `/api/discovery/accounts` | Returns the operator's monitored-accounts list (`/opt/claude-soma/social-discovery-accounts.txt`). |
| `GET` | `/api/insights/{platform}/{object_id}` | Read-only pass-through to the platform metrics endpoint. No write path reachable. |

> Internal routes need no in-app auth: bound to `127.0.0.1`, never reverse-proxied, `:8800` dropped by ufw.

### 4.1 MCP tools (`server.py`, 1:1 thin shims over the internal API)

`social_tokens_status`, `social_oauth_start`, `social_token_refresh`, `social_publish`, `social_job_status`, `social_jobs_list`, `social_drafts_list`, `social_draft_get`, `social_draft_approve`, `social_draft_reject`, `social_dm_threads`, `social_dm_set_auto_reply`, `social_dm_reply`, `social_dm_audit`, `social_discovery_run`, `social_discovery_accounts`, `social_insights`. Each is a `@mcp.tool()` that `httpx`-calls the matching route. Reply/draft text is always passed in by the operator/MCP client, never generated here.

---

## 5. Per-Platform Adapter Contract

One adapter per platform satisfies this protocol; all platform divergence is isolated here (reused from the prior draft, extended with `dm_reply` and `business_discovery`).

```python
class PlatformAdapter(Protocol):
    platform: str            # "instagram" | "threads" | "facebook_page"
    graph_host: str          # graph.instagram.com | graph.threads.net | graph.facebook.com
    api_version: str | None  # FB: "v25.0"; IG/Threads: None (host-versioned)

    def authorize_url(self, state: str) -> str: ...
    def exchange_code(self, code: str) -> Token: ...          # code → short → long-lived
    def refresh(self, token: Token) -> Token: ...             # raises if < 24h old (IG/Threads); no-op (FB)
    def publish(self, job: PublishJob) -> str: ...            # container → poll → publish; returns media_id
    def comment(self, target_id: str, text: str) -> str: ...  # reply to OWN comment/media; returns comment_id
    def insights(self, object_id: str, metrics: list[str]) -> dict: ...
    def dm_reply(self, conversation_id: str, text: str) -> str: ...           # Messenger API; IG+FB only
    def business_discovery(self, username: str) -> list[DiscoveryPost]: ...   # IG only; others raise NotSupported
```

```python
@dataclass
class Token:
    platform: str; account_id: str; token: str
    token_type: str            # "bearer" | "page_token"
    issued_at: int; expires_at: int | None; scopes: str   # expires_at None for FB Page

@dataclass
class PublishJob:
    job_id: int; platform: str
    kind: str                  # image|video|reel|story|carousel|trial_reel|text
    media_url: str | None; caption: str | None
    children: list[str] | None; scheduled_at: int | None

@dataclass
class DiscoveryPost:
    pro_account: str; media_id: str; media_type: str
    caption: str | None; permalink: str | None; timestamp: int
```

### 5.1 Instagram (Instagram-Login) — host `graph.instagram.com`, own `IG_APP_ID`/`IG_APP_SECRET`
- `authorize_url`: `instagram.com/oauth/authorize`, `response_type=code`, scopes `instagram_business_basic,instagram_business_content_publish,instagram_business_manage_comments,instagram_business_manage_messages,instagram_manage_insights`, `redirect_uri=https://social.mayankgupta.in/oauth/callback`, `state`.
- `exchange_code`: `POST api.instagram.com/oauth/access_token` (code→short) → `GET graph.instagram.com/access_token?grant_type=ig_exchange_token` (short→long, 60d).
- `refresh`: `GET /refresh_access_token?grant_type=ig_refresh_token`; **token must be ≥24h old**; returns `expires_in=5184000`.
- `publish`: `POST /{IG_USER_ID}/media` (container; `media_type=REELS|STORIES`, `media_product_type` for trial-reel) → poll `GET /{creation_id}?fields=status_code` until `FINISHED` (~30s, video) → `POST /{IG_USER_ID}/media_publish`. Cap 50/24h; check `/{IG_USER_ID}/content_publishing_limit`.
- `comment`: `POST /{comment_id}/replies` or `POST /{media_id}/comments`.
- `dm_reply`: Messenger API for Instagram — `POST /{IG_USER_ID}/messages` with `recipient` + `message`, scope `instagram_business_manage_messages`; only within 24h window (§11).
- `insights`: `GET /{media_id}/insights?metric=...`.
- `business_discovery`: `GET /{IG_USER_ID}/business_discovery?username={pro}&fields=username,media{id,caption,media_type,media_product_type,permalink,timestamp}` (read-only; returns `DiscoveryPost[]`).

### 5.2 Threads — host `graph.threads.net`, separate `THREADS_APP_ID`/`THREADS_APP_SECRET`
- `authorize_url`: `threads.net/oauth/authorize`, scopes `threads_basic,threads_content_publish,threads_manage_replies,threads_manage_insights`, same `redirect_uri`, `state`.
- `exchange_code`: `POST graph.threads.net/oauth/access_token` (code→short) → `GET /access_token?grant_type=th_exchange_token` (60d). `refresh`: `grant_type=th_refresh_token`, `≥24h`-old.
- `publish`: `POST /{THREADS_USER_ID}/threads` (`media_type=TEXT|IMAGE|VIDEO|CAROUSEL`; text shortcut `auto_publish_text=true`) → poll → `POST /{THREADS_USER_ID}/threads_publish`. Caps 250 posts + 1000 replies/24h.
- `comment`: reply via `/threads` with `reply_to_id` then publish (own-thread replies). `insights`: `GET /{thread_id}/insights`.
- `dm_reply`: **NotSupported** (Threads DMs are not in the API). `business_discovery`: **NotSupported**.

### 5.3 Facebook Page — host `graph.facebook.com/v25.0`, FB app `META_APP_ID`/`META_APP_SECRET`; Page token **non-expiring**
- `exchange_code`: code → long-lived user token (`fb_exchange_token`) → `GET /me/accounts` (or `debug_token` `granular_scopes.target_ids` → `GET /{page_id}?fields=access_token` when `/me/accounts` is empty for BM-owned Pages) → Page token. Scopes `pages_show_list,pages_read_engagement,pages_manage_posts,pages_manage_engagement,pages_manage_metadata,pages_read_user_content,pages_messaging`.
- `refresh`: **no-op** (non-expiring); scheduler logs a monthly liveness read.
- `publish`: `POST /{FB_PAGE_ID}/photos`|`/feed` (`scheduled_publish_time` supported natively; resumable upload for video). No daily ceiling (BUC).
- `comment`: `POST /{object_id}/comments`. `dm_reply`: Messenger API — `POST /{FB_PAGE_ID}/messages`, scope `pages_messaging`, within 24h window. `insights`: `GET /{post_id}/insights`. `business_discovery`: **NotSupported**.

---

## 6. SQLite Vault Schema

Path `/opt/claude-soma/social.sqlite` (overridable via `HERMES_SOCIAL_DB`), `0600`, `ubuntu`-owned, `PRAGMA journal_mode=WAL`. Tables `tokens`, `token_refresh_log`, `webhooks_queue`, `drafts`, `publish_jobs`, `audit_log`, `oauth_states` are reused verbatim from the prior draft (§6 there); this version extends `drafts` with `source`/`source_meta` and adds `dm_threads`, `dm_messages`, `dm_audit_log`, and `discovery_accounts`. Full DDL:

```sql
-- Live tokens (single runtime source of truth). Reused from prior draft.
CREATE TABLE tokens (
  platform TEXT NOT NULL, account_id TEXT NOT NULL, token TEXT NOT NULL,
  token_type TEXT, issued_at INTEGER NOT NULL, expires_at INTEGER,    -- NULL = facebook_page
  last_refreshed INTEGER, scopes TEXT, PRIMARY KEY (platform, account_id));

CREATE TABLE token_refresh_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL,
  attempted_at INTEGER NOT NULL, ok INTEGER NOT NULL, attempt_num INTEGER NOT NULL,
  error_msg TEXT, new_expires_at INTEGER);

CREATE TABLE webhooks_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT, idempotency_key TEXT NOT NULL UNIQUE,
  platform TEXT NOT NULL, event_type TEXT NOT NULL,    -- comment|mention|message
  object_id TEXT, payload_json TEXT NOT NULL,          -- verbatim body (audit + reprocess)
  enqueued_at INTEGER NOT NULL, processed_at INTEGER,
  status TEXT NOT NULL DEFAULT 'queued',               -- queued|processed|deduplicated|parse_error
  retry_count INTEGER NOT NULL DEFAULT 0);

-- Unified product draft surface (engagement + discovery + dm). EXTENDED.
CREATE TABLE drafts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,                  -- 'webhook' | 'business_discovery' | 'dm'
  source_meta TEXT,                      -- json: webhook_id / pro_account / conversation_id, window_closes_at
  webhook_id INTEGER REFERENCES webhooks_queue(id),
  platform TEXT NOT NULL,
  target_id TEXT NOT NULL,               -- comment_id/media_id/conversation_id to act on
  commenter TEXT, source_text TEXT, proposed_text TEXT,
  status TEXT NOT NULL DEFAULT 'pending_approval',
     -- pending_approval|approved|published|rejected|failed|failed_token_expired
     -- |failed_24h_window_expired|discovery_expired|discovery_failed_token_expired
  created_at INTEGER NOT NULL, decided_at INTEGER, published_id TEXT, error_msg TEXT);

CREATE TABLE publish_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL, kind TEXT NOT NULL,
  media_url TEXT, caption TEXT, children_json TEXT, creation_id TEXT,
  container_created_at INTEGER, scheduled_at INTEGER,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending|container_created|published|failed|expired
  media_id TEXT, created_at INTEGER NOT NULL, error_msg TEXT);

-- Inbound DM threads + messages + audit (NEW).
CREATE TABLE dm_threads (
  conversation_id TEXT NOT NULL, platform TEXT NOT NULL,   -- instagram | facebook_page
  sender_id TEXT NOT NULL, last_message_ts INTEGER NOT NULL,
  within_24h_window INTEGER NOT NULL DEFAULT 1, window_closes_at INTEGER NOT NULL,
  auto_reply_enabled INTEGER NOT NULL DEFAULT 0,           -- per-thread toggle (§11)
  PRIMARY KEY (platform, conversation_id));
CREATE TABLE dm_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL, conversation_id TEXT NOT NULL,
  message_id TEXT UNIQUE, direction TEXT NOT NULL,         -- inbound | outbound
  text TEXT, ts INTEGER NOT NULL);
CREATE TABLE dm_audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, platform TEXT NOT NULL,
  conversation_id TEXT NOT NULL, sender_id TEXT, mode TEXT NOT NULL,  -- auto | manual
  outcome TEXT NOT NULL, message_id TEXT, detail TEXT);

-- Operator's monitored discovery accounts (mirror of the txt file; NEW).
CREATE TABLE discovery_accounts (
  username TEXT PRIMARY KEY, resolved_user_id TEXT, resolved_at INTEGER, last_run_at INTEGER);

-- Append-only security/action trail (no token values, no bodies). Reused.
CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, actor TEXT,  -- oauth|worker|mcp|webhook
  action TEXT NOT NULL, platform TEXT, outcome TEXT, detail TEXT);        -- redacted summary only

-- OAuth CSRF nonces. Reused.
CREATE TABLE oauth_states (
  state TEXT PRIMARY KEY, platform TEXT NOT NULL, created_at INTEGER NOT NULL, consumed_at INTEGER);
```

Bootstrap (idempotent, on startup): for each platform with no `tokens` row, INSERT from the config file (§13) — IG/Threads `issued_at` from config (`2026-06-09`), `expires_at = issued_at + 5184000`; FB Page `expires_at = NULL`. If a row exists, **do nothing** — the vault, not the config, is authoritative after first seed. App ID/Secret are held in memory, **never** written to the vault.

---

## 7. Webhook Contract (reused verbatim from prior draft; HMAC unchanged)

**Subscription verify — `GET /api/webhooks/meta`:** `hub.mode=="subscribe"` AND `hub.verify_token == WEBHOOK_VERIFY_TOKEN` → `200 text/plain` echoing `hub.challenge`; else `403`.

**Event delivery — `POST /api/webhooks/meta`:**
1. Read **raw bytes** before any parsing.
2. Determine platform from payload `object` (`instagram`/`threads`/`page`); select that space's app secret. Unknown `object` → fail closed (`403`), never default to a secret.
3. `expected = "sha256=" + HMAC_SHA256(raw_body, app_secret)`; `hmac.compare_digest` vs `X-Hub-Signature-256`. Missing/mismatch → `403` + `audit_log(action='webhook_verify', outcome='denied')`. Parsed body never touched before this passes.
4. Extract `idempotency_key` (Meta entry/change id); `INSERT OR IGNORE` into `webhooks_queue`; return `200` even if the row already exists.
5. Respond `200` fast; **no Graph call / draft / DM send inline**.
6. `webhook_drain` (every 30s) routes by `event_type`: `comment`/`mention` → product engagement draft (§8); `message` → DM intake (§11). Parse failures → `parse_error`, row retained.

---

## 8. Product-side Draft + Approval Surface (engagement; the legacy queue is leaving)

**Design tension resolved.** FI-ENGAGEMENT-MIGRATE is moving the *overall* (X/LinkedIn, Playwright) engagement pipeline **out** to VPS-local. Therefore this product **owns its own** webhook-sourced draft+approval surface and **must not depend** on the VPS engagement queue. The surface is the unified `drafts` table (§6) plus `/api/drafts*` (§4), exposed via MCP (`social_drafts_list/get/approve/reject`).

- **Intake (webhook → draft):** `webhook_drain` converts each `comment`/`mention` event into a `drafts` row, `source='webhook'`, `status='pending_approval'`, populating `platform/target_id/commenter/source_text`; `proposed_text` empty (operator authors it on approve). **There is no code path from webhook intake to a posted reply** — structurally enforced.
- **Approve → post:** `POST /api/drafts/{id}/approve {reply_text?}` → `approved`; `publish_drain` posts via adapter `comment()`, records `published_id`. Double-post guard (5-min window per `target_id`). Token-expired at post → `failed_token_expired` + alert sentinel. `reject` → no API call.
- **Orchestrator-agnostic by contract.** The MCP tools return raw draft *state* and accept *decisions*; they encode **no** approval-orchestration policy. FI-ENGAGEMENT-MIGRATE (VPS-local) can call the same `social_drafts_list`/`social_draft_approve` tools to surface Meta drafts alongside X/LinkedIn in its own review gate — **no approval logic is re-implemented**, and this product keeps working standalone if that pipeline is absent.
- **Source-mixed surface.** `/api/drafts` filters by `source` (`webhook|business_discovery|dm`) so the operator sees all engagement in one place; the approve→post path differs per source (engagement → `comment()`, discovery → manual post assist, DM → `dm_reply()` within window).

---

## 9. Publish, Engage, Insights, Discovery

### 9.1–9.4 Publish / comment / insights
As §5 + prior-draft §5: container→poll→publish queue (`publish_jobs`), 18h container cutoff (6h margin under Meta's 24h), per-platform rate ceilings (IG 50/24h, Threads 250+1000/24h, FB BUC) — over-limit jobs stay `pending`, never error. `comment()` reachable only from an `approved` draft. `insights()` read-only pass-through.

### 9.6 IG Business Discovery (fetch → draft → manual-post assist; DRAFT side only)
- **API-only, read-only.** `POST /api/discovery/run {accounts:[str|int]}` → adapter `business_discovery(username)` (IG only) → each returned `DiscoveryPost` becomes a `drafts` row: `source='business_discovery'`, `status='pending_approval'`, `commenter=<pro_account>`, `source_text=<caption>`, `target_id=<media_id>`, `proposed_text=''` (operator authors engagement intent; nothing auto-proposed).
- **Operator-driven account list.** Monitored accounts come from `/opt/claude-soma/social-discovery-accounts.txt` (line-separated, operator-owned) or the `accounts` arg — never a hardcoded list. Mirrored into `discovery_accounts` for username→id caching (30-day) and `last_run_at`.
- **Scheduled, not continuous.** `discovery_run` is an explicit MCP/operator trigger (or one daily APScheduler tick, ~02:00), batching 1–2 accounts to bound BUC spend (each call costs impressions against the IG quota; surfaced via `/health` + `content_publishing_limit`).
- **Pre-flight token + resolution.** If the IG token expires within 24h, refuse the run and alert (refresh first). Accept username or numeric id; resolve username→id with a pre-flight `business_discovery(...&fields=id)`; on 404/private/changed, record the error in the run summary, don't crash. Per-account try/except → on 401 mark that account's drafts `discovery_failed_token_expired` and continue. Returns `{success, failed, created_drafts, errors[]}`.
- **Boundary vs the operational Playwright fallback (STRICT).** This product returns **only** media list + captions — **no** engagement metrics (like/comment/follower counts), **no** follow/like/comment-on-others. Those are the operational read-only-Playwright fallback's job and stay VPS-local. Stale `business_discovery` drafts auto-expire to `discovery_expired` after 7 days (configurable; audit-retained, never deleted).

---

## 10. (Schema is §6.) Draft state machine summary

`pending_approval → {approved → published | rejected | failed | failed_token_expired | failed_24h_window_expired (dm) | discovery_expired | discovery_failed_token_expired}`. Only `approve` (or an auto-reply-enabled DM within window, §11) ever drives a post. No intake path posts.

---

## 11. Inbound-DM Auto-Reply Policy + Operator Toggle

**The one place auto-send is allowed — and it is an explicit operator decision, not a default.**

- **Why DMs differ from §8.** A public comment/mention reply is public content → FI-NO-POST-WITHOUT-APPROVAL → always draft-gated. A DM reply is a **1:1, policy-compliant** message that Meta explicitly permits **inside the 24h standard messaging window**. So the never-auto-POST principle is preserved (no public post is ever auto-published), while DM replies are allowed to auto-send **if and only if the operator opts in per-thread**.
- **Intake.** A `message` webhook → `webhook_drain` upserts `dm_threads` (`window_closes_at = inbound_ts + 86400`, `within_24h_window`) and appends to `dm_messages` (`direction='inbound'`). It does **not** send anything.
- **Default = draft-gated.** A new thread has `auto_reply_enabled=0`. An operator reply (`social_dm_reply` / `POST /api/dm/threads/{id}/reply`) creates a `drafts` row `source='dm'` `pending_approval`; it posts only on `approve`, via `dm_reply()`, **iff** still inside the window.
- **Opt-in auto-reply (per-thread).** `social_dm_set_auto_reply {conversation_id, enabled:true}` sets `auto_reply_enabled=1` for that thread only — **no global switch**. When enabled, an operator-authored reply on that thread (`mode` defaulting to the thread toggle) is sent **immediately** via `dm_reply()` within the window, and recorded in `dm_audit_log` (`mode='auto'`). A single reply may override the thread default via `mode:"draft"|"auto"`.
- **Window enforcement (hard).** Every send (auto or approved) re-checks `now - inbound_ts`. `>23.5h` → warn but allow (30-min buffer); `>24h` → refuse, draft `failed_24h_window_expired` + alert. `dm_window_sweeper` (periodic) flips `within_24h_window=0` and surfaces window-close ts in draft/thread responses. Outside the window: out of scope (no tagged/template path).
- **Abuse guards.** Per-thread rate limit (≥60s between auto-sends) prevents fire-and-forget loops; every auto-send is `dm_audit_log`-recorded; `social_dm_audit` lists the last N hours. Token-expired at send → `failed_token_expired` + alert (same path as §8). Threads has no DM API → DM applies to IG + FB Page only.

---

## 12. Security Model

1. **Localhost bind; 4 public paths.** Service binds `127.0.0.1:8800`, never `0.0.0.0`. ufw inbound `443` (Caddy) only; `:8800` unreachable externally. Caddy whitelists `/oauth/callback`, `/api/webhooks/meta`, `/health`, `/api/status`; all else `404`. Internal API needs no in-app auth.
2. **Webhook authenticity.** Mandatory `X-Hub-Signature-256` HMAC over **raw bytes before parsing**, constant-time `hmac.compare_digest`, per-platform secret by `object`; unknown object fails closed; failures `403` + audited. Optional Meta-IP-range filter at Caddy.
3. **OAuth CSRF + server-side secret.** Single-use `state` nonce; app secret never leaves the server / never logged. Three **independent** App ID/Secret pairs; bootstrap/exchange/refresh code never cross-uses a secret (config loader asserts all three present at startup).
4. **No-post-without-approval (structural).** Public comments/mentions can only become `pending_approval` drafts; the only posters are the `approved`-draft drains and the operator-opted-in DM auto-reply within window. Discovery proposes nothing.
5. **Secret hygiene.** Structured logs; never log token values, `Authorization`, or raw webhook/DM bodies; `audit_log.detail`/`dm_audit_log.detail` carry redacted summaries only. `UMask=0077`. Token-operation error responses are structured (`{status:'token_expired', platform, refresh_required:true}`) with no token in the body.
6. **File permissions / product paths.** Config `/etc/claude-soma/social.env` and vault `/opt/claude-soma/social.sqlite` are `0600` ubuntu-owned; **neither is committed** (`.gitignore`). Vault is read once-bootstrapped then owned by the service; config never re-read after bootstrap.
7. **Token lifecycle safety.** Expired tokens are retained (audit), never auto-deleted; the service refuses to *use* an expired token and surfaces a re-auth URL. 5-min double-post guard; 24h DM-window hard check.

---

## 13. Provisioning-Doc Contract + Config File (key deliverable)

**Deliverable: a committed `docs/social-provisioning.md`** — step-by-step instructions for the **user** to do the one-time OAuth/token setup **by hand** (no automation, no VNC, no Playwright in the product). The service reads the resulting config **once** at startup, seeds the vault, and owns all token mutation thereafter.

The doc covers, end to end:
1. **Prerequisites** — IG converted to Business/Creator; optional linked FB Page; admin of the Page.
2. **Meta app(s)** — App IDs/Secrets for the three spaces (IG `1662457504971901`, Threads `1790145195303919`, FB `1375342191160828`), Development + Unpublished + self-as-tester (no app review).
3. **Three token flows** (verbatim endpoints from the runbooks), each with exact scopes:
   - **Instagram-Login:** `instagram.com/oauth/authorize` → `api.instagram.com/oauth/access_token` → `graph.instagram.com/access_token?grant_type=ig_exchange_token` (60d).
   - **Threads:** `threads.net/oauth/authorize` → `graph.threads.net/.../access_token?grant_type=th_exchange_token` (60d).
   - **Facebook Page:** Graph API Explorer / FLB code flow → long-lived user token → `/me/accounts` (or `debug_token` target_ids) → non-expiring Page token.
4. **Redirect URI** — register **exactly** `https://social.mayankgupta.in/oauth/callback` (no trailing slash) in each space's OAuth settings. The doc flags the IG-vs-Threads/FB trailing-slash gotcha (R2) and that this is *additive* to any existing `files.*` entry.
5. **Webhook** — set the same `WEBHOOK_VERIFY_TOKEN` value in each app's webhook config and in the config file.
6. **Refresh/expiry guidance** — IG/Threads 60d (service refreshes on day ~50, ≥24h-old constraint); FB Page non-expiring. Day-55 calendar reminder to glance at `/health`; if refresh fails 3×, the service writes an alert sentinel + surfaces it.
7. **Install** — run `deploy-systemd.sh` (units), drop the Caddy block, create `/etc/claude-soma/social.env` `0600`, start the service.

**Config file the service reads: `/etc/claude-soma/social.env`** (loaded by `EnvironmentFile=` and `config.py`; `0600` ubuntu-owned; **`.gitignore`d, never committed**). A committed **template** `config/social.env.example` documents the keys (mirroring `secrets.env.example`):

```
# /etc/claude-soma/social.env  (0600, NEVER committed)  — read ONCE at startup
META_APP_ID=            META_APP_SECRET=                 # Facebook/Page app space
IG_APP_ID=              IG_APP_SECRET=                   # Instagram-Login app space
THREADS_APP_ID=         THREADS_APP_SECRET=              # Threads app space
WEBHOOK_VERIFY_TOKEN=
IG_LONG_LIVED_TOKEN=    IG_USER_ID=                      # issued 2026-06-09, exp 2026-08-07
THREADS_LONG_LIVED_TOKEN= THREADS_USER_ID=              # issued 2026-06-09, exp 2026-08-08
FB_PAGE_ACCESS_TOKEN=   FB_PAGE_ID=                      # non-expiring
```

The service reads this file exactly once on startup, seeds the vault if a platform row is absent (§6), then never re-reads it; the vault is the runtime source of truth.

---

## 14. Acceptance Criteria (per capability)

**OAuth / bootstrap**
- [ ] Empty-vault first start seeds one `tokens` row per platform from `/etc/claude-soma/social.env` (IG/Threads 60d `expires_at`; FB `NULL`); restart does **not** re-seed/overwrite a refreshed token.
- [ ] `GET /oauth/callback` with valid single-use `state` runs the full server-side exchange and writes the vault; app secret never in any response/log; invalid/used `state` → `400`.
- [ ] App ID/Secret present in memory, **absent** from the vault.

**Token vault & refresh**
- [ ] In-process `token_refresh` (daily) **and** the external `claude-soma-social-refresh.timer` both refresh IG+Threads only when `expires_at-now<7d` AND `now-issued_at≥86400`; never FB Page. Success = atomic WAL update + `ok=1` log row.
- [ ] 3 consecutive failures (≤15min) or past-expiry → alert sentinel + `last_refresh_error` surfaced; expired token retained, never zeroed.
- [ ] `/health` + `/api/tokens/status` report correct `days_to_expiry` per platform, no token leakage.

**Webhooks**
- [ ] `GET /api/webhooks/meta` echoes `hub.challenge` only on a matching `hub.verify_token`; else `403`.
- [ ] `POST` → `403` for missing/byte-flipped `X-Hub-Signature-256`, `200` for valid; signature computed on raw bytes before parsing; all three `object` types exercised; unknown `object` fails closed.
- [ ] Valid event persisted to `webhooks_queue` before processing; redelivery (same Meta id) creates no second row and still `200`.

**Publish**
- [ ] `POST /api/publish/{platform}` creates a container, stores `creation_id`+`container_created_at`, publishes on next `publish_drain` tick (no `scheduled_at`), returns a `media_id`; reel/story/carousel/trial-reel kinds route correctly.
- [ ] Container >18h → `expired`, surfaced in `/api/jobs`; ≤18h publishes. IG 50/24h, Threads 250+1000/24h ceilings hold; over-limit jobs stay `pending`, don't error.

**Engagement / approval (FI-NO-POST-WITHOUT-APPROVAL)**
- [ ] No webhook-sourced reply is ever posted without a prior `approve`; static + runtime proof there is **no** intake→post path.
- [ ] `approve` posts the operator-edited reply via `comment()`, records `published_id`; `reject` makes no API call; 5-min double-post guard refuses repeats; token-expired → `failed_token_expired` + sentinel.
- [ ] `/api/drafts` filters by `source` and `status`; MCP `social_draft_*` round-trip; the surface works **standalone** (no dependency on the VPS engagement queue) and is callable by FI-ENGAGEMENT-MIGRATE without re-implementing approval logic.

**Inbound DM**
- [ ] New DM thread defaults `auto_reply_enabled=0` (draft-gated); `message` intake never sends.
- [ ] With per-thread auto-reply ON, an operator-authored reply within the window sends immediately via `dm_reply()` and is `dm_audit_log`-recorded (`mode='auto'`); a per-reply `mode` override works; no global auto-reply switch exists.
- [ ] Send at `>24h` is refused → `failed_24h_window_expired` + alert; `>23.5h` warns but allows; window-close ts surfaced in responses; ≥60s/thread rate limit holds; Threads has no DM path.

**IG Business Discovery**
- [ ] `POST /api/discovery/run` creates `source='business_discovery'` `pending_approval` drafts (caption + media id only, **no** engagement metrics), `proposed_text` empty; pre-flight refuses if IG token <24h to expiry; per-account 401 → `discovery_failed_token_expired` + continue; username→id resolution failure recorded in the summary, no crash; returns `{success,failed,created_drafts,errors[]}`.
- [ ] Stale discovery drafts auto-expire to `discovery_expired` after 7d (retained). `business_discovery()` raises NotSupported for Threads/FB.

**Insights**
- [ ] `GET /api/insights/{platform}/{object_id}` returns metrics read-only; no write path reachable.

**Product packaging / deployment**
- [ ] Code under `src/claude_soma/mcp_servers/social/`; `server.py` exposes `@mcp.tool()` shims + `main()`; tests under `tests/mcp_servers/test_social_*.py` pass with ruff(100)/mypy(strict)/pytest.
- [ ] `.mcp.json` has the `social` stdio stanza; `claude-soma-social.service` + `claude-soma-social-refresh.{timer,service}` are committed in `systemd/` and install via `deploy-systemd.sh` (service binds `127.0.0.1:8800`, survives restart from the vault; timer auto-restart-safe).
- [ ] Caddy `social.mayankgupta.in` block exposes exactly the 4 public paths; `:8800` unreachable externally; `docs/social-provisioning.md` + `config/social.env.example` committed; `/etc/claude-soma/social.env` and `/opt/claude-soma/social.sqlite` are `.gitignore`d and never committed.
- [ ] **No Playwright/VNC/scraping** anywhere in the component; no Anthropic key, no committed secrets; sole-author commits, no emoji.

---

I've completed the spec. It's returned above as the deliverable (per the instruction not to write report `.md` files). Key grounding decisions and the reusable contracts I carried forward:

- **Reused verbatim from the prior draft** (`/home/ubuntu/projects/social-manager/planning/FI-SOCIAL-SERVICE-PLAN-2026-06-09.md`): the SQLite vault schema (tokens / token_refresh_log / webhooks_queue / drafts / publish_jobs / audit_log / oauth_states), the three-token-space OAuth exchange + refresh flows, the HMAC webhook contract, the `PlatformAdapter` protocol, and the FI-NO-POST gate.
- **Re-packaged to product shape** per claude-soma conventions verified live in the repo: FastMCP `@mcp.tool()` + `main()` (`voice_stt/server.py`), the `.mcp.json` stdio stanza pattern, `claude-soma-<role>.service` (mirroring `claude-soma-api.service`) + timer/oneshot pairing (mirroring `cache-refresh`/`channel-clear`), `deploy-systemd.sh` sync semantics (timer auto-restart needs sibling service present; channel.service is the only hard-excluded unit), and the Caddy `import /etc/caddy/conf.d/*.caddyfile` mechanism. Paths moved to product locations (`/opt/claude-soma/social.sqlite`, `/etc/claude-soma/social.env`, port `8800`, `HERMES_SOCIAL_*` env prefix).
- **Integrated all five brainstorm dimensions**: product-packaging (single FastAPI+APScheduler), provisioning-doc (`docs/social-provisioning.md` + `config/social.env.example`), inbound-dm-reply (per-thread operator toggle, 24h-window hard enforcement), business-discovery (read-only, draft-only, metrics-free to respect the Playwright boundary), and the **product-owned draft+approval surface** that resolves the FI-ENGAGEMENT-MIGRATE tension (orchestrator-agnostic, standalone, no dependency on the departing VPS queue).

The spec is decisive and complete across every requested section: goals/non-goals (non-goals enumerate the STRICT EXCLUDE items), topology (src/systemd/.mcp.json/Caddy), public+internal+MCP API surfaces, the extended adapter contract, the SQLite schema, the webhook contract, the engagement draft surface, the DM auto-reply policy+toggle, the provisioning-doc + config contract, secrets/file layout, security model, and per-capability acceptance criteria.

---

## Part 3 — Implementation Plan

# FI-SOCIAL-SERVICE — Implementation Plan (product, v2)

> **Status: PROPOSED — operator review REQUIRED before any code.**
> **The first commit lands only after the operator reviews and consents to this plan.** Each of the three phases below is independently re-surfaced for operator review/consent *before* its code is written — approval of this plan is approval to *begin P1*, not a blanket approval of P2/P3.
> **Implementation cadence:** every task is executed by a Sonnet subagent with `--effort max` + the `sequential-thinking` MCP, following the repo's subagent-driven loop (implementer → spec-compliance reviewer → code-quality reviewer → fix loop → mark complete → next). Per CLAUDE.md the Agent *dispatch* model is `opus`; the operator's standing instruction for *this workstream's implementation* is Sonnet + max effort — honored here. Sole-author commits, no `Co-Authored-By`, no emoji, commit only when asked, push after each approved task.
> **Scope anchor:** this implements the v2 spec (product packaging) verbatim. Paths/units/Caddy/.mcp.json shapes below were verified live against the repo (`systemd/claude-soma-api.service`, `voice_stt/server.py`, `.mcp.json`, `scripts/deploy-systemd.sh`, `Caddyfile` `import /etc/caddy/conf.d/*.caddyfile`, `caddy/files.caddyfile`).

---

## Cross-phase ground rules (apply to every phase)

- **Code style gate (every task):** Python 3.12, `ruff` line-length 100, `mypy --strict`, `pytest` (`asyncio_mode=auto`). Subprocess/httpx I/O wrapped in `try/except` → `RuntimeError(name + last-500-chars-of-stderr)`, always `timeout=N`. No emoji anywhere. Default to no comments (only when *why* is non-obvious).
- **Secrets gate (every task):** No Anthropic API key. No committed secrets. `/etc/claude-soma/social.env` and `/opt/claude-soma/social.sqlite` are `.gitignore`d and never committed. App ID/Secret held in memory only, never written to vault, never logged.
- **No-Playwright gate (every task):** a CI/test assertion greps the `social/` tree for `playwright|selenium|webdriver|puppeteer|VNC|pw-login` and fails the build if any appears. This is the structural guarantee the component is API-only.
- **Process/port:** single FastAPI app on `uvicorn ... --host 127.0.0.1 --port 8800`; MCP shim (`server.py`) is stdio FastMCP, `httpx`-only, talks to `HERMES_SOCIAL_BASE_URL=http://127.0.0.1:8800`.
- **Env prefix:** `HERMES_SOCIAL_*` (matches the repo's stable `HERMES_*` interface-contract convention).
- **Per-phase review/consent:** before writing any code for a phase, the phase's step list + file manifest is re-surfaced to the operator. No code starts without that consent.

---

## Phase P1 — OAuth + SQLite token vault + automated refresh + the committed provisioning doc

**Goal:** stand up the single FastAPI process with a working OAuth callback, a `0600` SQLite vault that bootstraps from the user-provisioned config, and automated refresh (in-process tick + external timer). **Ship `docs/social-provisioning.md` first in this phase** so the user can self-provision (create Meta apps, set scopes/redirect URIs, generate the three long-lived tokens, populate `/etc/claude-soma/social.env`) *before* P1 code is exercised end to end.

### Ordered steps

1. **P1.0 — Provisioning doc + config template (ships first, no code prerequisite).**
   Write `docs/social-provisioning.md` (the §13 contract): prerequisites; the three Meta app spaces (IG `1662457504971901`, Threads `1790145195303919`, FB `1375342191160828`), Development + Unpublished + self-as-tester; the three token flows with exact endpoints + scopes; register redirect URI **exactly** `https://social.mayankgupta.in/oauth/callback` (flag the IG-vs-Threads/FB trailing-slash gotcha, additive to any existing `files.*` host); the shared `WEBHOOK_VERIFY_TOKEN`; refresh/expiry guidance (IG/Threads 60d, FB Page non-expiring, day-55 calendar reminder to glance at `/health`); install steps. Write the committed template `config/social.env.example` mirroring `secrets.env.example` (all keys, no values).
2. **P1.1 — Package skeleton + config loader + no-Playwright guard test.**
   Create `src/claude_soma/mcp_servers/social/` with `__init__.py`, `config.py` (load `/etc/claude-soma/social.env` once; assert all three App ID/Secret pairs + `WEBHOOK_VERIFY_TOKEN` present at startup; held in memory). Add the no-Playwright grep test. Add `httpx`, `fastapi`, `uvicorn`, `apscheduler` to `pyproject.toml` only if not already present (discuss any new dep per CLAUDE.md before adding).
3. **P1.2 — Vault (`vault.py`) + schema migration.**
   SQLite at `/opt/claude-soma/social.sqlite` (override `HERMES_SOCIAL_DB`), `PRAGMA journal_mode=WAL`, `0600` ubuntu-owned, single-writer transaction helper. Create the P1-relevant tables (`tokens`, `token_refresh_log`, `oauth_states`, `audit_log`) per §6 (full schema created now is fine; P2/P3 tables can be created here too since DDL is idempotent). Idempotent bootstrap-seed from config (IG/Threads `issued_at` from config, `expires_at = issued_at + 5184000`; FB `expires_at = NULL`); if a row exists, do nothing — vault is authoritative after first seed.
4. **P1.3 — Adapter OAuth surface (`adapters/base.py` + the three adapters, OAuth methods only).**
   Define the `PlatformAdapter` Protocol + `Token`/`PublishJob`/`DiscoveryPost` dataclasses (§5). Implement `authorize_url`, `exchange_code`, `refresh` for `instagram.py` (IG-Login, `graph.instagram.com`), `threads.py` (`graph.threads.net`), `facebook_page.py` (`graph.facebook.com/v25.0`, refresh = no-op). Publish/comment/insights/dm/discovery methods stubbed (`raise NotImplementedError`) until P2/P3.
5. **P1.4 — FastAPI app + OAuth + token routes + health.**
   `app.py` app factory wiring routers + APScheduler lifespan; `routes/oauth.py` (`GET /oauth/callback` validating single-use `state`, running code→short→long-lived exchange, writing vault, `302` to a minimal confirmation; `POST /api/oauth/start` minting the CSRF nonce), `routes/tokens.py` (`/api/tokens/{status,refresh,reauth-url}`), `routes/health.py` (`/health`, `/api/status` — secret-free per-platform summary).
6. **P1.5 — Scheduler refresh tick (`scheduler.py`).**
   APScheduler `token_refresh` (daily): refresh IG+Threads only when `expires_at-now < 7d` AND `now-issued_at ≥ 86400`; never FB Page. Atomic WAL update + `token_refresh_log` row. 3 consecutive failures (≤15min) or past-expiry → alert sentinel + surface `last_refresh_error`; expired token retained, never zeroed.
7. **P1.6 — systemd units (committed).**
   `systemd/claude-soma-social.service` (mirrors `claude-soma-api.service`: `Type=simple`, `User=ubuntu`, `WorkingDirectory=/opt/claude-soma`, `EnvironmentFile=/etc/claude-soma/social.env`, `Environment=HERMES_SOCIAL_DB=/opt/claude-soma/social.sqlite`, `ExecStartPre=install -d /var/log/claude-soma`, `ExecStart=.venv/bin/uvicorn claude_soma.mcp_servers.social.app:app --host 127.0.0.1 --port 8800 --no-server-header`, `UMask=0077`, `Restart=always`, logs to `/var/log/claude-soma/social.{log,err.log}`). Plus the external watchdog pair `systemd/claude-soma-social-refresh.timer` (`OnCalendar=*-*-* 03:30:00`, `Persistent=true`) + `.service` (`Type=oneshot` `curl -fsS -X POST http://127.0.0.1:8800/api/tokens/refresh -d '{"platform":"all"}'`).
8. **P1.7 — Caddy block (committed).**
   `caddy/social.caddyfile`: `social.mayankgupta.in` exposing exactly `/oauth/callback`, `/api/webhooks/meta`, `/health`, `/api/status` → `reverse_proxy 127.0.0.1:8800`; everything else `404`. (`/api/webhooks/meta` is wired in P3 but the public path is whitelisted now so the host is stable.) Picked up by the existing `import /etc/caddy/conf.d/*.caddyfile`; install to `/etc/caddy/conf.d/social.caddyfile`. Follow the `caddy/files.caddyfile` placeholder/finalize convention if any substitution is needed (none expected — the domain is literal).

### Repo files / dirs created in P1
- `docs/social-provisioning.md`
- `config/social.env.example`
- `src/claude_soma/mcp_servers/social/{__init__.py,config.py,vault.py,app.py,scheduler.py}`
- `src/claude_soma/mcp_servers/social/adapters/{__init__.py,base.py,instagram.py,threads.py,facebook_page.py}` (OAuth methods only)
- `src/claude_soma/mcp_servers/social/routes/{__init__.py,oauth.py,tokens.py,health.py}`
- `systemd/claude-soma-social.service`, `systemd/claude-soma-social-refresh.timer`, `systemd/claude-soma-social-refresh.service`
- `caddy/social.caddyfile`
- `tests/mcp_servers/{test_social_config.py,test_social_vault.py,test_social_oauth.py}` (+ no-Playwright guard test)
- `.gitignore` entries for `/etc/claude-soma/social.env` & `social.sqlite` patterns

### systemd install (via `deploy-systemd.sh`)
`sudo bash scripts/deploy-systemd.sh` copies the three new units → `/etc/systemd/system`, daemon-reloads. The refresh **timer** auto-restarts because its sibling `.service` is present in DEST (script's documented condition). `claude-soma-social.service` prints `RESTART REQUIRED` (or restarts under `--restart-services`); it is **not** `claude-soma-channel.service`, so it is safe to auto-restart — never hijacks the bot. Then `systemctl enable --now claude-soma-social.service claude-soma-social-refresh.timer` once (one-time enable, documented in the provisioning doc).

### Caddy route (P1)
Drop `caddy/social.caddyfile` → `/etc/caddy/conf.d/social.caddyfile`; `caddy reload`. Host `social.mayankgupta.in` resolves; 4 public paths reverse-proxy to `:8800`; all else 404; `:8800` unreachable externally (ufw 443 only).

### Test strategy (P1, pytest)
- `test_social_config.py`: loader asserts all three App ID/Secret pairs + verify token; missing key → startup error; no secret leaks into any returned/logged structure.
- `test_social_vault.py`: empty-vault first start seeds one `tokens` row per platform (IG/Threads 60d `expires_at`, FB `NULL`); restart does **not** re-seed/overwrite a refreshed token; WAL + `0600` enforced; App ID/Secret **absent** from vault.
- `test_social_oauth.py`: `/api/oauth/start` mints single-use `state`; `/oauth/callback` with valid state runs the (mocked-`httpx`) code→short→long exchange and writes vault, `302`s; invalid/used state → `400`; app secret never in any response.
- Refresh-tick unit test: refresh fires only when `expires_at-now<7d` AND `now-issued_at≥86400`; never FB; 3-fail → sentinel; expired token retained.
- No-Playwright guard test green. `deploy-systemd.sh --dry-run` against a temp DEST shows the new units copy and the timer would restart (sibling-service present).

### Done when (P1 acceptance gate)
- [ ] `docs/social-provisioning.md` + `config/social.env.example` committed; the user can follow the doc end-to-end to provision the three token spaces without any product code running.
- [ ] Empty-vault first start seeds correctly; restart never re-seeds/overwrites; App ID/Secret in memory only, absent from vault.
- [ ] `/oauth/callback` completes the server-side exchange and writes the vault; invalid/used `state` → `400`; no secret in any response/log.
- [ ] In-process `token_refresh` **and** the external `claude-soma-social-refresh.timer` both refresh IG+Threads under the 7-day/≥24h constraint, never FB; 3-fail → sentinel + `last_refresh_error`; expired token retained.
- [ ] `/health` + `/api/tokens/status` report correct per-platform `days_to_expiry` with no token leakage.
- [ ] Units committed in `systemd/`, install cleanly via `deploy-systemd.sh` (service binds `127.0.0.1:8800`, survives restart from the vault; timer auto-restart-safe; channel.service untouched); Caddy block exposes exactly the 4 public paths; ruff(100)/mypy(strict)/pytest + no-Playwright guard all green.

---

## Phase P2 — Publish + engage API + per-platform adapters + MCP shim + `.mcp.json` wiring

**Goal:** complete the adapters' write/read surface (publish post/reel/story/trial-reel/carousel, reply to + moderate own-content comments, read insights) behind the clean `PlatformAdapter` protocol, drive it with the publish-job queue, and expose the whole internal API through the stdio MCP shim wired into `.mcp.json` (the surface `social-manager` loads).

### Ordered steps

1. **P2.1 — Adapter publish/comment/insights methods.**
   Implement `publish` (container → poll `status_code` until `FINISHED` → publish; IG `media_type=REELS|STORIES` + `media_product_type` for trial-reel; Threads `TEXT|IMAGE|VIDEO|CAROUSEL` + `auto_publish_text`; FB `/photos`|`/feed` + native `scheduled_publish_time` + resumable video), `comment` (reply to OWN comment/media), `insights` (read-only pass-through) on all three adapters. Per-platform ceilings: IG 50/24h (check `/content_publishing_limit`), Threads 250 posts + 1000 replies/24h, FB BUC.
2. **P2.2 — Publish job queue + drain (`scheduler.py` extension, `publish_jobs` table).**
   `POST /api/publish/{platform}` creates the container, inserts a `publish_jobs` row (`creation_id`, `container_created_at`), returns `{job_id, creation_id, status}`. `publish_drain` tick publishes ready containers; 18h container cutoff (6h margin) → `expired`; over-limit jobs stay `pending`, never error. `/api/jobs` + `/api/jobs/{id}` report queue + container-24h watch.
3. **P2.3 — Insights route (`routes/insights.py`).**
   `GET /api/insights/{platform}/{object_id}` — read-only pass-through; assert no write path reachable from this route.
4. **P2.4 — MCP shim (`server.py`) + `.mcp.json` stanza.**
   `server.py`: `FastMCP("social")` with `main()`; one `@mcp.tool()` per route as a thin `httpx` call to `HERMES_SOCIAL_BASE_URL`. P2 tools: `social_tokens_status`, `social_oauth_start`, `social_token_refresh`, `social_publish`, `social_job_status`, `social_jobs_list`, `social_insights`. No Graph calls, no secrets, no business logic in the shim. Append the `social` stdio stanza to `.mcp.json` (command `.venv/bin/python -m claude_soma.mcp_servers.social.server`, env `HERMES_SOCIAL_BASE_URL`, `alwaysLoad:true`). Validate `.mcp.json` parses.

### Repo files / dirs created/extended in P2
- `src/claude_soma/mcp_servers/social/adapters/{instagram,threads,facebook_page}.py` (publish/comment/insights filled in)
- `src/claude_soma/mcp_servers/social/routes/{publish.py,insights.py}`
- `src/claude_soma/mcp_servers/social/server.py` (MCP shim)
- `src/claude_soma/mcp_servers/social/scheduler.py` (extended: `publish_drain`)
- `.mcp.json` (append `social` stanza)
- `tests/mcp_servers/{test_social_adapters.py,test_social_publish.py,test_social_server.py}`

### systemd install (P2)
No new units. `claude-soma-social.service` already runs the same `app:app` — the new routers are picked up on the next service restart (`systemctl restart claude-soma-social.service`, or via `deploy-systemd.sh --restart-services` since it's not the channel unit). The MCP shim is launched on-demand by the MCP client (stdio) — no systemd unit needed.

### Caddy route (P2)
No change — publish/jobs/insights are **internal** (`127.0.0.1:8800` only, never reverse-proxied). The 4-path public whitelist from P1 stands.

### Test strategy (P2, pytest)
- `test_social_adapters.py`: each adapter's `publish`/`comment`/`insights` against mocked `httpx` — container creation payloads, status poll loop, publish call, reel/story/carousel/trial-reel routing; Threads text shortcut; FB scheduled-publish; `business_discovery`/`dm_reply` raise `NotSupported` where applicable (Threads/FB discovery, Threads DM).
- `test_social_publish.py`: `POST /api/publish/{platform}` → container stored, published on next drain (no `scheduled_at`), returns `media_id`; container >18h → `expired` surfaced in `/api/jobs`; ceiling enforcement (IG 50, Threads 250+1000) → over-limit stays `pending`, no error.
- `test_social_server.py`: each `@mcp.tool()` shim round-trips to its route via mocked `httpx`; shim contains zero Graph hostnames and zero secret reads (static assertion); `main()` importable.
- Re-run no-Playwright guard + full lint/type/test suite.

### Done when (P2 acceptance gate)
- [ ] `POST /api/publish/{platform}` creates a container, stores `creation_id`+`container_created_at`, publishes on next drain, returns a `media_id`; reel/story/carousel/trial-reel kinds route correctly; container >18h → `expired`; IG 50/24h + Threads 250+1000/24h ceilings hold (over-limit stays `pending`, no error).
- [ ] `comment()` and `insights()` reachable per spec; `/api/insights` is read-only with no write path.
- [ ] `.mcp.json` has the `social` stdio stanza and parses; `server.py` exposes the P2 `@mcp.tool()` shims + `main()`; each shim is a thin `httpx` call with no Graph calls / no secrets.
- [ ] ruff(100)/mypy(strict)/pytest + no-Playwright guard all green; service restart picks up the new routers from the vault state.

---

## Phase P3 — Webhook receiver + product draft+approval surface + inbound-DM Messenger auto-reply + IG Business Discovery

**Goal:** receive Meta comments/mentions/messages (verify + HMAC + enqueue), drain into the **product-owned** draft+approval surface (no dependency on the departing VPS engagement queue — resolves the FI-ENGAGEMENT-MIGRATE tension), enforce FI-NO-POST-WITHOUT-APPROVAL for public content, implement the **per-thread operator-toggleable** inbound-DM auto-reply inside the 24h window, and add read-only IG Business Discovery → drafts.

### Ordered steps

1. **P3.1 — Schema extension (vault).**
   Extend `drafts` with `source`/`source_meta` (+ the full status set incl. `failed_24h_window_expired`, `discovery_expired`, `discovery_failed_token_expired`); add `webhooks_queue`, `dm_threads`, `dm_messages`, `dm_audit_log`, `discovery_accounts` per §6 (idempotent DDL).
2. **P3.2 — Webhook receiver (`webhooks.py` + `routes/webhooks.py`).**
   `GET /api/webhooks/meta` echoes `hub.challenge` only on matching `hub.verify_token`, else `403`. `POST`: read **raw bytes** first; resolve platform from `object` (`instagram`/`threads`/`page`); select that space's secret; `hmac.compare_digest` of `sha256=HMAC_SHA256(raw, secret)` vs `X-Hub-Signature-256`; unknown `object` fails closed (`403`); missing/mismatch `403` + audit. On pass, `INSERT OR IGNORE` into `webhooks_queue` (idempotent on Meta entry/change id); return `200` fast. **No Graph call / draft / DM send inline.**
3. **P3.3 — Draft surface + drains (`drafts.py` + `routes/drafts.py`).**
   `webhook_drain` (every 30s) routes by `event_type`: `comment`/`mention` → `drafts` row `source='webhook'` `pending_approval` (`proposed_text` empty — operator authors on approve); `message` → DM intake (P3.4). **Structurally no intake→post path.** `/api/drafts` (filter by `status` + `source`), `/api/drafts/{id}`, `POST .../approve {reply_text?}` (→ `approved`; `publish_drain` posts via adapter `comment()`, records `published_id`; 5-min double-post guard per `target_id`; token-expired → `failed_token_expired` + sentinel), `POST .../reject` (no API call). Orchestrator-agnostic: returns raw state + accepts decisions; FI-ENGAGEMENT-MIGRATE can call the same tools without re-implementing approval; works standalone if that pipeline is absent.
4. **P3.4 — Inbound-DM auto-reply + toggle (`routes/dm.py` + `dm_window_sweeper`).**
   `message` webhook → upsert `dm_threads` (`window_closes_at = inbound_ts + 86400`) + append `dm_messages` (`inbound`); sends nothing. New thread defaults `auto_reply_enabled=0` (draft-gated). `/api/dm/threads` (lists `within_24h_window`, `window_closes_at`, `auto_reply_enabled`); `POST /api/dm/threads/{id}/auto-reply {enabled}` (per-thread toggle — **no global switch**); `POST /api/dm/threads/{id}/reply {text, mode?}` (operator-authored; `mode` overrides thread toggle for one reply; auto → send now via `dm_reply()` within window + `dm_audit_log mode='auto'`; draft → `pending_approval`). Window hard-check on every send: `>23.5h` warn-but-allow, `>24h` refuse → `failed_24h_window_expired` + alert. `dm_window_sweeper` flips `within_24h_window=0`. ≥60s/thread auto-send rate limit. Token-expired → `failed_token_expired` + alert. IG + FB Page only (Threads `dm_reply` raises NotSupported). `/api/dm/audit`.
5. **P3.5 — IG Business Discovery (`routes/discovery.py` + adapter `business_discovery`).**
   IG adapter `business_discovery(username)` → `business_discovery?fields=username,media{id,caption,media_type,media_product_type,permalink,timestamp}` (media list + captions only — **no engagement metrics**, respecting the Playwright-fallback boundary). `POST /api/discovery/run {accounts}` → `DiscoveryPost` → `drafts` row `source='business_discovery'` `pending_approval` (caption + media id only, `proposed_text` empty); accounts from `/opt/claude-soma/social-discovery-accounts.txt` or the arg (never hardcoded), mirrored into `discovery_accounts` (30-day username→id cache, `last_run_at`); pre-flight refuse if IG token <24h to expiry; per-account 401 → `discovery_failed_token_expired` + continue; resolution failure recorded in summary, no crash; returns `{success,failed,created_drafts,errors[]}`. Daily APScheduler `discovery_run` tick (~02:00, batch 1–2 accounts to bound BUC). Stale discovery drafts auto-expire → `discovery_expired` after 7d (retained). `GET /api/discovery/accounts`. Threads/FB `business_discovery` raise NotSupported.
6. **P3.6 — MCP shim completion.**
   Add the remaining `@mcp.tool()` shims: `social_drafts_list`, `social_draft_get`, `social_draft_approve`, `social_draft_reject`, `social_dm_threads`, `social_dm_set_auto_reply`, `social_dm_reply`, `social_dm_audit`, `social_discovery_run`, `social_discovery_accounts`. Reply/draft text always operator-supplied, never server-generated.

### Repo files / dirs created/extended in P3
- `src/claude_soma/mcp_servers/social/{webhooks.py,drafts.py}`
- `src/claude_soma/mcp_servers/social/routes/{webhooks.py,drafts.py,dm.py,discovery.py}`
- `src/claude_soma/mcp_servers/social/adapters/instagram.py` (+`business_discovery`, +`dm_reply`); `facebook_page.py` (+`dm_reply`)
- `src/claude_soma/mcp_servers/social/scheduler.py` (extended: `webhook_drain`, `dm_window_sweeper`, `discovery_run`)
- `src/claude_soma/mcp_servers/social/vault.py` (extended schema)
- `src/claude_soma/mcp_servers/social/server.py` (remaining shims)
- `tests/mcp_servers/{test_social_webhooks.py,test_social_drafts.py,test_social_dm.py,test_social_discovery.py}`

### systemd install (P3)
No new units. The webhook receiver runs inside the already-installed `claude-soma-social.service`; restart it to pick up the new routers (`systemctl restart claude-soma-social.service` / `deploy-systemd.sh --restart-services`). The refresh timer/service from P1 are unchanged.

### Caddy route (P3)
`/api/webhooks/meta` was already whitelisted in the P1 `caddy/social.caddyfile` block, so no Caddy change is needed — Meta's GET-verify + POST-delivery now reach the live receiver. drafts/dm/discovery routes stay internal (not reverse-proxied).

### Test strategy (P3, pytest)
- `test_social_webhooks.py`: GET echoes `hub.challenge` only on matching verify token, else `403`; POST `403` on missing/byte-flipped signature, `200` on valid; signature computed on **raw bytes before parsing**; all three `object` types; unknown `object` fails closed; redelivery (same Meta id) → no second row, still `200`.
- `test_social_drafts.py`: no webhook-sourced reply posts without a prior `approve` (static + runtime proof of no intake→post path); `approve` posts edited reply via mocked `comment()` + records `published_id`; `reject` makes no call; 5-min double-post guard; token-expired → `failed_token_expired`; `/api/drafts` filters by `source`+`status`; surface works **standalone** and is callable without re-implementing approval.
- `test_social_dm.py`: new thread defaults `auto_reply_enabled=0`; intake never sends; per-thread auto-reply ON → operator reply within window sends immediately + `dm_audit_log mode='auto'`; per-reply `mode` override; no global switch; `>24h` refused → `failed_24h_window_expired` + alert; `>23.5h` warn-but-allow; ≥60s/thread rate limit; Threads has no DM path.
- `test_social_discovery.py`: `discovery_run` creates `source='business_discovery'` `pending_approval` drafts with caption + media id only (**no** engagement metrics), `proposed_text` empty; pre-flight refuses if IG token <24h; per-account 401 → `discovery_failed_token_expired` + continue; resolution failure in summary, no crash; returns the summary dict; 7-day auto-expire → `discovery_expired` (retained); Threads/FB → NotSupported.
- Re-run no-Playwright guard + full lint/type/test suite; validate `.mcp.json` still parses with the full tool set.

### Done when (P3 acceptance gate)
- [ ] `GET /api/webhooks/meta` verify-echo correct; `POST` HMAC verify on raw bytes for all three `object` types, unknown fails closed; idempotent enqueue before processing; redelivery → no second row, still `200`.
- [ ] FI-NO-POST-WITHOUT-APPROVAL: no webhook-sourced reply ever posts without `approve` (static + runtime proof of no intake→post path); `approve`→`comment()` records `published_id`; `reject`→no call; 5-min double-post guard; `/api/drafts` source/status filters; surface standalone + FI-ENGAGEMENT-MIGRATE-callable with no re-implemented approval logic.
- [ ] DM: new thread draft-gated by default; auto-reply ON sends immediately within window + `dm_audit_log mode='auto'`; per-reply `mode` override; no global switch; `>24h` refused → `failed_24h_window_expired` + alert; `>23.5h` warns; ≥60s/thread limit; Threads no DM.
- [ ] Discovery: drafts carry caption + media id only (no metrics), `proposed_text` empty; pre-flight token check; per-account 401 continues; resolution failures summarized; 7-day auto-expire (retained); Threads/FB NotSupported.
- [ ] Full MCP tool set present (P1+P2+P3 shims) and `.mcp.json` parses; ruff(100)/mypy(strict)/pytest + no-Playwright guard all green; service restart picks up the receiver/drains.

---

## Phase dependencies & exit

- **P1 → P2 → P3** are sequential. P1 establishes the process, vault, and OAuth that P2's publish queue and P3's drains both depend on; the provisioning doc (P1.0) unblocks the user to populate `/etc/claude-soma/social.env` before any P1 code is exercised.
- **No phase begins code until the operator reviews/consents to that phase**, and **no commit lands until the operator reviews this plan.**
- **Full-component exit** (after P3): all §14 acceptance criteria pass; code under `src/claude_soma/mcp_servers/social/`; `server.py` exposes `@mcp.tool()` shims + `main()`; tests under `tests/mcp_servers/test_social_*.py` green under ruff(100)/mypy(strict)/pytest; `.mcp.json` `social` stanza present; the three `systemd/` units committed + install via `deploy-systemd.sh` (channel.service never auto-restarted; refresh timer auto-restart-safe); Caddy `social.mayankgupta.in` exposes exactly the 4 public paths with `:8800` externally unreachable; `docs/social-provisioning.md` + `config/social.env.example` committed; `/etc/claude-soma/social.env` + `/opt/claude-soma/social.sqlite` `.gitignore`d and never committed; **no Playwright/VNC/scraping, no Anthropic key, no committed secrets, sole-author commits, no emoji** anywhere in the component.
