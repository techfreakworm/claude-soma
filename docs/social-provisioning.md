# Social provisioning guide

This document walks you through the one-time manual setup required before
`claude-soma-social.service` can run.  No automation, no Playwright, no VNC.
You perform these steps in your browser + a terminal; the service handles all
subsequent token refresh and expiry management automatically.

---

## Prerequisites

- Instagram account converted to **Business** or **Creator** (Settings > Account type).
- (Optional) a Facebook Page you administer, linked to your IG account.
- Admin access to the Meta Developer portal at https://developers.facebook.com.
- The three existing Meta apps (if already created):
  - Instagram-Login app: App ID `1662457504971901`
  - Threads app: App ID `1790145195303919`
  - Facebook app: App ID `1375342191160828`

---

## Step 1 — Meta Developer app configuration

For each of the three apps do the following in the Developer portal:

1. Open the app > **App settings > Basic** — note the App ID and App Secret.
2. Ensure the app is in **Development** mode (not Live) — no app review required.
3. Add yourself as a **tester** under **Roles > Test Users** if not already present.

### OAuth redirect URI

All three apps must list `https://social.mayankgupta.in/oauth/callback` as a
valid OAuth redirect URI.  The exact steps differ per product:

- **Instagram-Login product:** App > Instagram > Basic Display or Instagram API > Client OAuth Settings > Valid OAuth Redirect URIs.  Add `https://social.mayankgupta.in/oauth/callback` (no trailing slash).
- **Threads product:** App > Threads > Basic Display > Valid OAuth Redirect URIs.  Same URL, no trailing slash.
- **Facebook Login product:** App > Facebook Login > Settings > Valid OAuth Redirect URIs.  Same URL, no trailing slash.

> Note: the redirect URI is additive; any existing `files.mayankgupta.in` entries remain.

### Webhook verification token

Pick a long random string (e.g. `openssl rand -hex 32`) and note it.  You will
put this same string in every app's **Webhooks** configuration (Phase 3) and in
your `meta-tokens.env` file below.

---

## Step 2 — Obtain long-lived tokens

You have two options.  Option A is recommended once the service is deployed.
Option B is a manual fallback for bootstrapping before deploy.

### Option A — Use /oauth/start (after deploying the service)

Once the service is running at `social.mayankgupta.in`, visit:

```
https://social.mayankgupta.in/oauth/start?platform=instagram
https://social.mayankgupta.in/oauth/start?platform=threads
https://social.mayankgupta.in/oauth/start?platform=facebook_page
```

Each URL redirects you through the platform's authorization dialog; when you
approve, the service exchanges the code server-side, stores the resulting
long-lived token in the vault, and shows a confirmation page.  You are done.
Skip Option B and go to Step 3.

### Option B — Manual Graph API Explorer flow (bootstrap before deploy)

Use this if you want to pre-populate the env file before deploying the service.

#### Instagram (60-day long-lived token)

1. Open https://developers.facebook.com/tools/explorer and select your
   Instagram-Login app.
2. Click **Generate Access Token**.  Approve all scopes:
   `instagram_business_basic`, `instagram_business_content_publish`,
   `instagram_business_manage_comments`, `instagram_business_manage_messages`,
   `instagram_manage_insights`.
3. The Explorer gives you a short-lived token.  Exchange it for a 60-day token:

```bash
curl "https://graph.instagram.com/access_token?grant_type=ig_exchange_token&client_secret=IG_APP_SECRET&access_token=SHORT_LIVED_TOKEN"
```

The response contains `access_token` (long-lived) and `expires_in` (5184000 = 60 days).

4. Fetch your numeric user ID:

```bash
curl "https://graph.instagram.com/me?fields=id&access_token=LONG_LIVED_TOKEN"
```

5. Note `IG_LONG_LIVED_TOKEN` and `IG_USER_ID`.

#### Threads (60-day long-lived token)

1. Visit https://threads.net/oauth/authorize with your Threads app credentials
   (same scopes: `threads_basic`, `threads_content_publish`, `threads_manage_replies`,
   `threads_manage_insights`), or use the Graph API Explorer with your Threads app.
2. Exchange the short-lived code for a long-lived token:

```bash
curl "https://graph.threads.net/access_token?grant_type=th_exchange_token&client_secret=THREADS_APP_SECRET&access_token=SHORT_LIVED_TOKEN"
```

3. Fetch your Threads user ID:

