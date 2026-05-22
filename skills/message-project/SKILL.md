---
name: message-project
description: |
  Forward a message from the user to a specific named project-lead. Use when
  the user says "tell <project> to ...", "ask <project> about ...", or
  "<project>: ...".
allowed-tools:
  - mcp__project-orchestrator__send_to_project
  - SendMessage
---

# message-project

1. Extract the target project name from the user's message.

2. Call `mcp__project-orchestrator__send_to_project(name, message)` to confirm
   the project exists and to retrieve its `agent_id`.

3. Use `SendMessage` to relay the user's message to the project-lead's
   `agent_id`:

   ```
   SendMessage(to: "<agent_id>",
               summary: "user forward",
               message: "<the user's exact message>")
   ```

4. Confirm to the user: "Sent to f1-tracker."

## Error handling

- If `send_to_project` errors with `no project named X`, suggest the closest
  match from `list_projects` and ask the user to confirm.
