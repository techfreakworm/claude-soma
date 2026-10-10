#!/usr/bin/env bash
# P5: claude-soma's tmux send-keys hack against the opencode TUI.
#  TUI detached in `tmux -L ${OCSOMA_TMUX_PREFIX:-ocsoma-pe-}tui` (200x50); for each message: send-keys -l <text>, Enter,
#  poll capture-pane -p until the expected token shows up and a new turn-footer line appears,
#  then extract the reply from the pane. The TUI is started with --port so the probe can ALSO
#  read what the server actually received (mangling check) and, afterwards, send the same
#  messages over the HTTP API for comparison.
set -u
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P5-tmux/run1-singlesample.sh (archived single-sample run; superseded by run.sh multi-round)"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROXY_PORT=$(( ${OCSOMA_PORT_BASE:-47100} + 55 )); TUI_PORT=$(( ${OCSOMA_PORT_BASE:-47100} + 51 ))
PROBE_ID=P5-tmux; SANDBOX_FRESH=1; OC_BASEURL="http://127.0.0.1:$PROXY_PORT/v1"
source "$HERE/../sandbox.sh"
cd "$PROJ" || exit 1
MODEL=ollama/qwen3:32b
EVD="$OCSOMA_EVID/P5-tmux"; mkdir -p "$EVD"
: > "$EVD/proxy.jsonl"
proxy_start "$PROXY_PORT" "$EVD/proxy.jsonl" || exit 1
TM="tmux -L ${OCSOMA_TMUX_PREFIX:-ocsoma-pe-}tui"
BASE="http://127.0.0.1:$TUI_PORT"

$TM kill-server 2>/dev/null
$TM new-session -d -s tui -x 200 -y 50 -c "$PROJ" "$OC --port $TUI_PORT --hostname 127.0.0.1 -m $MODEL"
for i in $(seq 1 100); do curl -s -o /dev/null "$BASE/global/health" && break; sleep 0.2; done
sleep 3
$TM capture-pane -p -t tui > "$EVD/pane-start.txt"

ev_open P5-tmux/tmux.log "tmux send-keys -l + Enter into the opencode TUI, scrape capture-pane -p; success rate and failure modes; HTTP comparison" "$MODEL (thinking off)"
ev "TUI: tmux -L ${OCSOMA_TMUX_PREFIX:-ocsoma-pe-}tui new-session -d -s tui -x 200 -y 50 \"opencode --port $TUI_PORT --hostname 127.0.0.1 -m $MODEL\""
ev "## pane right after start"
ev_run cat "$EVD/pane-start.txt"

