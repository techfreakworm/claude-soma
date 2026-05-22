---
name: project-status
description: |
  Report the current status of a named project. Use when the user asks
  "how's <project>?", "status of <project>", "what's <project> doing?".
allowed-tools:
  - mcp__project-orchestrator__get_status
---

# project-status

1. Call `mcp__project-orchestrator__get_status(name)`.

2. Format a one-paragraph summary including:
   - Project type and current status
   - How long since last activity (idle_for_seconds → minutes)
   - The Remote Control URL for direct attach

3. If the user is on voice, summarize verbally and shorten the URL.
