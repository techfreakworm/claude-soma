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
gate/inject/log, MCP with per-agent scoping, subagents incl. background dispatch, SQLite usage) is
verified; what is lost is frontier-model quality, the hosted conveniences Claude Code gave for free
(channels, auto-memory, Remote Control), and turnkey unattended reliability on one local GPU — all
of which become an opencode-soma daemon to build, not capabilities that ship out of the box.

## Reading order

1. `FEASIBILITY.md` — the 12-surface parity table and per-item verdicts (1 PARITY, 4 DEGRADED, 7 NEEDS-BUILD).
2. `ARCHITECTURE.md` — the opencode-soma daemon, the one-writer-per-serve rule, flows, the full
   claude-soma -> opencode-soma component mapping, and the `opencode-soma init` wizard spec.
3. `MEMORY-DESIGN.md` — the SQLite memory service (claude-soma writes no memory itself).
4. `PLATFORM-MATRIX.md` — Linux / macOS / Windows(WSL2) install and isolation.
5. `RISKS.md` — ranked risks, each with its cheapest early test.
6. `ROADMAP.md` — build order.
7. `evidence/opencode/SUMMARY.md` — one line per opencode probe (P1-P9) with its evidence path.

## Reproduce the key evidence

Run from the worktree root. Each command is deterministic and touches no model; the first runs the
gate live, the rest surface a committed probe verdict or recompute the timing table. The full
transcript is in `evidence/start-here/reproduce.log`, and `probes/opencode/run-all.sh` re-runs every
opencode probe from scratch (~1h on qwen3:32b).

```bash
printf '%s' '{"tool_name":"WebFetch","tool_input":{"url":"http://x"}}' | python3.12 scripts/orchestrator_gate.py
tail -n1 research/opencode-soma/evidence/opencode/P8-serve/serve.log
tail -n1 research/opencode-soma/evidence/opencode/P2-hooks/gate.log
tail -n1 research/opencode-soma/evidence/opencode/P1-headless/crash-serve.log
grep -m1 VERDICT research/opencode-soma/evidence/memory/bench-5000.log
tail -n1 research/opencode-soma/evidence/opencode/P5-tmux/tmux.log
python3.12 -I research/opencode-soma/probes/opencode/turn_timing.py research/opencode-soma/evidence/opencode
```

Isolation (every probe ran in a sandbox; this confirms nothing touched the operator's real opencode
or claude dirs):

```bash
bash research/opencode-soma/probes/opencode/isolation.sh verify
```

## Operator decisions needed

- **A throwaway Telegram bot token** for the one claim not verified end to end — the long-poll bridge
  (`probes/platform/telegram_longpoll_e2e.py`). Everything upstream and downstream of it is proven;
  only a live token round trip is missing.
- **Pin opencode 1.18.8 or move to 1.18.35.** All evidence here is 1.18.8; 1.18.35 is desk-reviewed
  only. The wizard refuses an unpinned/mismatched binary either way.
- **A Windows box for WSL2** if Windows is a target: it is desk-verified only (see PLATFORM-MATRIX.md).
- **PR / open-source handling**: whether opencode-soma ships as its own public repo and how upstream
  opencode is tracked.
