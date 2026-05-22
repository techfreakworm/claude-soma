---
name: list-projects
description: |
  List currently active project-leads. Use when the user asks "what's running?",
  "show me my projects", "list active agents", "status?", or similar.
allowed-tools:
  - mcp__project-orchestrator__list_projects
---

# list-projects

1. Call `mcp__project-orchestrator__list_projects`.

2. Format the response as a short bullet list:

   ```
   Active project-leads:
   • f1-tracker (web-scraper, idle 12m) — <RC URL>
   • trip-planner (llm-app, idle 3m) — <RC URL>
   ```

3. If the user is on Telegram and the list is long (>5), use compact bullets.
   If on voice, summarize verbally: "Three projects: f1-tracker, trip-planner,
   and reports-api. Want details on any of them?"

4. If empty, reply: "No active project-leads right now."
