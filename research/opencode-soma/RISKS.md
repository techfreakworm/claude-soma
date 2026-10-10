# opencode-soma risks

Ranked, most serious first. Each risk names the evidence it rests on (paths are under
`research/opencode-soma/` unless they start with `docs/` or `src/`), what it would do to the product,
and the cheapest test that would show it early. "desk" marks a claim no probe exercised.

1. **Local-model judgement degrades silently.** As orchestrator and as memory curator, a local model
   gets things wrong without any error:
   - qwen3:32b repeated a stale value after the system prompt was refreshed
     (`evidence/opencode/P2-hooks/inject.log`);
   - curating memory, it made 4 destructive calls in 100 planted cases (2 false merges, 2 false
     supersedes; `evidence/memory/curation-qwen3_32b-think-false.log`);
   - lfm2.5-thinking (1.2B) stored a planted Stripe key and recorded a corrected-away port as
     current (`evidence/memory/curation-lfm2.5-thinking-think-true.log`).

   Impact: wrong facts and leaked secrets enter memory, and wrong or missing tool calls go unnoticed
   unless something checks them.
   Cheapest early test: a 30-scenario scripted Telegram replay on qwen3:32b against the fake Bot API,
   counting correct tool calls, destructive memory actions and secrets reaching any store.
2. **Unattended hangs.**
   - A second writer on one session wedged it `busy` with no LLM traffic until `POST /abort`
     (`evidence/opencode/P1-headless/busy.log`, `run1/wedge-snapshot.log`).
   - `serve` holds a pending permission with no server-side timeout seen; the probe's client gave up
     after 40 s (`evidence/opencode/P6-permissions/unattended.log`).
   - The declared `permission.ask` hook never fires in 1.18.8
     (`evidence/opencode/P6-permissions/plugin-answer.log`).
   - The absence of default provider timeouts in 1.18.8 is desk research, not probed.

   Impact: a lead or the orchestrator sits busy forever and the user hears nothing.
   Cheapest early test: a lead with an `ask` rule and no client for 10 min, plus ollama stopped
   mid-turn. The daemon watchdog must detect busy longer than N min and abort or answer.
3. **One local GPU slot for everything.**
   - With ollama 0.33.3 defaults (`OLLAMA_NUM_PARALLEL=1`) the orchestrator, every lead and the
     curator queue on one model. Under a concurrent opencode workload, curation calls needing
     about 6 s of compute waited up to 218 s, 1,011 s in total
     (`evidence/memory/curation-qwen3_32b-think-false.log`); qwen3 turns ran p50 7.5 s, p90
     49.8 s (`evidence/opencode/conditions.log`).
   - A second model is not served until the first is unloaded. That can be a full keep-alive (5 to
     15 min) even when the first model is idle, followed by a 20 GB reload
     (`evidence/memory/ollama-two-models.log`; the lfm2.5 warm-up in
     `curation-lfm2.5-thinking-think-true.log` waited 528 s).

   Impact: replies to the user arrive minutes late whenever a lead is busy, and a separate small
   curator model is unusable.
   Cheapest early test: measure ack and reply latency of a Telegram message while one lead is
   mid-turn on the same model, then with two leads.
4. **opencode churn.** 27 releases followed the probed 1.18.8 in 10 weeks, up to 1.18.35
   (`evidence/platform/opencode-releases.log`). 1.18.x also carries a parallel v2 API with its own
   event bus and message store, and background subagents sit behind an experimental flag
   (`evidence/opencode/SUMMARY.md`).

   Impact: hooks, endpoints or event names the product relies on change under it.
   Cheapest early test: re-run the probe suite (`probes/opencode/run-all.sh`) against 1.18.35 and
   diff the verdicts.
