# Multi-VPS Phase 2 — guard `send` + `tail-log` verbs (cross-host messaging + transcript pull)

**Status:** PLAN (reviewed by a 3-lens reviewer-subagent workflow — security/correctness/simplicity; all findings incorporated). Author: soma-improver. Baseline: `docs/multi-vps.md` (Phase 1).

## §0 What the review changed (so the rationale isn't lost)
The first draft proposed a general path-scoped `read <name> <relpath>` + `list-files`. The review **rejected it**:
- **Hardlink BLOCKER:** the B-lead runs as `ubuntu` — the *same* uid that owns `secrets.env`, `~/.ssh`, `~/.claude/.credentials.json` — and its project dir is `755` lead-writable. The lead can `ln /etc/claude-soma/secrets.env projects/<name>/notes.txt`; `realpath` keeps that path **inside** the project (a hardlink is not a symlink), so every confinement check passes and the secret is emitted. `realpath` cannot see hardlinks. Confinement is **illusory** under a shared uid.
- **TOCTOU BLOCKER:** validate-path-then-reopen-path races a lead-controlled symlink swap.
- **Use-case miss:** the transcript I actually want pull is `/var/log/claude-soma/<name>.log` — *outside* the project dir — so a project-confined `read` wouldn't even serve it.
- **Simplicity:** the only proven need is (a) message a remote lead, (b) pull its transcript. Both can be done with **guard-CONSTRUCTED** targets (no caller-supplied path), which keeps the guard's strongest invariant intact.

**Revised scope:** `send` (hardened) + `tail-log` (fixed NAME-derived target). General path-scoped read is **deferred** (fast-follow only if a concrete by-path artifact caller appears; ad-hoc pulls use the admin-key rsync break-glass, as today). `list-files` **cut**.

## §1 Problem
The A→B forced-command guard speaks `spawn/resume/kill/capture/list/has-session/stat-transcript/rc-url`. It has **no `send`** (can't deliver a message into a remote lead's claude pane) and **no transcript-content pull** (`capture` = ~200 live pane lines; `stat-transcript` = size/mtime only). So the orchestrator can't message a B-lead or read its log — both hit live with algo-trader@vps-b.

## §2 `send <name> <b64>` — deliver a message to a remote lead
Mirror the LOCAL path (`server.send_to_project_impl`: `tmux send-keys -l <msg>` then `Enter`), hardened for the remote (where the b64 may originate from an A-side subagent, not just the trusted operator).
- **Contract:** `send <name> <b64>`, b64 = `base64 -w0` of the UTF-8 message. Guard branch:
  1. `[ "${#P[@]}" -eq 3 ] || deny "send needs 2 args"`.
  2. `name` ∈ NAME_RX; b64 ∈ B64_RX; `${#b64} ≤ MAX_B64_LEN`.
  3. **NUL guard on the decoded stream** (bash vars truncate at NUL): `printf %s "$B64" | "$BASE64" -d | LC_ALL=C grep -qU $'\x00' && deny "NUL in message"`.
  4. Decode → `MSG`. **Reject C0 control bytes** (raw ESC/^C/^U etc. reach the PTY even with `-l`): `case "$MSG" in *[$'\x00'-$'\x08'$'\x0b'-$'\x1f'$'\x7f']*) deny "control byte in message";; esac` (allow `\t`; **reject `\n`** too — a newline submits early and would let one `send` inject a second prompt; the orchestrator sends one logical line per call).
  5. `"$TMUX" -L "$SOCK" has-session -t "$SESS" 2>/dev/null || deny "no live session"` (inside the guard — one invocation, no extra ssh hop, no TOCTOU).
  6. Deliver as an **argv array** (separator is a *bare* `;` argv token, exactly like the spawn precedent `spawner`/guard `";" pipe-pane` — never a literal `\;` string, never `sh -c`):
     `cmd=( "$TMUX" -L "$SOCK" send-keys -t "$SESS" -l -- "$MSG" ";" send-keys -t "$SESS" Enter ); exec "${cmd[@]}"`
     `-l` = literal (no key-name lookup); `--` stops option parsing (leading-`-` messages safe); `MSG` is always ONE argv element.
- **Escalation-scan fix:** the existing P[0:5] forbidden-substring loop must **exclude the b64 token** (P[2]) for `send`, mirroring how it excludes the spawn brief at P[5] — else a base64 message incidentally containing `ExecStartPre` false-DENYs. Make the scan scan only verb+name for `send`/`tail-log`.

