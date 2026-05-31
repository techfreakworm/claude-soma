# IST Migration — VPS timezone + en_IN locale (2026-05-31)

Tracks the decision basis, operator runbook, and rollback procedure for
migrating the VPS from UTC to `Asia/Kolkata` (+05:30) with `LC_TIME=en_IN.UTF-8`.

**Script to run:** `scripts/migrate-to-ist.sh`  
**OPERATOR runs this. Do NOT auto-execute from any subagent session.**

---

## Per-timer decision table

| File | Old `OnCalendar` | New `OnCalendar` | Rationale |
|---|---|---|---|
| `claude-soma-daily-status.timer` | `*-*-* 10:00:00` (live only; was 04:30 UTC = 10:00 IST in prior rev) | `*-*-* 10:00:00` | Intent is 10:00 IST. File added to repo with this value; post-migration fires at 10:00 IST. |
| `claude-soma-portfolio-oneliner.timer` | `Mon..Fri *-*-* 03:30:00` (no UTC; = 09:00 IST on UTC VPS) | `Mon..Fri *-*-* 09:00:00` | Intent is 09:00 IST Mon–Fri. Rewritten to IST value so post-migration behavior is identical. |
| `claude-soma-pw-refresh.timer` | `Sun *-*-* 04:00:00` (no UTC; = 09:30 IST on UTC VPS) | `Sun *-*-* 09:30:00` | Intent is Sunday morning session refresh. Live /etc already set to 09:30; repo now matches. |
| `claude-soma-usage-snapshot.timer` | `*-*-* 23:55:00` (no UTC; = 05:25 IST next day on UTC VPS) | `*-*-* 05:25:00` | Intent is 05:25 IST. Live /etc already set to 05:25; repo now matches. |
| `claude-soma-rc-url-refresh.timer` | `*-*-* 04:00:00 UTC` | unchanged | Explicit UTC suffix — tz-neutral. Fires at 04:00 UTC = 09:30 IST post-migration, 30 min before daily-status. |
| `claude-soma-relay-cleanup.timer` | `*-*-* 04:15:00 UTC` | unchanged | Explicit UTC suffix — tz-neutral. No behavior change. |
| `claude-soma-secrets-backup.timer` | `*-*-* 03:30:00 UTC` | unchanged | Explicit UTC suffix — tz-neutral. No behavior change. |
| `claude-soma-cache-refresh.timer` | `OnBootSec=3min / OnUnitActiveSec=5min` | unchanged | Interval-based — no absolute time, tz-neutral. |
| `claude-soma-healthcheck.timer` | `OnBootSec=2min / OnUnitActiveSec=10min` | unchanged | Interval-based — no absolute time, tz-neutral. |
| `claude-soma-idle-reaper.timer` | `OnBootSec=15min / OnUnitActiveSec=6h` | unchanged | Interval-based — no absolute time, tz-neutral. |

Notes on live vs repo discrepancy at the time of migration:
- `claude-soma-daily-status.timer` existed only in `/etc/systemd/system/` (not in repo). Added to repo now.
- `claude-soma-relay-cleanup.timer` and `claude-soma-portfolio-oneliner.timer` exist in repo but not in `/etc/systemd/system/`. The install step will deploy them; they will be inert unless their `.service` file is also installed.
- The live `/etc/systemd/system/` versions of `pw-refresh` and `usage-snapshot` were already updated to IST values in a prior operator session. This PR makes the repo source-of-truth match.

---

## Code-audit summary — naive `datetime.now()`

Grep command run:
```
grep -rIn "datetime\.now()" src/ scripts/ 2>/dev/null | grep -v ".pyc"
grep -rIn "time\.localtime\|time\.mktime" src/ scripts/ 2>/dev/null
```

Results:

**1 hit:**
```
src/claude_soma/install.py:1085:    ts = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
```

