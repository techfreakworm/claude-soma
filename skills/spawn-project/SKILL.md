---
name: spawn-project
description: |
  Spawn a new persistent project-lead background session for a new app /
  workstream the user is starting. Use when the user says "build me X",
  "create an app that Y", "set up a server that Z", or similar greenfield
  project requests. The project-lead runs in its own cwd, has its own
  Remote Control URL, and creates its own agent team appropriate to the type.
allowed-tools:
  - mcp__project-orchestrator__spawn_project
  - mcp__project-orchestrator__list_template_types
---

# spawn-project

## Process

1. Pick a kebab-case name from the user's request (e.g. "f1-tracker",
   "trip-planner-llm"). Confirm with the user only if ambiguous.

2. Pick a `type` from the available templates:
   - `web-scraper` — scraping, data extraction, change detection
   - `llm-app` — model-powered applications, evaluation, prompt engineering
   - `server-app` — APIs, backends, ops
   - `agentic-coding` — multi-step coding with subagents, no persistent team
   - `custom` — anything else; user-defined teammates

   If unsure, call `mcp__project-orchestrator__list_template_types` and pick
   the closest match. Ask the user only if multiple types fit equally well.

3. Compose the `brief` — 2-5 paragraphs covering:
   - What to build (functional scope)
   - Any specific tech preferences mentioned by the user
   - Acceptance criteria (what "done" looks like)
   - Coordination notes for teammates

4. Call `mcp__project-orchestrator__spawn_project(name, type, brief)`.

5. Reply to the user with the project name + Remote Control URL:

   > Started `f1-tracker`. Attach in your phone's Claude app or at
   > <RC URL>. I'll relay messages from here too.

## Constraints

- Max 6 concurrent projects. If the cap is hit, the tool errors — relay the
  error to the user and offer to kill one of the listed projects.
- Names must match `^[a-z][a-z0-9-]{0,63}$`. Rewrite "F1 Tracker!" → "f1-tracker".
- Permission mode defaults to `acceptEdits`; user can override with phrases
  like "with full auto" → `auto`, "read-only" → `default`.