## §3 `tail-log <name>` — pull the remote lead's transcript (fixed target, no caller path)
- **Contract:** `tail-log <name>` (2 tokens, like `capture`). Guard branch:
  1. `[ "${#P[@]}" -eq 2 ] || deny "tail-log needs 1 arg"`; `name` ∈ NAME_RX.
  2. `LOG="${LOG_DIR}/${NAME}.log"` — the path STRING is **guard-constructed** from NAME (LOG_DIR=/var/log/claude-soma), so the *string* carries no caller-supplied path (no `..`/metachar — NAME_RX + CHARSET_RX). **BUT the LEAF is not trusted:** LOG_DIR is lead-writable (the lead runs as `ubuntu`), so a lead can plant a **symlink or hardlink** at `<name>.log` pointing at `secrets.env`/`~/.ssh`/`.credentials.json`. (This is the same shared-uid class that killed the general `read` — and it's why `tail-log` is unlike `capture`/`stat-transcript`/`rc-url`, which emit pane text / size+mtime / one URL line, never arbitrary file CONTENT.)
  3. **Leaf hardening (defeats symlink + hardlink, no TOCTOU):** read via a guard-owned `python3` (NAME-derived `LOG` is the only input): `os.open(LOG, O_RDONLY|O_NOFOLLOW)` (a symlink open fails → DENY), `os.fstat` requires `S_ISREG` AND `st_nlink == 1` (rejects hardlinks), then `lseek` to the last `MAX_READ_BYTES` and `base64` — all on ONE held fd (no recheck/reopen). On any failure → `deny`.
  4. `MAX_READ_BYTES` const (2 MB → ~2.7 MB b64); `base64`-out keeps it on the existing `text=True` ssh pipe. The same `_read_log_tail_safe` (O_NOFOLLOW + S_ISREG + nlink==1) hardens `get_transcript_impl`'s LOCAL branch too.
  - **Deferred root-cause fix:** the durable defeat of the shared-uid leaf problem is a uid split / root-owned transcript dir (lead's pipe-pane writes via a root-owned drop-box). Until that lands, O_NOFOLLOW + `st_nlink==1` is the enforced minimum bar (and `/var/log` is root-owned, so LOG_DIR itself isn't lead-replaceable).

## §4 Orchestrator-side integration (host-aware)
- `build_guard_command`: add `send` → `send <name> <b64msg>`; `tail-log` → `tail-log <name>`.
- `RemoteRunner`: add `.send(name, message)` (b64-encodes); `.tail_log(name) -> bytes` (`base64.b64decode(stdout)` — **default `validate=False`**, tolerates ssh trailing newline).
- `server.send_to_project_impl`: **host-aware** — registry `host==local` → existing tmux send-keys; else `RemoteRunner(host).send(...)`. Call `_reg().touch(name)` after BOTH branches on success (the remote branch must not drop the idle-clock bump — the exact bug `touch_project_impl` fixed locally).
- New `get_transcript`/`tail_log` impl + MCP tool: host-aware (local = read `/var/log/claude-soma/<name>.log` tail; remote = `RemoteRunner.tail_log`).

## §5 Security invariants (asserted by the guard matrix)
1. Guard never `sh -c`/`eval`s **caller-supplied** input; the only `sh -c` (tail-log pipe) takes guard-owned argv only; every other leaf is a tmux/printf argv token.
2. `send`: NUL-rejected on the decoded stream; C0 control bytes (incl. `\n`) rejected; delivered via `send-keys -l -- "$MSG"` with the chain separator a bare `;` argv token; MSG always a single argv element ⇒ no key interpretation, no flag/again-no command injection.
3. **No verb takes a caller-supplied filesystem path** (the path STRING is always guard-constructed from NAME_RX'd `<name>`). For `tail-log`, which streams file CONTENT from a lead-writable dir, the LEAF is additionally hardened against a planted symlink/hardlink via `O_NOFOLLOW` + `S_ISREG` + `st_nlink == 1` on a single held fd. (The other read-ish verbs — capture/stat-transcript/rc-url — need no leaf hardening because they never emit arbitrary file bytes.) Durable fix = uid split / root-owned transcript dir (deferred).
4. All inputs bounded by NAME_RX + B64_RX + MAX_B64_LEN + MAX_READ_BYTES; the escalation-substring scan never scans any b64 payload.
5. Orchestrator key stays forced-command-only — these are verbs on the same guard, not a shell.

## §6 Tests
- **NEW guard-matrix harness** (`tests/scripts/test_guard_matrix.py` or a bats driver — does not exist yet): sets `SSH_ORIGINAL_COMMAND`, runs the guard against a private tmux socket + temp LOG_DIR, asserts ACCEPT/DENY **and side effects**. Must-have cases:
  - `send`: ACCEPT valid (assert a `cat`-running session receives exactly `MSG\n`); ACCEPT b64 whose decode contains `ExecStartPre` (regression for the escalation-scan window); DENY control-byte message, DENY `\n` message, DENY NUL, DENY bad name, DENY wrong arg-count.
  - `tail-log`: ACCEPT (assert output b64-decodes to `tail -c MAX` of the log); DENY missing log, DENY bad name, DENY wrong arg-count.
- **Python units** (mirror `tests/mcp_servers/test_remote_runner.py`): `RemoteRunner.send`/`.tail_log` encoding + decode (feed stdout with a trailing newline); `send_to_project_impl` host-routing (mock RemoteRunner) asserting the remote branch is taken AND `touch` is called.

## §7 Rollout
- Ship the patched guard to B (`enroll-host vps-b` re-run, idempotent — or scp the guard). B = only remote host today (algo-trader).
- Guard + RemoteRunner + tests land with no channel restart. The **live** host-aware `send_to_project_impl` for the running bot needs a channel reload → **operator-gated restart**, flagged, not done unilaterally.

## §8 Phasing (each gated by an adversarial reviewer subagent)
- **2a:** guard `send` (hardened) + `RemoteRunner.send` + host-aware `send_to_project_impl` + guard-matrix + py tests → review → commit.
- **2b:** guard `tail-log` + `RemoteRunner.tail_log` + host-aware transcript MCP tool + tests → review → commit.
- **Deferred (documented, not built):** general path-scoped `read` (would reintroduce realpath/symlink/hardlink/TOCTOU machinery + a uid split to be safe) and `list-files`. Ad-hoc artifact pulls use the admin-key rsync break-glass until a concrete by-path caller exists.
- MILESTONE per phase; flag the B guard re-ship + the channel restart for the live send path.