```bash
curl "https://graph.threads.net/me?fields=id&access_token=LONG_LIVED_THREADS_TOKEN"
```

4. Note `THREADS_LONG_LIVED_TOKEN` and `THREADS_USER_ID`.

#### Facebook Page (non-expiring page token)

1. Use Graph API Explorer with your Facebook app.  Generate a user token with
   scopes: `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`,
   `pages_manage_engagement`, `pages_manage_metadata`, `pages_read_user_content`,
   `pages_messaging`.
2. Exchange for a long-lived user token:

```bash
curl "https://graph.facebook.com/v25.0/oauth/access_token?grant_type=fb_exchange_token&client_id=META_APP_ID&client_secret=META_APP_SECRET&fb_exchange_token=SHORT_USER_TOKEN"
```

3. Fetch the non-expiring Page token:

```bash
curl "https://graph.facebook.com/v25.0/me/accounts?access_token=LONG_USER_TOKEN"
```

The response lists pages you admin.  Find your page (by `id`) and note its
`access_token` — this is the non-expiring Page token.

4. Note `FB_PAGE_ACCESS_TOKEN` and `FB_PAGE_ID`.

---

## Step 3 — Populate the env file

Copy the template and fill in your values:

```bash
mkdir -p ~/.config/social-manager
cp /opt/claude-soma/config/social/meta-tokens.env.example \
   ~/.config/social-manager/meta-tokens.env
chmod 600 ~/.config/social-manager/meta-tokens.env
$EDITOR ~/.config/social-manager/meta-tokens.env
```

Fill in all six App ID/Secret pairs, the `WEBHOOK_VERIFY_TOKEN`, and any
pre-provisioned long-lived tokens from Option B above.  Tokens from Option A
are written to the vault directly — you can leave those fields blank.

The service reads this file exactly once on startup via `HERMES_SOCIAL_CONFIG`
(default `~/.config/social-manager/meta-tokens.env`).  After the vault is
seeded, the file is no longer read.  Do not commit this file to git.

---

## Step 4 — Deploy and install the service

```bash
# On the VPS, from /opt/claude-soma:
git pull --ff-only
sudo bash scripts/deploy-systemd.sh

# Create the vault directory with correct permissions:
sudo install -d -o ubuntu -g ubuntu -m 0700 /var/lib/claude-soma

# Enable and start:
sudo systemctl enable --now claude-soma-social.service
sudo systemctl enable --now claude-soma-social-refresh.timer

# Check liveness:
curl -s http://127.0.0.1:9200/health | python3 -m json.tool
```

---

## Step 5 — Install the Caddy vhost

```bash
sudo cp /opt/claude-soma/config/caddy/social.caddyfile \
        /etc/caddy/conf.d/social.caddyfile
sudo caddy reload --config /etc/caddy/Caddyfile
```

Verify: `curl -s https://social.mayankgupta.in/health`

---

## Token expiry and refresh

| Platform | Token type | Expiry |
|---|---|---|
| Instagram | Long-lived bearer | 60 days |
| Threads | Long-lived bearer | 60 days |
| Facebook Page | Page token | Non-expiring |

The daily systemd timer (`claude-soma-social-refresh.timer`, runs at 03:30 UTC)
calls `python -m claude_soma.social.refresh`, which refreshes IG and Threads
tokens when they are within 7 days of expiry and at least 24 hours old.
Facebook Page tokens are never refreshed (they do not expire).

**Calendar reminder:** set a reminder for day 55 after token issuance to check
`curl -s https://social.mayankgupta.in/health`.  If `days_to_expiry` is below 7,
the timer should have already refreshed; if not, trigger a manual refresh:

```bash
sudo systemctl start claude-soma-social-refresh.service
journalctl -u claude-soma-social-refresh.service -n 50
```

If the token is already expired (days_to_expiry negative), use Option A from
Step 2 above to re-authorize via `/oauth/start`.

---

## Security notes

- `~/.config/social-manager/meta-tokens.env` must be `chmod 600` — it contains
  app secrets.
- `/var/lib/claude-soma/social.sqlite` is created `0600` by the service.
- Never commit either file to git; both are in `.gitignore`.
- The service never logs raw token values.  Error responses contain only
  structured metadata (platform, status, days_to_expiry).
- Port 9200 is bound to `127.0.0.1` only.  Caddy proxies only four paths:
  `/oauth/*`, `/health`, `/api/webhooks/*`.  All other routes are unreachable
  externally.
