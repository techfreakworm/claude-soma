# RUNBOOK-S3-ENV-BACKFILL — soma-improver transient unit env backfill

**Goal:** add `HERMES_LEAD_NAME=soma-improver` + `HERMES_NOTIFY_ENDPOINT=http://127.0.0.1:9100` to the soma-improver lead's transient systemd unit so this lead can use the proper `mcp__hermes-notify__notify_orchestrator` MCP tool instead of the current direct EventStore bypass.

**Why deferred to operator:** the change requires restarting `claude-soma-lead-soma-improver.service`, which hands off this very session. The new session boots with `--continue` (per FI-LEAD-CONTINUE) so the transcript is preserved, but the in-flight session is interrupted — not auto-fireable from inside the lead.

**Source of truth:** S3 audit in `/tmp/PAN-S3-crosscut.md` Section 5 (Evidence A/B/C/D).

---

## Pre-change state (verified 2026-06-02)

```
$ sudo cat /run/systemd/transient/claude-soma-lead-soma-improver.service | grep ^Environment
Environment="HOME=/home/ubuntu" "PATH=..." "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1"
```

No `HERMES_LEAD_NAME`. No `HERMES_NOTIFY_ENDPOINT`. soma-improver currently bypasses `notify_orchestrator` MCP tool and writes events via direct `EventStore.insert_event` + `urllib.request.urlopen(http://127.0.0.1:9100/notify, ...)`.

For comparison, new leads spawned via `src/claude_soma/mcp_servers/project_orchestrator/spawner.py:170-171` get these injected automatically:

```python
f"--setenv=HERMES_LEAD_NAME={name}",
"--setenv=HERMES_NOTIFY_ENDPOINT=http://127.0.0.1:9100",
```

---

## Operator steps (run as ubuntu with sudo)

### Step 1 — Edit the transient unit env line in place

The transient unit file lives at `/run/systemd/transient/claude-soma-lead-soma-improver.service`. Edit it with sudo + your editor of choice:

```bash
sudo cp /run/systemd/transient/claude-soma-lead-soma-improver.service /tmp/soma-improver-transient.bak
sudo nano /run/systemd/transient/claude-soma-lead-soma-improver.service
```

Find the `Environment=` line (single line; values are space-separated quoted strings):

```
Environment="HOME=/home/ubuntu" "PATH=..." "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1"
```

Append two more quoted assignments at the end of the same line (before the closing newline):

```
Environment="HOME=/home/ubuntu" "PATH=..." "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1" "HERMES_LEAD_NAME=soma-improver" "HERMES_NOTIFY_ENDPOINT=http://127.0.0.1:9100"
```

Save + exit.

### Step 2 — Reload systemd's view

```bash
sudo systemctl daemon-reload
```

Verify the new env is registered (without restarting yet):

```bash
sudo systemctl show claude-soma-lead-soma-improver.service -p Environment
```

Expected output contains both `HERMES_LEAD_NAME=soma-improver` and `HERMES_NOTIFY_ENDPOINT=http://127.0.0.1:9100`. If they're absent, the edit was malformed — restore from backup (`sudo cp /tmp/soma-improver-transient.bak /run/systemd/transient/claude-soma-lead-soma-improver.service && sudo systemctl daemon-reload`) and retry the edit.

### Step 3 — Restart the lead

```bash
sudo systemctl restart claude-soma-lead-soma-improver.service
```

**Do NOT** use `stop` then `start` — transient units may be reaped by `--collect` on stop, and a separate `start` will fail because the unit no longer exists. `restart` is atomic.

The lead session will:
1. Tear down the existing tmux pane (current claude session ends mid-turn; this NEEDS_INPUT request will be the last visible artifact in this transcript).
2. Re-execute the unit's `ExecStart` which spawns a new tmux session.
3. The new claude session starts with `--continue` — Claude Code loads the most recent transcript file for this project from `~/.claude/projects/-home-ubuntu-projects-soma-improver/*.jsonl` and resumes.
4. The new claude session's environment now includes the two new env vars.

### Step 4 — Verify env reached the new session

In the new session (you, post-restart), the operator may ask you to run:

```python
import os
print("HERMES_LEAD_NAME:", repr(os.environ.get("HERMES_LEAD_NAME")))
print("HERMES_NOTIFY_ENDPOINT:", repr(os.environ.get("HERMES_NOTIFY_ENDPOINT")))
```

Expected: `'soma-improver'` and `'http://127.0.0.1:9100'`.

Then test the proper MCP tool path (instead of the bypass):

```python
# Pre-fix: this would raise RuntimeError("HERMES_LEAD_NAME is not set in the environment.")
# Post-fix: this should succeed.
```

Invoke `mcp__hermes-notify__notify_orchestrator(type="STARTED", payload={"task":"verify-env-backfill","progress":"S3 follow-up verified live","percent":100})` — if it returns `{"stored_id": <int>, "delivered": <bool>}` without error, the backfill succeeded and the bypass is no longer required.

---

## Rollback

If post-restart the lead behaves incorrectly or the new env vars cause an unexpected regression:

```bash
sudo cp /tmp/soma-improver-transient.bak /run/systemd/transient/claude-soma-lead-soma-improver.service
sudo systemctl daemon-reload
sudo systemctl restart claude-soma-lead-soma-improver.service
```

This restores the pre-change Environment= line (no new vars). The bypass path (direct EventStore + POST /notify) continues to work as it does today.

---

## Notes

- **Transient unit volatility:** the unit file in `/run/systemd/transient/` is recreated by `systemd-run` if the unit is fully torn down and respawned. The in-place edit only persists until the next full teardown. If/when the operator wants this change durable across reboots, the cleaner path is to migrate soma-improver from a transient unit to a persistent `/etc/systemd/system/claude-soma-lead-soma-improver.service` unit with the spawner's correct env-injection baked in. That is a separate, larger refactor not in scope for this runbook.
- **The bypass code stays in place** — it is still the correct fallback path (e.g. for tests that instantiate EventStore directly). Backfilling the env just gives this lead a SECOND, cleaner option going forward.
- **The new --continue session** preserves transcript content but starts fresh on token accounting. If pre-restart context was approaching limits, this is actually a soft win (fresh budget).

---

## Verification checklist

- [ ] `sudo systemctl show claude-soma-lead-soma-improver.service -p Environment` returns env line containing BOTH new vars.
- [ ] Post-restart, `os.environ.get("HERMES_LEAD_NAME") == "soma-improver"`.
- [ ] Post-restart, `mcp__hermes-notify__notify_orchestrator(type="STARTED", payload={...})` returns `{"stored_id": ..., "delivered": ...}` without RuntimeError.
- [ ] `/tmp/soma-improver-transient.bak` retained until verification is complete (delete after green).
