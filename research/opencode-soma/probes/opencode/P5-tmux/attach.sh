#!/usr/bin/env bash
# P5 variant: the send-keys/scrape hack against `opencode attach <url>` (a TUI attached to a
# standalone `opencode serve`), not the self-hosting `opencode --port` TUI used in run.sh. N=5
# messages; success = the typed message reached the server (GET /session/{id}/message) AND the
# reply token appears in the attached pane (column-aware). Model lfm2.5-thinking (quality
# irrelevant to send-keys), recorded; falls back to whatever is loaded.
set -u
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P5-tmux/attach.sh"
HERE="$(cd "$(dirname "$0")" && pwd)"
SERVE_PORT=$(( ${OCSOMA_PORT_BASE:-47100} + 53 ))
PROBE_ID=P5-attach; SANDBOX_FRESH=1
MODEL=ollama/lfm2.5-thinking
OC_DEFAULT_MODEL="$MODEL"
source "$HERE/../sandbox.sh"
EVD="$OCSOMA_EVID/P5-tmux"; mkdir -p "$EVD"
TM="tmux -L ${OCSOMA_TMUX_PREFIX:-ocsoma-pe-}attach"
BASE="http://127.0.0.1:$SERVE_PORT"

( cd "$PROJ" && exec "$OC" serve --port "$SERVE_PORT" --hostname 127.0.0.1 ) > "$SANDBOX/serve.out" 2>&1 &
SP=$!
for i in $(seq 1 100); do curl -s -o /dev/null "$BASE/global/health" && break; sleep 0.2; done
SID=$(python3.12 -I "$OCSOMA_PROBES/ocdrive.py" --base "$BASE" new --title p5-attach)

ev_open P5-tmux/attach.log "send-keys/capture-pane against 'opencode attach <url>' to a serve; N=5" "$MODEL"
ev "serve on $BASE, session $SID; attach: tmux ... opencode attach $BASE --session $SID"
$TM kill-server 2>/dev/null
$TM new-session -d -s at -x 200 -y 50 -c "$PROJ" "$OC attach $BASE --session $SID"
sleep 6
$TM capture-pane -p -t at > "$EVD/attach-pane-start.txt"
ev_run cat "$EVD/attach-pane-start.txt"

cat > "$SANDBOX/cases.txt" <<CASES
a|ATTACH-A-OK|Reply with exactly ATTACH-A-OK and nothing else.
b|ATTACH-B-OK|He said "hi" and paid \$5 for \`ls\`. Reply with exactly ATTACH-B-OK and nothing else.
c|ATTACH-C-OK|First line.\nSecond line.\nReply with exactly ATTACH-C-OK and nothing else.
d|ATTACH-D-OK|Ignore this filler: alpha bravo charlie delta echo foxtrot. Reply with exactly ATTACH-D-OK and nothing else.
e|ATTACH-E-OK|Reply with exactly ATTACH-E-OK and nothing else.
CASES

scrape=0; deliver=0; n=0
while IFS='|' read -r cid token msg; do
  n=$((n+1)); text=$(printf '%b' "$msg")
  before=$($TM capture-pane -p -t at | grep -c '▣')
  $TM send-keys -t at -l "$text"; sleep 0.4; $TM send-keys -t at Enter
  status=timeout
  for i in $(seq 1 240); do
    pane=$($TM capture-pane -p -t at)
    printf '%s\n' "$pane" | python3.12 -I "$HERE/pane_check.py" "$token" && { status=ok-scrape; break; }
    now=$(printf '%s\n' "$pane" | grep -c '▣')
    if [ "$now" -gt "$before" ]; then
      # turn finished; column-aware check (sidebar shares rows)
      printf '%s\n' "$pane" | cut -c1-150 | python3.12 -I "$HERE/pane_check.py" "$token" && { status=ok-scrape; break; }
      status=turn-ended-token-not-in-visible-pane; break
    fi
    sleep 0.5
  done
  sleep 1
  recv=$(curl -s "$BASE/session/$SID/message" | jq -r --arg s "$text" '[.[] | select(.info.role=="user") | ([.parts[] | select(.type=="text" and ((.synthetic//false)|not)) | .text] | join(""))] | map(select(. == $s)) | length')
  [ "${recv:-0}" -ge 1 ] && deliver=$((deliver+1))
  case "$status" in ok-scrape) scrape=$((scrape+1));; esac
  $TM capture-pane -p -t at > "$EVD/attach-pane-$cid.txt"
  ev "### case $cid: scrape=$status, server received exact text=$([ "${recv:-0}" -ge 1 ] && echo yes || echo no)"
done < "$SANDBOX/cases.txt"
ev "## final conversation on the server (what attach delivered)"
ev_run sh -c "curl -s '$BASE/session/$SID/message' | jq -c '.[] | {role: .info.role, text: ([.parts[] | select(.type==\"text\") | .text] | join(\" \") | .[0:90])}'"
$TM kill-server 2>/dev/null
kill "$SP" 2>/dev/null; wait "$SP" 2>/dev/null
ev "summary: delivered-to-server $deliver/$n ; reply-scraped-from-pane $scrape/$n"
if [ "$deliver" = "$n" ] && [ "$scrape" -ge 1 ]; then
  ev_verdict PARTIAL "via opencode attach to a serve: all $n messages reached the server over the attached TUI (send-keys delivery 100%), replies scraped from the pane $scrape/$n (same sidebar/column + alt-screen caveats as run.sh); the HTTP API remains the robust path."
else
  ev_verdict PARTIAL "attach send-keys: delivered $deliver/$n, scraped $scrape/$n (see per-case lines)."
fi
ev_redact "$EV_LOG"
tail -1 "$EV_LOG"
