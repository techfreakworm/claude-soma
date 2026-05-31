---
name: schedule-routine
description: |
  Schedule a one-shot Telegram reminder or a recurring cloud routine. For
  one-shot reminders ("remind me in 5m", "remind me tomorrow at 9am"), use
  mcp__hermes_api__schedule_reminder (primary path). For recurring cloud
  routines ("every weekday at 9am, do X"), use the RemoteTrigger path (legacy).
allowed-tools:
  - mcp__hermes_api__schedule_reminder
  - RemoteTrigger
  - mcp__project_orchestrator__register_routine
---

# schedule-routine

## One-shot reminders (primary path)

For any request that fires once ("remind me in 5m", "ping me at 10am tomorrow",
"remind me to check X in 2 hours"), call `mcp__hermes_api__schedule_reminder`:

```
mcp__hermes_api__schedule_reminder(
  when="<5m | 2h | 1d | ISO-8601 | Unix epoch>",
  message="<plain-text reminder body>"
)
```

`when` formats:
- Relative: `5m`, `2h`, `1d`
- ISO 8601: `2026-06-01T09:00:00Z`
- Unix epoch: `1750000000`

Returns `{"pid": int, "fires_at_iso": str, "message_preview": str}`. Confirm
the `fires_at_iso` to the user.

This path spawns a detached bash subprocess (survives MCP restart) and sends
the message via the Telegram Bot API. No RemoteTrigger call needed.

## Recurring routines (cloud path — legacy)

For recurring schedules ("every weekday at 9am, do X", "every Sunday 11am"),
use the RemoteTrigger path below. Note: RemoteTrigger v2 requires `{name, cron,
prompt}` body. The `{name, cron, prompt}` shape is the only accepted form —
alternate body shapes return HTTP 400.

1. Parse the cadence from the user's request:
   - "every weekday at 9am" → cron `7 9 * * 1-5` (always pick an off-minute)
   - "every Sunday 11am" → `13 11 * * 0`
   - "tomorrow morning" → use `mcp__hermes_api__schedule_reminder` instead

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

4. After RemoteTrigger.create succeeds, register the routine locally so it
   appears in the dashboard. Call:

   ```
   mcp__project_orchestrator__register_routine(
     name="<the routine name>",
     kind="cloud",
     schedule="<the cron expression you used>",
     target_skill="<the skill the routine invokes>",
     description="<one-liner the user gave you>",
     created_by="user",
     metadata_json='{"trigger_id": "<the id returned by RemoteTrigger.create>"}'
   )
   ```

   If the local registration fails, surface that as a warning but do NOT
   roll back the cloud routine — the cloud side is the source of truth and
   the dashboard fallback already synthesizes a "cloud" entry from the
   /routines list query.

5. Confirm to user with parsed run time + claude.ai URL the response includes.

## Constraints

- Cloud routines run on Anthropic infra, do NOT have access to local files or
  MCPs. For routines needing local data, fire a webhook to the gateway instead.
- Minimum cadence for cloud routines: 1 hour.
- V1 cap: 5 active cloud routines. If hitting cap, ask user which to remove.
- One-shot reminders (`mcp__hermes_api__schedule_reminder`) have no cap.
