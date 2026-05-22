---
name: portfolio-status
description: |
  Summarize what the user is currently working on across ~/Projects/llm/*
  and the active orchestrator projects. Use for "what am I working on?",
  "give me the portfolio status", "where did I leave off?".
allowed-tools:
  - Bash(ls ~/Projects/llm/*)
  - Bash(git log *)
  - mcp__project-orchestrator__list_projects
  - Read
---

# portfolio-status

1. Call `mcp__project-orchestrator__list_projects` for active leads.

2. For each subdir of `~/Projects/llm/`:
   - Read its `MEMORY.md` first line if present (this is the project name +
     description from auto-memory)
   - Read its last commit (`git -C <path> log -1 --format='%cr %s'`)

3. Format as:

   ```
   Active project-leads:
     - <name>: <one-line status>
   
   Repos in ~/Projects/llm:
     - <name> — last commit <time> "<message>"
   ```

4. Keep under 15 lines. If asking via voice, condense to a 30-second readout.