5. **Local server exposure.**
   - Without `OPENCODE_SERVER_PASSWORD`, `opencode serve` starts unauthenticated ("server is
     unsecured") and exposes `POST /global/upgrade` (`evidence/opencode/P8-serve/serve.log`); with a
     password every route returns 401.
   - CORS is opt-in via `--cors` (`evidence/opencode/SUMMARY.md`). The default CORS policy of 1.18.8
     toward opencode.ai origins was not probed (desk).
   - A LAN dashboard adds another surface.

   Impact: a local process or LAN host drives the agent or upgrades the binary.
   Cheapest early test: curl the serve API and the dashboard from another LAN host without
   credentials (expect 401), and send a credentialed preflight from an opencode.ai origin (expect
   no access).
6. **Prompt injection with broad permissions.** In the P6 demo the model ignored the injected
   command when ungated, but tried it when gated, and the gate blocked it
   (`evidence/opencode/P6-permissions/danger.log`). Whether a model follows an injection is not
   stable from run to run.

   Impact: a fetched page or message makes an agent run a destructive command.
   Cheapest early test: re-run the P6 injection demo under the production gate policy and
   permission rules, ten times.
7. **Sleep and idle shutdown.** A sleeping laptop runs routines late: launchd coalesces missed
   calendar jobs into one run on wake and loses `StartInterval` firings
   (`evidence/platform/macos-launchd.log`). Telegram keeps undelivered updates for at most 24 hours
   (`evidence/platform/telegram-nat.log`). WSL2 shuts idle distros down after 15 s / 60 s by default
   (PLATFORM-MATRIX.md, desk).

   Impact: missed or very late reminders, and messages lost after a day asleep.
   Cheapest early test: sleep the Mac for 15 min with a reminder due, then check it fires exactly
   once on wake.
8. **Telegram 409 from a second poller.** Only one `getUpdates` consumer may hold a token. claude-soma
   already lost its poller this way (`docs/KNOWN_BUGS.md:14-66`; PLATFORM-MATRIX.md).

   Impact: the bot goes deaf while the process looks healthy.
   Cheapest early test: run `probes/platform/telegram_longpoll_e2e.py` twice at once on a throwaway
   token; the daemon must hold a lock file and refuse a second poller.
9. **Windows unverified.** Nothing ran on Windows. Only artifact availability is known
   (`evidence/platform/artifacts.log`), and v1 targets WSL2 only (PLATFORM-MATRIX.md).

   Impact: a stranger on Windows cannot install or keep the daemon alive.
   Cheapest early test: one Windows 11 WSL2 install following the keep-alive recipe, then the P0
   acceptance suite.
10. **Experimental background subagents.** `background=true` works, but only behind an experimental
    flag (`evidence/opencode/P4-subagents/background.log`).

    Impact: if P4 relies on it, an upgrade can remove or change it.
    Cheapest early test: a contract test for background dispatch run on every upgrade, with a
    fallback dispatch tool.
11. **Smaller machines.** A bare turn carries about 6.8K prompt tokens (p50 6,836, max 14,786;
    `evidence/opencode/conditions.log`), and qwen3:32b's cold first turn took about 31 s even on an
    M5 Max (`evidence/opencode/SUMMARY.md`).

    Impact: 16 GB laptops with 8B models may be too slow or too weak to orchestrate.
    Cheapest early test: run the P0 acceptance suite on a 16 GB laptop with an 8B model and record
    turn latency and correctness.
12. **Desktop browser sessions expire or get flagged.** A persistent profile keeps cookies and
    localStorage across relaunches (`evidence/platform/playwright-persist.log`). Session lifetime
    and bot detection on real social sites were not tested (desk).

    Impact: scheduled posting silently stops until the user logs in again.
    Cheapest early test: log in once through the persistent profile, then re-check the session
    after 7 days.

## Ranking notes

- **One GPU slot moved above opencode churn** (item 3, the lead's draft had it fourth). Its cost is
  measured and hits every user interaction: up to 218 s waiting for a 6 s job, and minutes for a
  model switch. Churn is mitigated cheaply by pinning 1.18.8 and contract tests.
- **Claims the evidence does not support, re-worded here:**
  - "no default provider timeouts in 1.18.8" (not probed, marked desk);
  - "CORS for *.opencode.ai" (only the opt-in `--cors` flag is evidenced);
  - "approvals never time out" (the evidence shows a pending approval held with no server-side
    timeout seen in 40 s);
  - "a separate V2 line" (no v2 release line exists; it is a parallel v2 API inside 1.18.x).
