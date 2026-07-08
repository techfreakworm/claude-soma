# Multi-VPS Phase 3 (PLAN) — durable cross-host read of vps-b artifacts on VPS-A

**Status:** PLAN ONLY — do NOT implement until the operator reviews. Author: soma-improver (sequential-thinking + a 3-lens adversarial reviewer-subagent pass — reliability/security/simplicity; soma-brain pane is down). Baseline: `docs/multi-vps.md` (Phase 1), `2026-06-30-multi-vps-phase2-guard-send-read.md` (Phase 2).

> **Operator framing vs. recommendation — read this first.** You asked to *"permanently mount vps-b's `/home/ubuntu` onto A"* via a systemd automount. I planned that, then ran it through the review process you mandated. **The review found a security BLOCKER and a reliability BLOCKER that make a permanent full-home mount the wrong tool here**, and all three lenses independently recommended a different shape. This doc gives you the honest evaluation you asked for, recommends the safer mechanism, and **still includes the least-bad form of a live mount in §9** so you can overrule with full information. Nothing is implemented; this is the review artifact.

## §0 What the review changed (so the rationale isn't lost — mirrors the Phase-2 §0 style)
The first draft recommended **sshfs read-only mount of all of `/home/ubuntu` at `/mnt/vps-b`** (systemd automount + self-heal timer) **plus** an `rrsync` reports-mirror. The review rejected the **mount half**:

