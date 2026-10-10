# opencode-soma platform matrix

Can a stranger run opencode-soma on Linux, macOS or Windows with no VPS? Each cell is one of:

- **PASS / PARTIAL / FAIL**: measured by a probe in `probes/platform/` (transcript in
  `evidence/platform/`) or by probe-engineer (`evidence/opencode/`).
- **listed**: a published build exists (`evidence/platform/artifacts.log`), but nothing was run on
  that OS.
- **desk**: desk-research-only, sources at the end.

The only machine was an M5 Max Mac. Linux evidence comes from an ubuntu:24.04 arm64 container on
it. There is no Windows evidence beyond artifact listings. On Windows, v1 means WSL2 (see the
Windows decision under the matrix).

## Matrix

| | Linux | macOS | Windows (via WSL2) |
|---|---|---|---|
| **opencode engine** | PASS: installer `--version 1.18.8` in 3.8 s, 171 MB binary; `opencode run` answered PONG via the host's ollama in 14 s (`linux-container.log`) | PASS: host install used by every probe-engineer probe (`evidence/opencode/P0-setup/sandbox-verify.log`) | desk: the Linux build inside the distro, as opencode's docs recommend [1]; the Linux container is the closest evidence. Native Windows builds exist (`artifacts.log`) but are not the v1 path |
| **daemon supervision** | PASS (verify only): systemd user unit, `Restart=on-failure`; `systemd-analyze verify` clean in the container (`linux-container.log`). Running user services without a login session needs `loginctl enable-linger` (desk [2]) | PASS (lint only): LaunchAgent with `KeepAlive`; `plutil -lint` OK; never loaded (`macos-launchd.log`) | desk: the same systemd user unit inside WSL with `[boot] systemd=true`, plus the keep-alive below. Idle distros are shut down (`instanceIdleTimeout` 15 s, `vmIdleTimeout` 60 s defaults), and systemd services do not count as activity [3][4] |
| **autostart** | desk: `WantedBy=default.target` plus linger [2] | PASS (lint only): `RunAtLoad` in the same plist (`macos-launchd.log`). Installing it shows a "Background Items Added" notice (desk [5]) | desk: a Task Scheduler "At log on" task that starts the distro and holds it up (keep-alive below) [3][4][6] |
| **scheduled routines and sleep** | PASS (verify only): timer `OnCalendar=Mon..Fri 08:00`, `Persistent=true`; `systemd-analyze calendar` shows the next elapse (`linux-container.log`). Catch-up after downtime is documented, not measured (desk [2]) | PASS (doc quoted from the local man page): a missed `StartCalendarInterval` job runs once on wake, coalescing missed intervals; `StartInterval` firings during sleep are lost (`macos-launchd.log`) | desk: systemd timers inside WSL (`Persistent=true`) while the distro is up; Windows sleep pauses the VM, and Persistent timers catch up on resume [2] |
| **lead hosting** | PASS: tmux 3.4 create/send-keys/capture round trip in the container (`linux-container.log`) | PARTIAL: tmux delivered 12/12 messages verbatim to the opencode TUI over two rounds, and a scrape found 12/12 replies once each turn had completed. Mid-turn, a naive whole-line check matched the sidebar's auto-generated session title, which repeats the prompt's token, 8/12 times before any reply existed, so single-sample runs flip. Long replies are cut to the visible screen. The `opencode serve` HTTP API got 6/6 with neither failure (`evidence/opencode/P5-tmux/tmux.log`, `P8-serve/serve.log`) | desk: tmux and `opencode serve` inside WSL, as on Linux. There is no native tmux on Windows |
| **memory store (`bun:sqlite`)** | PASS: FTS5, recursive CTEs, JSON1, WAL; 50k-fact recall 0.9–1.6 ms p50 (`evidence/memory/bun-sqlite.log`) | PASS: same, 0.5–0.8 ms (`bun-sqlite.log`) | desk: Bun's Linux build inside WSL; the database must live on the WSL filesystem, not under `/mnt/c` |
| **Playwright, persistent desktop profile** | listed: manylinux x64/arm64 wheels (`artifacts.log`); not run. A headed login needs a display (desktop session, or Xvfb plus VNC as claude-soma does today) | PASS: cookie and localStorage survived a headless relaunch and a headed relaunch of the same user-data-dir; an empty control dir had neither. Chromium download 566 MB, 28 s (`playwright-persist.log`) | desk: the Linux wheels inside WSL; a headed login window through WSLg (Windows 11) |
| **STT (whisper.cpp)** | listed: Ubuntu x64/arm64 tarballs (`artifacts.log`). claude-soma runs whisper-cli on its Ubuntu VPS today (`src/claude_soma/mcp_servers/voice_stt/server.py:15-22`) | PASS: ggml-small.en, WER 0 on WAV and on a Telegram-style OGG/Opus note; 1.8–3.6 s for 5.6 s of audio while ollama shared the GPU. whisper-cli cannot read OGG itself, so ffmpeg is required (`stt-whisper.log`) | desk: the Ubuntu build inside WSL, on the CPU |
| **TTS (piper)** | listed: piper-tts manylinux x64/arm64 wheels (`artifacts.log`). claude-soma never wired voice replies end to end (below) | PASS: piper-tts 1.8.0 wheel; 0.10–0.12 s to synthesize 5 s once the model is loaded (1 s load), 1.35–3.5 s per CLI call; valid Ogg/Opus; whisper round trip WER 0 (`tts-piper.log`) | desk: the manylinux wheels inside WSL, including arm64. Native Windows has **no arm64 wheel** (`artifacts.log`) |
| **Telegram behind home NAT** | same protocol as macOS (long polling is outbound HTTPS) | PASS: host at 192.168.0.4 behind NAT; api.telegram.org answers a fake token with JSON 401 over outbound HTTPS; a 10 s held outbound request survives the NAT; the Bot API sentences are quoted (`telegram-nat.log`). The real 30–50 s hold needs a token: `probes/platform/telegram_longpoll_e2e.py`, written, not run | desk: same protocol; outbound HTTPS from WSL goes through Windows' NAT |
| **local models (ollama)** | listed: Linux x64/arm64 builds (`artifacts.log`); the container reached the host's ollama | PASS with a caveat: works (Metal). With defaults (`OLLAMA_NUM_PARALLEL=1`) a second model is not served while the first is busy (`evidence/memory/ollama-two-models.log`) | desk: ollama on the Windows side for GPU access (`OllamaSetup.exe` listed in `artifacts.log`), reached from WSL at `localhost` with mirrored networking or via the host IP [3][7] |
| **blockers** | headless servers have no display for headed Playwright logins | laptop sleep pauses everything: routines run late on wake, Telegram queues updates up to 24 h (`telegram-nat.log` quote) | nothing ran on Windows. The WSL keep-alive is mandatory. WSL gets 50% of RAM by default [3], which competes with a local model |

