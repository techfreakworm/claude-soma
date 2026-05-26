# /api/routines: aggregate all schedule sources + fix slow load

2026-05-26. Two coupled issues from `TASK-dashboard-and-schedules-2026-05-26.md`.

## Item 2 — aggregate ALL schedule sources

`/api/routines` already merged the claude-soma registry, claude-soma systemd
timers, and cloud RemoteTrigger routines, but it (a) had no crontab source at
all and (b) filtered systemd timers to `claude-soma-*` only, so generic server
schedules were invisible.

Now it aggregates, each row labelled by `created_by`:

- registry routines (`user` / `bot`) — unchanged.
- systemd timers — `created_by="system"`, filter **removed** so ALL timers from
  `systemctl list-timers --all` surface, not just claude-soma ones.
- **cron** (new) — `created_by="cron"`: the user crontab (`crontab -l`),
  `/etc/crontab`, and `/etc/cron.d/*`. Robust parse: skips comments / blanks /
  env-assignments, handles `@macro` schedules and the system 6-field form
  (user column between schedule and command). Cron paths are module constants
  (`ETC_CRONTAB`, `CRON_D_DIR`) so tests point them at fixtures.

Live result: the listing went from registry + claude-soma timers (~11 rows) to
32 rows — 22 system timers, 8 cron, 1 user, 1 bot. This also fixes the
orchestrator's "what's scheduled" answer (same endpoint).

## Item 3 — slow load

Profiled the three (now four) sources:

| source | time |
|---|---|
| registry (sqlite) | 1.9 ms |
| systemd timers | 46 ms |
| **cloud (`claude -p`)** | **12,012 ms** |

The cloud query spawns a whole `claude` process and was 100% of the latency
(and it returns 0 rows here). Fixes:

- **Cache** the cloud result (`HERMES_ROUTINES_CLOUD_TTL`, default 300s). Repeat
  loads: **12.2s -> 117ms (104x)**.
- **Parallelize** all four sources (`ThreadPoolExecutor`) so the new cron
  shell-outs don't stack onto latency and a cold cloud call overlaps the rest.
- **Cap** the cloud timeout at 30s (was 120s) so a hung query can't wedge the
  page; on overrun it returns [] and the other sources still render.
- Frontend `app/admin/routines/loading.tsx` skeleton: the page is an async
  server component, so on a cold cache the user now sees an instant skeleton
  instead of a blank wait.

## Follow-up (not done here)

The first request per TTL still pays ~12s. A background prewarm (e.g. extend
the cache-refresh timer, which currently only hits unauthed endpoints) could
keep the cloud cache warm so no user ever waits. Left as a follow-up.
