---
name: usage-report
description: |
  Surface Claude Code's current usage state in a chat-friendly format. Use for
  "how much credit?", "quota?", "am I close to my limit?", "usage report".
allowed-tools:
  - Bash(claude *)
  - Bash(sqlite3 /opt/claude-soma/usage.sqlite *)
---

# usage-report

1. Run `claude -p '/usage' --output-format json` once. Parse the JSON.

2. If `/opt/claude-soma/usage.sqlite` exists, also fetch the last 7 days of
   daily snapshots for trend context:

   ```bash
   sqlite3 /opt/claude-soma/usage.sqlite \
     "SELECT date, interactive_credits_used, agent_sdk_credits_used
      FROM daily_snapshots ORDER BY date DESC LIMIT 7;"
   ```

3. Format:

   ```
   Today:
     Interactive bucket: X% used (~N turns left at current rate)
     Agent SDK bucket: Y% used
   7-day trend: avg N turns/day interactive, M turns/day Agent SDK
   ```

4. If approaching 75% on either bucket, flag with a warning emoji-free note.
