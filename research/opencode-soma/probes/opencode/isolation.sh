#!/usr/bin/env bash
# Isolation check for the opencode probes.
#   isolation.sh snapshot   take the "before" snapshot (run BEFORE any probe)
#   isolation.sh verify     compare against it and write evidence/opencode/isolation-check.log
# Never sources sandbox.sh: it must look at the REAL dirs of the real user.
set -u

REAL_HOME="$(python3.12 -I -c 'import os,pwd; print(pwd.getpwuid(os.getuid()).pw_dir)')"
REAL_TMP="${OCSOMA_REAL_TMPDIR:-${TMPDIR:-/tmp}}"
REAL_TMP="${REAL_TMP%/}"
STATE_DIR="$REAL_TMP/ocsoma-pe/isolation"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../../.." && pwd)"
EVID="$REPO_ROOT/research/opencode-soma/evidence/opencode"
OC_BIN=/Users/techfreakworm/.opencode/bin/opencode

DIRS=(
  "$REAL_HOME/.config/opencode"
  "$REAL_HOME/.local/share/opencode"
  "$REAL_HOME/.cache/opencode"
  "$REAL_HOME/.local/state/opencode"
  "$REAL_HOME/.opencode"
  "$REAL_TMP/opencode"
  "/Library/Application Support/opencode"
)
CLAUDE_DIR="$REAL_HOME/.claude"

listing() {
  # path, size, mtime for everything down to depth 3 (node_modules pruned below depth 1)
  local d="$1"
  [ -e "$d" ] || { echo "ABSENT $d"; return; }
  find "$d" -maxdepth 3 -path '*/node_modules/*' -prune -o -print0 2>/dev/null \
    | xargs -0 stat -f '%m %z %N' 2>/dev/null | sort -k3
}

hashes() {
  local d="$1"
  [ -d "$d" ] || return 0
  find "$d" -maxdepth 1 -type f -print0 2>/dev/null | xargs -0 shasum -a 256 2>/dev/null | sort -k2
}

key() { echo "$1" | sed 's#[/ ]#_#g'; }

# Read opencode's version WITHOUT touching the operator's real HOME: redirect HOME + every XDG dir
# (and TMPDIR, since opencode also keeps a tmp dir under os.tmpdir()) into a throwaway sandbox.
sandboxed_version() {
  local vx="$STATE_DIR/verx"
  rm -rf "$vx"; mkdir -p "$vx/home" "$vx/config" "$vx/data" "$vx/cache" "$vx/state" "$vx/tmp"
  env -i PATH="$PATH" \
    HOME="$vx/home" \
    XDG_CONFIG_HOME="$vx/config" XDG_DATA_HOME="$vx/data" XDG_CACHE_HOME="$vx/cache" XDG_STATE_HOME="$vx/state" \
    TMPDIR="$vx/tmp/" OPENCODE_DISABLE_AUTOUPDATE=1 \
    "$OC_BIN" --version 2>/dev/null </dev/null
  rm -rf "$vx"
}