LONGCTX=$(python3.12 -I -c 'print(" ".join(["The quick brown fox jumps over the lazy dog number %d." % i for i in range(1, 40)]))')
# id|expected-token|message (message may contain \n escapes for the multi-line case)
cat > "$SANDBOX/cases.txt" <<EOF
short|TMUX-SHORT-OK|Reply with exactly TMUX-SHORT-OK and nothing else.
long|TMUX-LONG-OK|Here is some filler context you can ignore: $LONGCTX End of filler. Reply with exactly TMUX-LONG-OK and nothing else.
multiline|TMUX-MULTI-OK|First line of my message.\nSecond line of my message.\nReply with exactly TMUX-MULTI-OK and nothing else.
quotes|TMUX-QUOTES-OK|He said "hello" and she said 'bye'. Reply with exactly TMUX-QUOTES-OK and nothing else.
dollar|TMUX-DOLLAR-OK|Price is \$5 and the var is \$HOME and code is \`ls -la\` here. Reply with exactly TMUX-DOLLAR-OK and nothing else.
longreply|TMUX-LONGREPLY-END|Write the numbers from 1 to 70 as English words, one per line, and after the last line write TMUX-LONGREPLY-END on its own line.
EOF

footer_count() { $TM capture-pane -p -t tui | grep -c '▣'; }
results="$SANDBOX/results.jsonl"; : > "$results"
while IFS='|' read -r cid token msg; do
  text=$(printf '%b' "$msg")
  before=$(footer_count)
  t0=$(python3.12 -I -c 'import time;print(time.time())')
  $TM send-keys -t tui -l "$text"
  sleep 0.3
  $TM send-keys -t tui Enter
  status="timeout"; first_footer=""
  for i in $(seq 1 360); do
    pane=$($TM capture-pane -p -t tui)
    now=$(printf '%s\n' "$pane" | grep -c '▣')
    [ -z "$first_footer" ] && [ "$now" -gt "$before" ] && first_footer="$i"
    if printf '%s\n' "$pane" | python3.12 -I "$HERE/pane_check.py" "$token"; then status="ok"; break; fi
    sleep 0.5
  done
  t1=$(python3.12 -I -c 'import time;print(time.time())')
  sleep 1
  # what the TUI's own server received for this case: number of user messages and exact-text match
  TSID=$(curl -s "$BASE/session" | jq -r 'sort_by(.time.updated) | last | .id')
  t0ms=$(python3.12 -I -c "print(int($t0*1000)-500)")
  recv=$(curl -s "$BASE/session/$TSID/message" | jq -c --argjson t0 "$t0ms" '[.[] | select(.info.role=="user" and .info.time.created >= $t0) | ([.parts[] | select(.type=="text" and ((.synthetic // false)|not)) | .text] | join(""))]')
  n_user=$(printf '%s' "$recv" | jq 'length')
  exact=$(printf '%s' "$recv" | jq --arg s "$text" 'map(. == $s) | any')
  printf '%s\n' "$pane" > "$EVD/pane-$cid.txt"
  $TM capture-pane -p -t tui -S -2000 > "$EVD/pane-$cid-scrollback.txt"
  ev "### case $cid: status=$status wall=$(python3.12 -I -c "print(round($t1-$t0,1))")s (turn-footer first seen at poll ${first_footer:-never}); server received $n_user user message(s), exact text match=$exact"
  ev "received: $(printf '%s' "$recv" | head -c 400)"
  ev_run cat "$EVD/pane-$cid.txt"
  printf '{"case":"%s","token":"%s","status":"%s","wall_s":%s,"user_msgs":%s,"exact":%s}\n' "$cid" "$token" "$status" "$(python3.12 -I -c "print(round($t1-$t0,1))")" "${n_user:-null}" "${exact:-null}" >> "$results"
  sleep 2
done < "$SANDBOX/cases.txt"

ev "## scrollback check: tmux history (-S -2000) of the alt-screen TUI for the long reply"
ev_run python3.12 -I - "$EVD/pane-longreply.txt" "$EVD/pane-longreply-scrollback.txt" <<'PY'
import re, sys
ones = "one two three four five six seven eight nine".split()
teens = "ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
tens = "twenty thirty forty fifty sixty seventy".split()
words = ones + teens + [t if not o else f"{t}[- ]{o}" for t in tens for o in [""] + ones][: 70 - 19]
for path in sys.argv[1:]:
    txt = open(path, encoding="utf-8", errors="replace").read().lower()
    lines = [re.sub(r"^[\s\u2502\u2503|]*", "", l).strip(" .") for l in txt.splitlines()]
    seen = sum(1 for w in words if any(re.fullmatch(w, l) for l in lines))
    print(f"{path.rsplit('/',1)[-1]}: {len(txt.splitlines())} lines captured, {seen}/70 number lines visible")
PY
ev "## what the TUI's server actually received and answered (GET /session/{id}/message on the TUI's own --port)"
TSID=$(curl -s "$BASE/session" | jq -r 'sort_by(.time.updated) | last | .id')
ev "TUI session: $TSID"
ev_run sh -c "curl -s '$BASE/session/$TSID/message' | jq -c '.[] | {role: .info.role, text: ([.parts[] | select(.type==\"text\") | .text] | join(\" \") | .[0:160]), text_len: ([.parts[] | select(.type==\"text\") | .text] | join(\" \") | length)}'"
ev "## results (scrape)"
ev_run cat "$results"

ev "## HTTP comparison: the same cases over the TUI's own server API (new session; replies read via GET /message)"
HSID=$(python3.12 -I "$OCSOMA_PROBES/ocdrive.py" --base "$BASE" new --title p5-http)
hres="$SANDBOX/http-results.jsonl"; : > "$hres"
while IFS='|' read -r cid token msg; do
  text=$(printf '%b' "$msg")
  out=$(python3.12 -I "$OCSOMA_PROBES/ocdrive.py" --base "$BASE" turn --session "$HSID" --model "$MODEL" --text "$text" --timeout 300)
  ft=$(printf '%s' "$out" | jq -r '.final_text // ""')
  wall=$(printf '%s' "$out" | jq -r '.wall_s')
  case "$ft" in *"$token"*) st=ok;; *) st=token-missing;; esac
  ev "### http case $cid: status=$st wall=${wall}s reply_len=${#ft} reply_head=$(printf '%s' "$ft" | head -c 80 | tr '\n' ' ')"
  printf '{"case":"%s","status":"%s","wall_s":%s,"reply_len":%s}\n' "$cid" "$st" "$wall" "${#ft}" >> "$hres"
done < "$SANDBOX/cases.txt"
ev_run cat "$hres"
$TM kill-server 2>/dev/null
proxy_stop "$PROXY_PORT"
n_ok=$(grep -c '"status":"ok"' "$results"); n=$(wc -l < "$results" | tr -d ' ')
h_ok=$(grep -c '"status":"ok"' "$hres"); hn=$(wc -l < "$hres" | tr -d ' ')
ev "scrape success: $n_ok/$n ; HTTP success: $h_ok/$hn"
if [ "$n_ok" = "$n" ]; then
  ev_verdict PASS "send-keys + capture-pane scraping got $n_ok/$n replies (HTTP: $h_ok/$hn); see per-case panes for rendering caveats."
elif [ "$n_ok" -ge 1 ]; then
  ev_verdict PARTIAL "send-keys + capture-pane scraping got $n_ok/$n replies vs HTTP $h_ok/$hn; failure modes per case above."
else
  ev_verdict FAIL "scraping got 0/$n replies (HTTP $h_ok/$hn)."
fi
ev_redact "$EV_LOG"
tail -1 "$EV_LOG"

# post-hoc column-aware re-check of the saved panes (separate log, no LLM)
bash "$HERE/analyze.sh"
