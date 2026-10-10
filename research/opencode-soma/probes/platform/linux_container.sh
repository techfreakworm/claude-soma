#!/usr/bin/env bash
# Linux evidence from inside an ubuntu:24.04 arm64 container (ocsoma-me-linux) on this Mac:
#   - installs opencode 1.18.8 with the official installer (--version 1.18.8 --no-modify-path),
#   - runs `opencode --version` and one `opencode run` against the HOST's ollama
#     (host.docker.internal:11434), share disabled, autoupdate off,
#   - runs tmux inside the container (create, list, send-keys, capture, kill),
#   - runs `systemd-analyze verify` on a sample user unit for the opencode-soma daemon.
# The container is removed at the end. Writes evidence/platform/linux-container.log.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
LOG="$ROOT/evidence/platform/linux-container.log"
NAME=ocsoma-me-linux
OC_VERSION="${OC_VERSION:-1.18.8}"
MODEL="${MODEL:-lfm2.5-thinking}"
if [[ "$MODEL" == *thinking* ]]; then REASONING=true; MOPTS="{}"; else REASONING=false; MOPTS="{ \"reasoningEffort\": \"none\" }"; fi
mkdir -p "$(dirname "$LOG")"
now() { python3.12 -c 'import time; print(time.time())'; }
dx() { docker exec "$NAME" bash -lc "$1"; }
{
  echo "date_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "command: bash linux_container.sh   (OC_VERSION=$OC_VERSION MODEL=$MODEL)"
  echo "docker: $(docker version --format '{{.Server.Version}} ({{.Server.Os}}/{{.Server.Arch}})')  host: $(uname -srm)"
  docker rm -f "$NAME" >/dev/null 2>&1
  t0=$(now)
  docker run -d --name "$NAME" --platform linux/arm64 --add-host=host.docker.internal:host-gateway ubuntu:24.04 sleep infinity >/dev/null
  echo "container up in $(python3.12 -c "print(round($(now)-$t0,1))")s: $(dx 'cat /etc/os-release | grep PRETTY_NAME; uname -srm')"
  t0=$(now)
  dx 'apt-get update -qq >/dev/null && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates tmux systemd >/dev/null 2>&1; echo apt-rc=$?'
  echo "apt-get install curl ca-certificates tmux systemd: $(python3.12 -c "print(round($(now)-$t0,1))")s"
  echo "== opencode install (official installer, pinned)"
  t0=$(now)
  dx "curl -fsSL https://opencode.ai/install | bash -s -- --version $OC_VERSION --no-modify-path 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | tail -4"
  echo "install wall: $(python3.12 -c "print(round($(now)-$t0,1))")s"
  dx 'ls -la ~/.opencode/bin/; du -sh ~/.opencode/bin/opencode; file ~/.opencode/bin/opencode 2>/dev/null || true'
  echo "opencode --version: $(dx '~/.opencode/bin/opencode --version')"
  echo "== opencode run against host ollama"
  dx "mkdir -p ~/.config/opencode ~/proj && cd ~/proj && git init -q 2>/dev/null; cat > ~/.config/opencode/opencode.json <<'JSON'
{
  \"\$schema\": \"https://opencode.ai/config.json\",
  \"autoupdate\": false,
  \"share\": \"disabled\",
  \"enabled_providers\": [\"ollama\"],
  \"model\": \"ollama/$MODEL\",
  \"small_model\": \"ollama/$MODEL\",
  \"provider\": {
    \"ollama\": {
      \"npm\": \"@ai-sdk/openai-compatible\",
      \"name\": \"Ollama on the host\",
      \"options\": { \"baseURL\": \"http://host.docker.internal:11434/v1\" },
      \"models\": { \"$MODEL\": { \"name\": \"$MODEL\", \"tool_call\": true, \"reasoning\": $REASONING,
                    \"options\": $MOPTS, \"limit\": { \"context\": 32768, \"output\": 4096 } } }
    }
  }
}
JSON
cat ~/.config/opencode/opencode.json | head -8"
  echo "host ollama reachable from the container: $(dx 'curl -s --max-time 5 http://host.docker.internal:11434/api/version')"
  t0=$(now)
  dx "cd ~/proj && OPENCODE_DISABLE_AUTOUPDATE=1 OPENCODE_DISABLE_SHARE=1 setsid -w timeout 900 ~/.opencode/bin/opencode run --print-logs --log-level WARN --model ollama/$MODEL 'Reply with exactly the word PONG and nothing else. Do not use any tools.' > /tmp/oc-run.out 2>&1; echo opencode-run-rc=\$?; sed 's/\x1b\[[0-9;]*m//g' /tmp/oc-run.out | grep -v '^\s*$' | tail -25"
  echo "opencode run wall: $(python3.12 -c "print(round($(now)-$t0,1))")s"
  echo "== tmux inside the container"
  dx 'tmux -V; tmux -L ocsoma-me-t new-session -d -s lead1 "bash --norc"; tmux -L ocsoma-me-t send-keys -t lead1 "echo hello-from-tmux-\$((6*7))" Enter; sleep 1; tmux -L ocsoma-me-t ls; tmux -L ocsoma-me-t capture-pane -p -t lead1 | grep -m1 "hello-from-tmux-42" && echo tmux-roundtrip=ok; tmux -L ocsoma-me-t kill-server'
  echo "== systemd-analyze verify on a sample user unit"
  dx "mkdir -p ~/.config/systemd/user && cat > ~/.config/systemd/user/opencode-soma.service <<'UNIT'
