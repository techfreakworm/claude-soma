# opencode-soma: Start Here

This folder is the research answer to one question: can claude-soma — a Telegram personal assistant
with voice, a project orchestrator, scheduled routines, social posting and a dashboard — be rebuilt
as **opencode-soma**, with opencode 1.18.8 as the engine instead of Claude Code, on any model
(including a local one), installable by a stranger on Linux/macOS/Windows with no VPS. Nobody is
building opencode-soma yet; this is the feasibility study, the architecture, and the evidence behind
both.

## Verdict

Feasible. opencode 1.18.8 can be the engine on a local model with no Claude account, and every
load-bearing mechanism (HTTP+SSE serve per lead, crash resume, instant-ack queuing, plugin
gate/inject/log, MCP with per-agent scoping (holds against the model; a trusted HTTP caller can
re-grant per request, so the daemon is the only client and the password is the boundary), subagents
incl. background dispatch, SQLite usage) is verified. Headline numbers: 0 PARITY / 5 DEGRADED /
7 NEEDS-BUILD across the 12 surfaces; qwen3:32b
turn p50 7.5s / p90 50s, 4 destructive curation calls in 100 cases and 0 secrets stored even with the
scrub off, while the 1.2B lfm2.5 stored 5 secrets from 4 of 5 secret-bearing chats (16 dangerous
outcomes); tmux send-keys delivery 12/12 over 2 rounds but the scrape false-positives mid-turn 8/12,
vs HTTP 6/6; crash resume and per-agent MCP scoping both hold. What is lost is model quality (frontier
not tested), the hosted conveniences Claude Code gave for free (channels, auto-memory, Remote
Control), and turnkey unattended reliability on one local GPU.

**The single biggest risk is unattended 24/7 reliability on a local model** — silent judgement errors
plus opencode's hang classes: the second-writer session wedge, pending approvals with no server-side
timeout, and no default provider timeouts in 1.18.8 (desk). All of the above become an opencode-soma
daemon to build, not capabilities that ship out of the box.

## Reading order

1. `FEASIBILITY.md` — the 12-surface parity table and per-item verdicts (0 PARITY, 5 DEGRADED, 7 NEEDS-BUILD).
2. `ARCHITECTURE.md` — the opencode-soma daemon, the one-writer-per-serve rule, flows, the full
   claude-soma -> opencode-soma component mapping, and the `opencode-soma init` wizard spec.
3. `MEMORY-DESIGN.md` — the SQLite memory service (claude-soma writes no memory itself).
4. `PLATFORM-MATRIX.md` — Linux / macOS / Windows(WSL2) install and isolation.
5. `RISKS.md` — ranked risks, each with its cheapest early test.
6. `ROADMAP.md` — build order.
7. `evidence/opencode/SUMMARY.md` — one line per opencode probe (P1-P9) with its evidence path.

## Reproduce the key evidence

Run from the worktree root. Each command is deterministic and touches no model: the DoD gate and the
P2/P5 `analyze.sh` re-derive verdicts from saved artifacts, the memory bench builds a SQLite index
over a freshly generated 5k corpus in `$TMPDIR`, the timing table recomputes from the committed proxy
logs, and the isolation verify confirms nothing touched the operator's real opencode/claude dirs. The
full transcript is in `evidence/start-here/reproduce.log`; `probes/opencode/run-all.sh` re-runs every
opencode probe from scratch (~1h on qwen3:32b, which the verifier's rerun uses).

```bash
python3.12 -I research/opencode-soma/probes/check_dod.py
bash research/opencode-soma/probes/opencode/P2-hooks/analyze.sh
bash research/opencode-soma/probes/opencode/P5-tmux/analyze.sh
python3.12 research/opencode-soma/probes/memory/gen_memory.py --n-facts 5000 --out "$TMPDIR/sh-mem-5k"
python3.12 -I research/opencode-soma/probes/opencode/sqlite_mem_bench.py --data "$TMPDIR/sh-mem-5k" --n-facts 5000
python3.12 -I research/opencode-soma/probes/opencode/turn_timing.py research/opencode-soma/evidence/opencode
bash research/opencode-soma/probes/opencode/isolation.sh verify
```

Note: the last command rewrites the committed `evidence/opencode/isolation-check.log` and compares
against this machine's `$TMPDIR/ocsoma-pe/isolation` snapshot taken before the probes, so it exits 2
on a machine without that snapshot; it is the probe author's closing check, not a portable one. The
other commands are fully portable and read-only except the SQLite bench, which writes only to
`$TMPDIR`.

## Operator decisions needed

- **A throwaway Telegram bot token** for the one claim not verified end to end — the long-poll bridge
  (`probes/platform/telegram_longpoll_e2e.py`). Everything upstream and downstream of it is proven;
  only a live token round trip is missing.
- **Pin opencode 1.18.8 or move to 1.18.35.** All evidence here is 1.18.8; 1.18.35 is desk-reviewed
  only. The wizard refuses an unpinned/mismatched binary either way.
- **A Windows box for WSL2** if Windows is a target: it is desk-verified only (see PLATFORM-MATRIX.md).
- **PR / open-source handling**: whether opencode-soma ships as its own public repo and how upstream
  opencode is tracked.
