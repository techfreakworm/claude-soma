---
name: social-publish
description: |
  End-to-end social-media campaign publish. Gathers raw materials (transcript,
  video frames, screenshots, README) from a tutorial video and supporting links,
  generates branded diagrams via codex, writes per-platform content, then drives
  the platform-specific poster agents to draft or publish on each platform.
  Use when the user says "publish my build log", "launch a social campaign",
  or "ship this to X + LinkedIn + Medium + Bluesky" (any subset).
allowed-tools:
  - Read
  - Bash(python3 *)
  - Bash(ls *)
  - SendMessage
---

# social-publish

Orchestrates a multi-platform content campaign from a single brief or source
video. Supports the following platforms:

**Platforms**
- `x` — X (Twitter) thread, 280 chars/post, uses playwright-x
- `x-article` — X long-form article, uses playwright-x
- `linkedin` — LinkedIn newsletter or post, uses playwright-linkedin
- `medium` — Medium long-form post, uses playwright-medium
- `bluesky` — Bluesky post or linked-reply thread, 300 graphemes/post, AT Protocol direct API

## Process

1. **Gather** raw materials: transcript, screenshots, diagrams, README excerpt.
   Ask the user for the source video URL or local path if not provided.

2. **Generate diagrams** if needed via `codex-image-gen`.

3. **Write** per-platform content by dispatching the platform writer agent:
   - `social-x-writer` for X threads
   - `social-x-article-writer` for X long-form
   - `social-linkedin-writer` for LinkedIn
   - `social-medium-writer` for Medium
   - `social-bluesky-writer` for Bluesky

4. **Review** all drafts together. Present to the user for approval if running
   interactively. In autonomous mode, proceed directly to posting.

5. **Post** by dispatching the platform poster agent for each approved draft:
   - `social-x-poster` for X
   - `social-x-article-poster` for X long-form
   - `social-linkedin-poster` for LinkedIn
   - `social-medium-poster` for Medium
   - `social-bluesky-poster` for Bluesky

## Platform selection

The user can specify a subset of platforms. Default is all five. Examples:
- "ship to X and Bluesky" → x, bluesky only
- "LinkedIn + Medium" → linkedin, medium only
- "everywhere" → all five

## Credential requirements

| Platform  | Auth method                              | Credential file                  |
|-----------|------------------------------------------|----------------------------------|
| X         | Playwright session (pw-login.js)         | ~/.claude-pw/state-x.json        |
| LinkedIn  | Playwright session (pw-login.js)         | ~/.claude-pw/state-linkedin.json |
| Medium    | Playwright session (pw-login.js)         | ~/.claude-pw/state-medium.json   |
| Bluesky   | AT Protocol app password (bluesky-login) | ~/.claude-pw/bluesky.json        |

If a required credential file is missing, skip that platform and report which
one needs re-auth.

## Output

Report the final status of each platform:
- Posted: the canonical URL or AT URI of the published post
- Skipped: reason (missing creds, user excluded, draft rejected)
- Failed: error summary