- **[BLOCKER — security] The full-home mount re-opens the exact exfil class Phase-2 refused.** B-side leads run as uid `ubuntu` — the same uid that owns the secrets. Mounting all of `/home/ubuntu` read-only onto A exposes, confirmed live on B (all `-rw-------`, owner `ubuntu`): `~/.ssh/id_ed25519` (B's **admin private key** = full shell back into B), `~/.claude/.credentials.json` (Anthropic OAuth), `~/.git-credentials` (GitHub PAT), and `projects/algo-trader/.env` + `.dhan-creds.env` (**live broker credentials**). On A these become readable by **all 18 same-uid `claude-soma-lead-*` processes** — on the *most-attackable* host (the bot/orchestrator that ingests operator + subagent input). Phase-2 went to extreme lengths (`O_NOFOLLOW`+`S_ISREG`+`st_nlink==1`) to stop a lead exfiltrating *one* file; a full-home mount hands over all of them at once. Read-access to B's `id_ed25519` is **admin-equivalent escalation**, so the §4 "strictly less privileged" claim was false in effect.
- **[BLOCKER — security] `internal-sftp -R` is read-only but NOT chrooted.** Without `ChrootDirectory`, the sftp namespace is all of B; a B-side symlink (`ln -s /etc/claude-soma/secrets.env ~/x`) or a `../../etc/...` traversal reaches `secrets.env` anyway — so "secrets.env lives at /etc, not /home" was false comfort. And `ChrootDirectory` **cannot** target the `ubuntu`-owned `/home/ubuntu` (chroot root must be root-owned).
- **[BLOCKER — reliability] The hang mitigations don't actually bound the hang.** `timeout 5 ls /mnt/vps-b || fusermount3 -uz` cannot clear a dead-daemon mount: a FUSE request with no server parks the `ls` in uninterruptible **D-state**, which *ignores SIGKILL*, so `timeout` never fires the `||` and the **self-heal timer itself accretes one D-state process per minute**. The real un-wedge is `echo 1 > /sys/fs/fuse/connections/<N>/abort`. Also: `ConnectTimeout` was missing (cold access to a down B blocks ~127s on the TCP SYN timeout, not 120s), and idle-unmount returns **EBUSY** on a wedged mount so it never fires when it matters. The "120s bounded window" was false.
- **[design contradiction] The self-heal probe nullifies the idle-unmount.** A 60s `ls` resets the 120s idle clock → the mount never idles → never auto-unmounts. The headline safety mechanism cancels itself.
- **[simplicity] The mount dodged "recommend ONE" and has almost no residual job.** Transcripts are already served by Phase-2 `tail-log` (shipped, 8813736); reports by the mirror in this very plan. The mount's only unique job is *occasional human browse of an arbitrary non-report, non-transcript file* — which does not justify standing D-state risk + a standing cred-read surface on the live channel host. It's also read-only, so it can't even "manage" (move/curate) files.
- **[correctness] The mirror was hardcoded to one of two B-leads.** B runs **both** `algo-trader` and `algo-researcher`; a mirror pinned to `algo-trader/reports` misses half the remote leads day one.

**Revised recommendation:** ship the **`rrsync` read-only reports-mirror, generalized to all B-lead report dirs, hardened against hang + symlink** as the ONE durable mechanism; **cut the permanent sshfs mount, its automount, self-heal timer, and the sftp key.** Ad-hoc browse stays on the admin-key rsync break-glass (already blessed in Phase-2) or an **on-demand** mount (§9), not standing infra on the channel host.

## §1 Problem
The orchestrator on **A** repeatedly needs B-side artifacts — EOD reports, transcripts, run outputs — and today every read is a bespoke ssh pull. Phase-2 `tail-log` already covers the live transcript on-demand. The residual durable gap is: **B-lead reports/artifacts should surface centrally on A (and on the relay) without per-file ssh pulls and without a hang-prone live mount on the channel host.**

**Prime directive:** A is the **channel host** (live bot + orchestrator). The operator-named failure mode — *stale mounts hanging processes* — is **catastrophic here**: a FUSE/NFS mount whose server vanishes parks any accessing process in uninterruptible **D-state** (ignores SIGKILL); if the orchestrator, the watchdog, or any of the 18 same-uid leads ever walks the mount, that process wedges. The only safe posture on this host is to **not have a standing remote mount at all** on the hot path.

## §2 Options matrix (read-only artifact access; B home = 3.1 GB; transport = Tailscale)
| Option | Reliability / hang risk | Auto-recover (reboot+drop) | Latency | Security | Verdict |
|---|---|---|---|---|---|
| **NFS** | hard-mount hangs (worst) | `_netdev` | good | **new `nfsd` on prod-financial B**; AUTH_SYS uid-trust weak | ✗ reject — daemon on B + weak auth |
| **sshfs / rclone mount** (FUSE/sftp) | D-state hang on dead daemon; mitigations are partial + complex (abort-knob, ConnectTimeout, namespace) | systemd `.automount` | per-op ssh RTT | full-home mount = cred exposure to all A leads; needs chroot it can't have | ✗ reject as **standing** infra on the channel host (✓ only as **on-demand**, narrowed — §9) |
| **periodic rsync mirror** (`rrsync -ro`, already on B) | **ZERO standing hang risk** (reads a LOCAL copy; transport hardened with `--timeout`+`ConnectTimeout`+unit `TimeoutStartSec`) | systemd timer (oneshot, no-overlap) | eventually-consistent (stale by interval) | read-only, path-locked by `rrsync`, `--safe-links`; one narrow key | ✓ **RECOMMENDED — the ONE mechanism** |

## §3 Recommendation — `rrsync` read-only reports-mirror (the ONE), no standing mount
**A systemd-timer `rrsync -ro` mirror of every B-lead's report dir → a local dir on A → the relay, hardened against transport hang and symlink escape. No permanent FUSE mount on the channel host.** This delivers the operator's durable goal (B artifacts visible on A + on the relay, no per-file ssh) with zero standing D-state risk, one narrow read-only key, and reuse of infrastructure that already exists (`rrsync` on B; `soma-publish`/relay on A). Ad-hoc human browse of arbitrary B files is handled **on-demand** (§9), not by standing infra.

Rationale: the mirror reads only LOCAL files on the hot path (un-hangable); `rrsync -ro` is path-locked + read-only server-side; it covers the only durable, programmatic need; and it sidesteps every BLOCKER above. `rclone`/sshfs remain available **on-demand** for the rare browse.

## §4 Security — one narrow key; the cred-exposure that killed the mount
- **Reports-mirror key** `~/.ssh/vps-b-reports-ro` (new, `chmod 600`). B `authorized_keys`, one line per lead report root (or a single wrapper that confines to a report-roots allowlist):
  `command="rrsync -ro /home/ubuntu/projects/algo-trader/reports",restrict,from="100.103.37.115" ssh-ed25519 <pub>`
  and the same for `…/algo-researcher/reports` (own key or a small `rrsync` multiplexer keyed to an allowlist of `<lead>/reports` dirs). `rrsync -ro` = read-only + path-locked to that subtree; `restrict` = no pty/forward/agent; `from=` pins A's tailnet IP.
- **Symlink safety:** the mirror rsync runs `--safe-links` (drops any symlink whose target escapes the tree) so a B-side `reports/x -> /etc/claude-soma/secrets.env` cannot pull secret bytes across; the publish step reads **regular files only**.
- **Honest limits (documented, not hand-waved):** (a) on a shared-uid host, "one narrow key" is not true compartmentalization — it sits in the same `ubuntu`-owned `~/.ssh` as the admin/guard keys, so any A-lead compromise yields all keys; real separation needs a uid split (deferred, tracked with the Phase-2 root-cause item). (b) `from=` defends against *other* tailnet nodes, not against A itself being compromised — it is hygiene, not a boundary. (c) The mirror key can read **only** the report dirs, never `~/.ssh`/creds — this is the whole point of dropping the full-home mount.
- The Phase-2 guard `soma-orchestrator` key and the admin `id_ed25519` are untouched.

## §5 Reports-mirror design (generalized, hang-hardened)
- **Discovery:** mirror **all** B-lead report dirs. Source the lead list from the host registry (`config/claude/hosts.json` / registry leads with `host==vps-b`), not a hardcoded project — today that's `algo-trader` + `algo-researcher`.
- **Local target:** `/var/lib/claude-soma/vps-b-reports/<lead>/` on A (92 GB free; reports ≈ 1.1 GB; `--delete-after` bounds it).
- **Transport hardening (so an unreachable B fails fast, never hangs the timer):**
  `rsync -a --safe-links --delete-after --partial-dir=.rsync-partial --timeout=30 -e 'ssh -i ~/.ssh/vps-b-reports-ro -o IdentitiesOnly=yes -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3' <key-forces-the-path> /var/lib/claude-soma/vps-b-reports/<lead>/`
  - `--timeout=30` + `ConnectTimeout=10` + `ServerAlive*` → a dead transport errors in ~30s instead of blocking on a dead socket.
  - `--delete-after` (not bare `--delete`) + `--partial-dir` → a partial/empty sync never wipes the local last-good mirror and never publishes a half-file.
- **systemd unit hygiene:** `Type=oneshot` service + `.timer` (`OnCalendar=*:0/15`, `Persistent=true`, `RandomizedDelaySec`); set `TimeoutStartSec=300` so even a wedged rsync is force-killed and the unit returns `inactive` (otherwise oneshot's "still activating" silently skips every future tick); `OnFailure=` → operator notify (`notify_lib.sh`/`operator_dm.py`) so a stuck/failed mirror is **visible**, not silent. No FUSE, no mount, nothing to wedge in D-state.

## §6 Read-only decision: **READ-ONLY** (data plane), writes/control stay on the guard (control plane)
The mirror is a pure read-only **data plane**; B is mutated only via the Phase-2 **guard** (`spawn`/`resume`/`send`). `rrsync -ro` enforces read-only server-side, so even a compromised A cannot clobber B's live trading files through this path.

## §7 Relay integration (vps-b reports surface centrally on files.mayankgupta.in — hang-free)
The relay root is `/var/lib/claude-soma/relay` (soma-publish copies there → served at the relay domain; B content already reaches it today by a manual route — the mirror automates that). After each successful mirror run (rsync **exit 0** only), `soma-publish` the new/changed reports under a host-scoped `vps-b/<lead>/` relay prefix (the relay namespace is flat/host-agnostic, so the prefix is additive). The relay path touches only LOCAL files → robust even when B is unreachable (serves last-good).

## §8 Phase-2 complement (clean separation; the mount is NOT standing infra)
Three non-overlapping channels, none of which can wedge the bot:
- **guard `send`/`tail-log`/`capture`** = orchestrator/bot **hot path** — programmatic, hang-free, least-privilege; control + on-demand live transcript.
- **`rrsync` reports-mirror → relay** = the durable **artifact/report feed** (this plan).
- **on-demand mount / admin-key rsync break-glass** (§9) = the rare ad-hoc human browse — invoked deliberately, torn down after, never standing.
This keeps Phase-3 *consistent* with Phase-2's own conclusion (Phase-2 §0 rejected a general path-scoped read under shared uid; a standing full-home mount would have quietly reopened that door with D-state risk on top). No guard verb is duplicated or conflicted.

## §9 If you still want a LIVE mount — the least-bad form (on-demand, NOT recommended as standing infra)
Should you overrule and require live browse of arbitrary B files from A, do it **on-demand**, narrowed, and hardened — never a permanent automount on the channel host:
- **Narrow the source** to a curated, secret-free subtree (e.g. `…/reports` or a deliberately-assembled browse dir), **never** raw `/home/ubuntu` (which exposes `~/.ssh`/creds — §0).
- **Private mount namespace / dedicated short-lived unit** so the mount is *not* visible to the orchestrator/bot/18 leads (makes "off the hot path" structural, not a doc-comment). A system-wide `/mnt/vps-b` is readable by every same-uid lead and one stray `find /` wedges the bot.
- **`-o ro,ConnectTimeout=10,reconnect,ServerAliveInterval=15,ServerAliveCountMax=3`**, pinned B host key (`StrictHostKeyChecking=yes` + a pre-seeded `known_hosts`, not `accept-new`).
- **Teardown that actually works:** `ExecStop`/cleanup must `echo 1 > /sys/fs/fuse/connections/<N>/abort` *then* `fusermount3 -uz` — and a **reboot-while-wedged drill** (kill the sshfs daemon, `systemctl reboot`, confirm A doesn't hang unmounting) before trusting it.
- **Simplest acceptable:** skip the mount entirely and use the **admin-key rsync break-glass** (already blessed in Phase-2 for ad-hoc pulls) or an on-demand `rclone mount` you start, use, and unmount in one session. Standing automount infra is only worth building if a concrete, repeated, *automated* by-path consumer appears — none does today.

## §10 Failure modes + mitigations (mirror)
| Failure | Mitigation |
|---|---|
| B unreachable (tailnet drop / B reboot) | `--timeout`+`ConnectTimeout` fail fast (~30s); `OnFailure` notify; relay serves last-good local mirror; **no D-state on A** (only local reads on the hot path) |
| rsync run exceeds interval | oneshot no-overlap + `TimeoutStartSec=300` force-kill → future ticks restored (avoids the silent-skip trap) |
| Partial/empty source | `--delete-after` + `--partial-dir`; publish gated on exit 0 → never wipes/publishes a half-file |
| Symlink escape in reports | `--safe-links` drops out-of-tree symlinks; publish reads regular files only |
| A disk fill | reports-only (~1.1 GB) on 92 GB free; `--delete-after` bounds it; alert >85% |
| Key leak | one read-only path-locked tailnet-pinned key; revoke = drop the `authorized_keys` line; documented shared-`~/.ssh` fate (uid-split deferred) |

## §11 Rollout (operator-gated; staged, reversible)
1. Gen `~/.ssh/vps-b-reports-ro` (`chmod 600`); operator adds the `rrsync -ro <lead>/reports` `authorized_keys` line(s) on B for `algo-trader` + `algo-researcher` (manual trust step).
2. Install the mirror oneshot + timer (`TimeoutStartSec`, `OnFailure` notify) + the post-success `soma-publish` hook; `daemon-reload`.
3. Verify: a B report appears under `/var/lib/claude-soma/vps-b-reports/<lead>/` and at the relay under `vps-b/<lead>/`; kill the transport mid-run and confirm fail-fast + notify + last-good preserved (no hang).
4. Soak: B reboot + tailnet drop → confirm the timer recovers and A never wedges.
**Rollback:** disable the timer + drop the `authorized_keys` line(s) (Phase-2 guard path + admin key unaffected). No mount to unwind.

## §12 Deferred / alternatives
- **On-demand `rclone mount`** (§9) if a real repeated browse need appears (better VFS/reconnect than sshfs; still on-demand, never standing on the channel host).
- **Generalize the mirror into `enroll-host`** so future hosts auto-provision the reports key + timer (Phase-3b productization, after this proves out).
- **Durable uid-split on A** (the shared-`ubuntu` root cause behind both this and the Phase-2 leaf-hardening) — the real fix that would make a narrowed mount and per-key compartmentalization actually enforceable; tracked with the Phase-2 root-cause item.
