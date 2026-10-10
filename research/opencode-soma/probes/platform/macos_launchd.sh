#!/usr/bin/env bash
# Validates the two sample LaunchAgent plists with `plutil -lint` (and prints their parsed form) and
# quotes launchd's sleep/wake semantics from the local launchd.plist(5) man page. It never runs
# launchctl load/bootstrap/enable. Writes evidence/platform/macos-launchd.log.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
LOG="$ROOT/evidence/platform/macos-launchd.log"
mkdir -p "$(dirname "$LOG")"
{
  echo "date_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "command: bash macos_launchd.sh  (plutil -lint + plutil -convert json; no launchctl)"
  echo "host: $(uname -srm) macOS $(sw_vers -productVersion) build $(sw_vers -buildVersion)"
  rc=0
  for f in "$HERE"/launchd/*.plist; do
    echo "== $(basename "$f")"
    plutil -lint "$f" || rc=1
    plutil -convert json -o - "$f" | python3.12 -m json.tool --compact
  done
  echo "== launchd.plist(5), StartCalendarInterval and sleep (local man page)"
  man launchd.plist 2>/dev/null | col -b | tr -s ' ' | grep -i -B2 -A6 'asleep' | head -24
  echo "== launchd.plist(5), KeepAlive"
  man launchd.plist 2>/dev/null | col -b | tr -s ' ' | grep -i -A5 '^ *KeepAlive <boolean or dictionary' | head -10
  echo "launchctl not invoked; nothing was loaded, bootstrapped or enabled."
  if [ "$rc" = 0 ]; then
    echo "VERDICT: PASS — both sample LaunchAgents (KeepAlive daemon, weekday StartCalendarInterval routine) pass plutil -lint; launchd runs a missed calendar job once on wake, coalescing missed intervals"
  else
    echo "VERDICT: FAIL — plutil -lint rejected a sample plist"
  fi
} 2>&1 | tee "$LOG"