**Windows decision: WSL2 is the single supported Windows path for v1. Native Windows is not
supported in v1: untested, not impossible.** Reasons:

- (a) opencode's own docs recommend WSL [1].
- (b) Agents' bash tool calls assume a POSIX shell, while native opencode falls back to pwsh/cmd, so
  agent reliability degrades silently (desk).
- (c) It keeps one code path with Linux: systemd user units, `opencode-soma attach` for a human
  look, the same daemon.
- (d) piper-tts has no Windows arm64 wheel (`artifacts.log`).

**WSL2 keep-alive recipe** (desk-research-only, [3][4]):

1. `%UserProfile%\.wslconfig`: `[wsl2] vmIdleTimeout=-1`, and `[general] instanceIdleTimeout=-1` on
   WSL versions that support it.
2. `/etc/wsl.conf` in the distro: `[boot] systemd=true` (boot settings need Windows 11 or Server
   2022).
3. A Task Scheduler "At log on" task running
   `wsl.exe -d <distro> --exec /bin/sh -c "exec sleep infinity"` to hold the distro up.
4. ollama runs on the Windows side for GPU access. It is reached from WSL at `localhost:11434` with
   `networkingMode=mirrored`, or via the Windows host IP.
5. A headed Playwright login opens through WSLg (Windows 11).

**Gaps in claude-soma today that these rows fill:**

- **Voice replies were never wired end to end.** `skills/respond-with-voice/SKILL.md:9,19` calls
  `mcp__telegram__reply_voice`, but the Telegram plugin defines no such tool (its tool is `reply`),
  and nothing in the repo calls `sendVoice`. The TTS row is therefore new capability: the audio
  side is proven above, but the `sendVoice` upload still has to be built.
- **One-shot reminders do not survive a reboot.** `schedule_reminder`
  (`src/claude_soma/mcp_servers/hermes_api/server.py:194-238`) spawns a detached `sleep N; notify`
  process (`start_new_session=True`). opencode-soma's scheduler has to keep reminders in its
  database and catch up after sleep or reboot, like `Persistent=true` and launchd's coalesced runs.

## How claude-soma receives Telegram today

- **The bot is a long-poller.** `scripts/channel-claude.sh` starts claude with the Telegram channel
  plugin:
  - `:62-64` select `plugin:telegram@claude-plugins-official` and the bot-only settings file;
  - `:88-96` `exec claude --channels "${CHANNEL_PLUGIN}" --settings "${SETTINGS}"`;
  - the settings file enables only that plugin (`config/claude/channel-settings.telegram.json:2-3`).