**Assessment:** This generates a timestamp string for a backup filename inside `install.py`.
After the tz change the timestamp will format in IST instead of UTC (e.g. `20260531T152230`
instead of `20260531T092230`). This is cosmetic — the file still sorts correctly and the backup
logic is unaffected. No correctness risk.

**Action:** Flag for cleanup in a separate PR (no fix in this PR per task scope).

`time.localtime` / `time.mktime`: **zero hits** — clean.

**FI-NOTIFY / DM timestamps:** `datetime.fromtimestamp(ts).strftime(...)` in the notify
formatter formats in local time. After migration this will emit IST-formatted timestamps in
Telegram DMs, which is the intended user experience.

---

## Registry routines table

`/opt/claude-soma/registry.sqlite` `routines` table uses:
- `schedule` column: human-readable display strings (`"daily 10:00 IST"`, `"every 5 min"`)
  — **not** cron expressions, not parsed by any scheduler. No migration needed.
- `next_run` / `last_run` columns: Unix epoch (`REAL`) — tz-neutral. No migration needed.

---

## Operator runbook

### Prerequisites

- You are on the VPS as a user with `sudo` access.
- Clone is at `/home/ubuntu/projects/soma-improver/claude-soma` and is up-to-date
  (run `git pull` if in doubt).
- Estimated downtime: zero for running services; timer firing times shift at end of step.

### Steps

```bash
# 1. Verify current state
timedatectl
locale

# 2. Dry-run to review all planned changes (no changes applied)
cd /home/ubuntu/projects/soma-improver/claude-soma
sudo bash scripts/migrate-to-ist.sh --dry-run

# 3. Review the dry-run output. Expected lines:
#    [ACTION] Set timezone Asia/Kolkata
#    [ACTION] Generate en_IN.UTF-8 locale  (or [SKIP] if already present)
#    [ACTION] Set LC_TIME=en_IN.UTF-8      (or [SKIP] if already present)
#    [ACTION] Install <timer>.timer → /etc/systemd/system/  (for changed files)
#    [ACTION] Restart <timer>.timer

# 4. Apply
sudo bash scripts/migrate-to-ist.sh

# 5. Verify timezone
timedatectl
# Expected:  Time zone: Asia/Kolkata (IST, +0530)

# 6. Verify locale
grep LC_TIME /etc/default/locale
# Expected:  LC_TIME=en_IN.UTF-8

# 7. Verify timers
systemctl list-timers --all | grep claude-soma
# Check Next trigger columns show IST times

# 8. Reload your shell to pick up LC_TIME in the current session
exec $SHELL -l

# 9. Spot-check a timer
systemctl show claude-soma-daily-status.timer | grep NextElapseUSecRealtime
# Should fire at 10:00 IST
```

---

## Rollback procedure

If something goes wrong, revert in this order:

```bash
# 1. Revert timezone to UTC
sudo timedatectl set-timezone UTC

# 2. Revert LC_TIME
sudo update-locale LC_TIME=

# 3. Restore original /etc/systemd/system timer files from git
#    (check out the last known-good versions)
git show HEAD~1:systemd/claude-soma-portfolio-oneliner.timer \
    | sudo tee /etc/systemd/system/claude-soma-portfolio-oneliner.timer
git show HEAD~1:systemd/claude-soma-pw-refresh.timer \
    | sudo tee /etc/systemd/system/claude-soma-pw-refresh.timer
git show HEAD~1:systemd/claude-soma-usage-snapshot.timer \
    | sudo tee /etc/systemd/system/claude-soma-usage-snapshot.timer

# 4. Reload and restart
sudo systemctl daemon-reload
sudo systemctl restart claude-soma-portfolio-oneliner.timer
sudo systemctl restart claude-soma-pw-refresh.timer
sudo systemctl restart claude-soma-usage-snapshot.timer

# 5. Verify
timedatectl
systemctl list-timers | grep claude-soma
```

No application code needs to be reverted — services restart automatically and
re-read the system clock. The `install.py` datetime.now() cosmetic issue is
unaffected by rollback.
