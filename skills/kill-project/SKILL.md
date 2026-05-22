---
name: kill-project
description: |
  Gracefully shut down a named project-lead and archive its memory. Use when
  the user says "shut down <project>", "kill <project>", "stop <project>", or
  "we're done with <project>".
allowed-tools:
  - mcp__project-orchestrator__kill_project
  - mcp__project-orchestrator__get_status
  - SendMessage
---

# kill-project

1. Resolve the project's `agent_id` via `mcp__project-orchestrator__get_status`.

2. Send a graceful shutdown request via `SendMessage`:

   ```
   SendMessage(to: "<agent_id>",
               message: {"type": "shutdown_request",
                         "reason": "user requested kill"})
   ```

3. Mark killed in the registry: `mcp__project-orchestrator__kill_project(name, archive=true)`.

4. Confirm to the user: "Hibernated f1-tracker. Memory archived. Spin it back
   up anytime."