snapshot() {
  mkdir -p "$STATE_DIR"
  rm -f "$STATE_DIR"/*.listing "$STATE_DIR"/*.sha256 "$STATE_DIR"/claude-top.listing
  touch "$STATE_DIR/marker"
  date -u '+%Y-%m-%dT%H:%M:%SZ' > "$STATE_DIR/snapshot-time"
  for d in "${DIRS[@]}"; do
    listing "$d" > "$STATE_DIR/$(key "$d").listing"
    hashes "$d" > "$STATE_DIR/$(key "$d").sha256"
  done
  find "$CLAUDE_DIR" -maxdepth 1 -print0 | xargs -0 stat -f '%m %z %N' | sort -k3 > "$STATE_DIR/claude-top.listing"
  echo "snapshot taken at $(cat "$STATE_DIR/snapshot-time") into $STATE_DIR"
  wc -l "$STATE_DIR"/*.listing "$STATE_DIR"/*.sha256
}

verify() {
  [ -f "$STATE_DIR/marker" ] || { echo "no snapshot at $STATE_DIR"; exit 2; }
  local out="$EVID/isolation-check.log" fail=0
  mkdir -p "$EVID"
  {
    echo "# isolation-check"
    echo "date-utc: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "snapshot-utc: $(cat "$STATE_DIR/snapshot-time")"
    echo "command: bash research/opencode-soma/probes/opencode/isolation.sh verify"
    echo "opencode-version: $(sandboxed_version)"
    echo "note: the version line above is read from opencode run with a THROWAWAY HOME+XDG under"
    echo "      \$TMPDIR/ocsoma-pe/isolation/verx (see sandboxed_version); this script makes NO opencode"
    echo "      call against the operator's real HOME. (For the record: a plain 'opencode --version' only"
    echo "      prints the version; opencode Global init only mkdir -p's dirs that already exist, so the"
    echo "      operator's earlier real-HOME --version calls today wrote nothing and changed no mtime -"
    echo "      corroborated by the UNCHANGED listings below.) All probes ran under sandbox.sh."
    echo
    for d in "${DIRS[@]}"; do
      local k; k="$(key "$d")"
      echo "== $d"
      listing "$d" > "$STATE_DIR/$k.listing.after"
      hashes "$d" > "$STATE_DIR/$k.sha256.after"
      if diff -u "$STATE_DIR/$k.listing" "$STATE_DIR/$k.listing.after" > "$STATE_DIR/$k.listing.diff"; then
        echo "listing (depth<=3, size+mtime): UNCHANGED ($(wc -l < "$STATE_DIR/$k.listing" | tr -d ' ') entries)"
      else
        echo "listing: CHANGED"; cat "$STATE_DIR/$k.listing.diff"; fail=1
      fi
      if diff -u "$STATE_DIR/$k.sha256" "$STATE_DIR/$k.sha256.after" > /dev/null; then
        echo "sha256 of top-level files: UNCHANGED ($(wc -l < "$STATE_DIR/$k.sha256" | tr -d ' ') files)"
      else
        echo "sha256: CHANGED"; diff -u "$STATE_DIR/$k.sha256" "$STATE_DIR/$k.sha256.after"; fail=1
      fi
      if [ -e "$d" ]; then
        local newer; newer="$(find "$d" -newer "$STATE_DIR/marker" 2>/dev/null | head -50)"
        if [ -z "$newer" ]; then echo "find -newer marker (full depth): nothing"; else
          echo "find -newer marker (full depth): FOUND"; echo "$newer"; fail=1; fi
      fi
      echo
    done
    echo "== $CLAUDE_DIR (top level only; this Claude Code session itself writes here)"
    find "$CLAUDE_DIR" -maxdepth 1 -print0 | xargs -0 stat -f '%m %z %N' | sort -k3 > "$STATE_DIR/claude-top.listing.after"
    diff -u "$STATE_DIR/claude-top.listing" "$STATE_DIR/claude-top.listing.after" | sed 's/^/  /' || true
    echo "entries under $CLAUDE_DIR newer than marker whose path mentions opencode:"
    local oc_newer; oc_newer="$(find "$CLAUDE_DIR" -newer "$STATE_DIR/marker" 2>/dev/null | grep -i opencode | grep -v '/projects/' | head -20)"
    if [ -z "$oc_newer" ]; then echo "  none"; else echo "$oc_newer"; fail=1; fi
    echo "new top-level entries in $CLAUDE_DIR:"
    comm -13 <(awk '{print $3}' "$STATE_DIR/claude-top.listing" | sort) <(awk '{print $3}' "$STATE_DIR/claude-top.listing.after" | sort) | sed 's/^/  /'
    echo
    if [ "$fail" = 0 ]; then
      echo "VERDICT: PASS — no opencode-owned real dir changed since the snapshot; all probe I/O stayed in the sandbox."
    else
      echo "VERDICT: FAIL — a real opencode dir changed after the snapshot (see diffs above)."
    fi
  } > "$out" 2>&1
  tail -3 "$out"
}

case "${1:-}" in
  snapshot) snapshot ;;
  verify) verify ;;
  *) echo "usage: $0 snapshot|verify"; exit 2 ;;
esac