- **The plugin uses grammY's long polling.** Read-only copy at
  `~/.claude/plugins/marketplaces/claude-plugins-official/external_plugins/telegram/server.ts`:
  - `:95` `new Bot(TOKEN)` and `:1009` `bot.start(...)`;
  - `:58-61` "Telegram allows exactly one getUpdates consumer per token".
- **The one-consumer rule is a known incident.** `docs/KNOWN_BUGS.md:14-66` (#1) describes new
  sessions hijacking the poller.
- **Setup uses getUpdates too.** `scripts/setup-telegram.sh:54-58` reads the operator's chat id with
  `getUpdates`.
- **No webhook route exists.**
  - The site `Caddyfile:13-44` routes only the dashboard and API: `/api/auth/*` and
    `/api/admin/upload/*` to :3000, `/api/*` to :9000, everything else to :3000.
  - `config/caddy/social.caddyfile:10` exposes `/oauth/*`, `/health` and `/api/webhooks/*` for the
    social service, which is not Telegram.

So claude-soma never needed an inbound port for Telegram. The VPS was for being always on, not for
reachability.

## Why long polling needs no inbound port and a webhook does

From the Bot API page (quoted verbatim in `telegram-nat.log`):

- "There are two mutually exclusive ways of receiving updates for your bot - the getUpdates method
  on one hand and webhooks on the other."
- **Long polling.** `getUpdates` is a request the bot opens to api.telegram.org, and Telegram holds
  it open for up to `timeout` seconds ("Timeout in seconds for long polling."). The answer comes
  back on that same outbound connection, which the NAT's connection tracking already knows, so
  nothing listens on the laptop.
- **Webhooks.** With `setWebhook`, "we will send an HTTPS POST request to the specified URL". That
  means Telegram connects in, on one of "443, 80, 88, 8443", so a laptop behind home NAT needs a
  port-forward, a tunnel or a relay.
- **The two modes exclude each other.** `getUpdates` "will not work if an outgoing webhook is set
  up", and updates wait on Telegram's side for at most 24 hours. A sleeping laptop catches up on
  wake if it was asleep for less than a day.

## Per-OS notes

- **Linux** is the closest to today's VPS: systemd user units and timers (`Persistent=true` for
  catch-up), tmux, the ubuntu builds of whisper.cpp, and piper wheels. The container proved
  install, engine, tmux and unit syntax. It did not prove a booted systemd, linger, or headed
  Playwright.
- **macOS** proved everything except actually loading the LaunchAgents:
  - per-user LaunchAgents with `KeepAlive` for the daemon;
  - one agent per routine with `StartCalendarInterval` (missed runs collapse into one run on wake);
  - or the daemon's own scheduler, provided it does the same catch-up on start and wake;
  - a desktop Chrome profile through Playwright replaces the VNC'd browser (persistence measured);
  - whisper and piper both run locally.
- **Windows** is untested. v1 supports it only through WSL2: the Linux path running inside the
  distro, plus the keep-alive recipe above. Native Windows (opencode's Windows binaries, a Task
  Scheduler daemon, `opencode serve` in place of tmux) is not ruled out, but it is not a v1 target.
  Before anyone promises Windows, it needs one real WSL2 run of this matrix's Linux probes.

## Desk-research sources

1. opencode on Windows: https://opencode.ai/docs/windows-wsl (WSL recommended, native works).
2. systemd: `loginctl enable-linger` (systemd-logind), and `Persistent=` in `systemd.timer(5)`:
   https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html.
3. WSL settings, updated 2026-09-16: https://learn.microsoft.com/en-us/windows/wsl/wsl-config
   - `[boot] systemd=true` (boot settings need Windows 11 or Server 2022);
   - `[wsl2] vmIdleTimeout` default 60000 ms;
   - `[general] instanceIdleTimeout` default 15000 ms ("Set to -1 to disable auto shutdown");
   - also https://learn.microsoft.com/en-us/windows/wsl/systemd.
4. WSL idle shutdown of services:
   - https://www.phoronix.com/news/Microsoft-WSL-systemd ("systemd services will NOT keep your
     WSL instance alive");
   - https://learn.microsoft.com/en-us/answers/questions/1192206/wsl2-cant-run-in-the-background-after-enabling-sys;
   - https://github.com/microsoft/WSL/issues/13291.
5. macOS 13+ background item notifications (Apple platform behaviour; not reproduced here).
6. Task Scheduler `StartWhenAvailable`:
   https://learn.microsoft.com/en-us/windows/win32/taskschd/tasksettings-startwhenavailable.
7. ollama on Windows: https://docs.ollama.com/windows.
8. Docker Desktop licence (relevant only if Neo4j were adopted; see MEMORY-DESIGN.md 11.3):
   https://docs.docker.com/subscription/desktop-license/.