[Unit]
Description=opencode-soma daemon (Telegram long-poll, scheduler, dashboard)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/root/.opencode/bin/opencode serve --hostname 127.0.0.1 --port 47299
Restart=on-failure
RestartSec=5
Environment=OPENCODE_DISABLE_AUTOUPDATE=1

[Install]
WantedBy=default.target
UNIT
cat > ~/.config/systemd/user/opencode-soma-digest.timer <<'UNIT'
[Unit]
Description=opencode-soma daily digest

[Timer]
OnCalendar=Mon..Fri 08:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT
cat > ~/.config/systemd/user/opencode-soma-digest.service <<'UNIT'
[Unit]
Description=opencode-soma daily digest run

[Service]
Type=oneshot
ExecStart=/root/.opencode/bin/opencode --version
UNIT
systemd-analyze --version | head -1
systemd-analyze verify ~/.config/systemd/user/opencode-soma.service ~/.config/systemd/user/opencode-soma-digest.timer ~/.config/systemd/user/opencode-soma-digest.service; echo verify-rc=\$?
systemd-analyze calendar 'Mon..Fri 08:00' --iterations=2 2>&1 | head -6"
  docker rm -f "$NAME" >/dev/null
  echo "cleanup: container $NAME removed ($(docker ps -a --filter name=$NAME --format '{{.Names}}' | wc -l | tr -d ' ') left)"
} 2>&1 | tee "$LOG"
ok_ver=$(grep -c "^opencode --version: $OC_VERSION" "$LOG")
ok_run=$(( $(grep -c -x 'PONG' "$LOG") > 0 && $(grep -c 'opencode-run-rc=0' "$LOG") > 0 ))
ok_tmux=$(grep -c 'tmux-roundtrip=ok' "$LOG")
ok_sd=$(grep -c 'verify-rc=0' "$LOG")
if [ "$ok_ver" -ge 1 ] && [ "$ok_run" -ge 1 ] && [ "$ok_tmux" -ge 1 ] && [ "$ok_sd" -ge 1 ]; then
  v="PASS"; m="opencode $OC_VERSION installed and answered via host ollama; tmux round-trip ok; systemd-analyze verify clean"
else
  v="PARTIAL"; m="version=$ok_ver run=$ok_run tmux=$ok_tmux systemd_verify=$ok_sd (see log)"
fi
echo "VERDICT: $v — ubuntu:24.04 arm64 container: $m" | tee -a "$LOG"
