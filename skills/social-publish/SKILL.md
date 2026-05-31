---
name: social-publish
description: |
  End-to-end social-media campaign publish. Gathers raw materials (transcript,
  video frames, screenshots, README) from a tutorial video and supporting links,
  generates branded diagrams via codex, writes per-platform content, then drives
  the platform-specific poster agents to draft or publish on each platform.
  Use when the user says "publish my build log", "launch a social campaign",
  or "ship this to X + LinkedIn + Medium" (any subset).
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

> **Note**: `bluesky` agents (`social-bluesky-writer`, `social-bluesky-poster`)
> + `scripts/bluesky-post.py` shipped 2026-05-31 (S17, commit `d721e33`) but
> are **INERT — user opted out 2026-05-31**. Code remains on disk for
> reference but is not part of the active platform list. Do NOT dispatch
> the bluesky agents from this skill.

## Process

1. **Gather** raw materials: transcript, screenshots, diagrams, README excerpt.
   Ask the user for the source video URL or local path if not provided.

2. **Generate diagrams** if needed via `codex-image-gen`.

3. **Write** per-platform content by dispatching the platform writer agent:
   - `social-x-writer` for X threads
   - `social-x-article-writer` for X long-form
   - `social-linkedin-writer` for LinkedIn
   - `social-medium-writer` for Medium

4. **Review** all drafts together. Present to the user for approval if running
   interactively. In autonomous mode, proceed directly to posting.

5. **Post** by dispatching the platform poster agent for each approved draft:
   - `social-x-poster` for X
   - `social-x-article-poster` for X long-form
   - `social-linkedin-poster` for LinkedIn
   - `social-medium-poster` for Medium

## Platform selection

The user can specify a subset of platforms. Default is all four. Examples:
- "ship to X and LinkedIn" → x, linkedin only
- "LinkedIn + Medium" → linkedin, medium only
- "everywhere" → all four

## Credential requirements

| Platform  | Auth method                              | Credential file                  |
|-----------|------------------------------------------|----------------------------------|
| X         | Playwright session (pw-login.js)         | ~/.claude-pw/state-x.json        |
| LinkedIn  | Playwright session (pw-login.js)         | ~/.claude-pw/state-linkedin.json |
| Medium    | Playwright session (pw-login.js)         | ~/.claude-pw/state-medium.json   |

If a required credential file is missing, skip that platform and report which
one needs re-auth.

## Output

Report the final status of each platform:
- Posted: the canonical URL or AT URI of the published post
- Skipped: reason (missing creds, user excluded, draft rejected)
- Failed: error summary
