---
name: schedule-routine
description: |
  Create a recurring or one-shot cloud routine via Claude Code's RemoteTrigger
  API. Use when the user says "every weekday at 9am, do X", "remind me
  tomorrow", "schedule X every Sunday", and similar.
allowed-tools:
  - RemoteTrigger
---

# schedule-routine

## Process

1. Parse the cadence from the user's request:
   - "every weekday at 9am" → cron `7 9 * * 1-5` (always pick an off-minute)
   - "every Sunday 11am" → `13 11 * * 0`
   - "tomorrow morning" → one-shot, pick `30 8 <tomorrow-DoM> <month> *`,
     `recurring: false`

2. Compose the routine's prompt body — explicit and standalone, since cloud
   routines do NOT inherit your current context:

   ```
   Run the morning-brief skill. Send result to telegram chat <chat-id>.
   ```

3. Call `RemoteTrigger` with action `create`:

   ```
   RemoteTrigger(action="create", body={
     "name": "<human-readable name>",
     "cron": "<cron expr>",
     "prompt": "<the routine body>"
   })
   ```

4. Confirm to user with parsed run time + claude.ai URL the response includes.

## Constraints

- Routines run on Anthropic infra, do NOT have access to local files or MCPs.
  For routines needing local data, fire a webhook to the gateway instead.
- Minimum cadence: 1 hour.
- V1 cap: 5 active routines. If hitting cap, ask user which to remove.
