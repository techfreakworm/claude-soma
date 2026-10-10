#!/usr/bin/env bash
# P5 (multi-round): claude-soma's tmux send-keys + capture-pane hack against the opencode TUI.
# A single-sample 5/6 flipped in the verifier's C10 rerun, so this runs P5_ROUNDS rounds x 6 messages
# in ONE invocation and bases each VERDICT on the aggregate, with a STRICT scrape checker that:
#   (1) excludes the right-hand sidebar columns (cut -c1-120) - the sidebar auto-TITLE echoes the
#       token, and
#   (2) accepts a reply only AFTER the server confirms the turn completed (HTTP) - the old checker
#       matched the title mid-turn, before any reply existed (the C10 false positive).
# It also records, per round, whether the OLD whole-line checker would have false-positived mid-turn.
# Delivery (send-keys -> server, verbatim) and the HTTP comparison are measured alongside.
set -u
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P5-tmux/run.sh"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROXY_PORT=$(( ${OCSOMA_PORT_BASE:-47100} + 55 )); TUI_PORT=$(( ${OCSOMA_PORT_BASE:-47100} + 51 ))
PROBE_ID=P5-tmux; SANDBOX_FRESH=1; OC_BASEURL="http://127.0.0.1:$PROXY_PORT/v1"
source "$HERE/../sandbox.sh"
cd "$PROJ" || exit 1
MODEL=ollama/qwen3:32b
ROUNDS=${P5_ROUNDS:-2}
EVD="$OCSOMA_EVID/P5-tmux"; mkdir -p "$EVD"
: > "$EVD/proxy.jsonl"
proxy_start "$PROXY_PORT" "$EVD/proxy.jsonl" || exit 1
TM="tmux -L ${OCSOMA_TMUX_PREFIX:-ocsoma-pe-}tui"
BASE="http://127.0.0.1:$TUI_PORT"
COL=120   # transcript column cutoff; the TUI sidebar (title/context) lives to the right of this

$TM kill-server 2>/dev/null
$TM new-session -d -s tui -x 200 -y 50 -c "$PROJ" "$OC --port $TUI_PORT --hostname 127.0.0.1 -m $MODEL"
for i in $(seq 1 100); do curl -s -o /dev/null "$BASE/global/health" && break; sleep 0.2; done
sleep 3

LONGCTX=$(python3.12 -I -c 'print(" ".join(["The quick brown fox jumps over the lazy dog number %d." % i for i in range(1, 40)]))')
cat > "$SANDBOX/cases.txt" <<EOF
short|TMUX-SHORT-OK|Reply with exactly TMUX-SHORT-OK and nothing else.
long|TMUX-LONG-OK|Here is some filler context you can ignore: $LONGCTX End of filler. Reply with exactly TMUX-LONG-OK and nothing else.
multiline|TMUX-MULTI-OK|First line of my message.\nSecond line of my message.\nReply with exactly TMUX-MULTI-OK and nothing else.
quotes|TMUX-QUOTES-OK|He said "hello" and she said 'bye'. Reply with exactly TMUX-QUOTES-OK and nothing else.
dollar|TMUX-DOLLAR-OK|Price is \$5 and the var is \$HOME and code is \`ls -la\` here. Reply with exactly TMUX-DOLLAR-OK and nothing else.
longreply|TMUX-LONGREPLY-END|Write the numbers from 1 to 70 as English words, one per line, and after the last line write TMUX-LONGREPLY-END on its own line.
EOF

tui_sid() { curl -s "$BASE/session" | jq -r 'map(select(.title != "p5-http")) | sort_by(.time.updated) | last | .id // empty'; }
completed_assistants() { local sid; sid=$(tui_sid); [ -z "$sid" ] && { echo 0; return; }; curl -s "$BASE/session/$sid/message" | jq '[.[] | select(.info.role=="assistant" and .info.time.completed != null)] | length'; }
check() { python3.12 -I "$HERE/pane_check.py" "$1"; }   # reads stdin, exit 0 if a reply-line == token

ev_open P5-tmux/tmux.log "tmux send-keys + capture-pane hack, $ROUNDS rounds x 6 messages; strict (sidebar-excluded, completion-gated) vs old whole-line scrape; delivery; HTTP comparison" "$MODEL (thinking off)"
ev "TUI: $TM new-session -d -s tui -x 200 -y 50 (opencode --port $TUI_PORT); rounds=$ROUNDS; sidebar cut at col $COL"
res="$SANDBOX/res.jsonl"; : > "$res"
for r in $(seq 1 "$ROUNDS"); do
  ev "## round $r"
  while IFS='|' read -r cid token msg; do
    text=$(printf '%b' "$msg")
    before=$(completed_assistants)
    $TM send-keys -t tui -l "$text"; sleep 0.3; $TM send-keys -t tui Enter
    # mid-turn (~1.5s, before completion): would the OLD whole-line checker false-positive on the title?
    sleep 1.5
    midpane=$($TM capture-pane -p -t tui)
    fp_mid=no; printf '%s\n' "$midpane" | check "$token" && fp_mid=yes
    # wait for the server to confirm the turn completed
    done_turn=no
    for i in $(seq 1 360); do
      [ "$(completed_assistants)" -gt "$before" ] && { done_turn=yes; break; }
      sleep 0.5
    done
    sleep 1
    pane=$($TM capture-pane -p -t tui)
    whole=no; printf '%s\n' "$pane" | check "$token" && whole=yes
    strict=no; printf '%s\n' "$pane" | cut -c1-"$COL" | check "$token" && strict=yes
    # delivery: did the server receive this exact text as a user message?
    tsid=$(tui_sid)
    deliv=$(curl -s "$BASE/session/$tsid/message" | jq --arg s "$text" '[.[] | select(.info.role=="user") | ([.parts[] | select(.type=="text" and ((.synthetic//false)|not)) | .text] | join(""))] | map(select(.==$s)) | length')
    [ "${deliv:-0}" -ge 1 ] && deliv=yes || deliv=no
    printf '{"round":%d,"case":"%s","delivered":"%s","strict":"%s","whole":"%s","fp_midturn":"%s","turn_completed":"%s"}\n' "$r" "$cid" "$deliv" "$strict" "$whole" "$fp_mid" "$done_turn" >> "$res"
    ev "round $r $cid: delivered=$deliv strict_scrape=$strict whole_scrape=$whole old_checker_fp_midturn=$fp_mid turn_completed=$done_turn"
    [ "$r" = 1 ] && { $TM capture-pane -p -t tui > "$EVD/pane-$cid.txt"; $TM capture-pane -p -t tui -S -2000 > "$EVD/pane-$cid-scrollback.txt"; }
  done < "$SANDBOX/cases.txt"
done

ev "## aggregate over $ROUNDS rounds x 6 messages"
ev_run python3.12 -I - "$res" <<'PY'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
n = len(rows)
d = sum(r["delivered"] == "yes" for r in rows)
st = sum(r["strict"] == "yes" for r in rows)
wh = sum(r["whole"] == "yes" for r in rows)
fp = sum(r["fp_midturn"] == "yes" for r in rows)
print(f"messages total: {n}")
print(f"delivered verbatim to server: {d}/{n}")
print(f"reply found by STRICT scrape (sidebar-excluded + completion-gated): {st}/{n}")
print(f"reply found by OLD whole-line scrape (post-completion): {wh}/{n}")
print(f"OLD whole-line checker FALSE-POSITIVE mid-turn (matched the sidebar title before any reply): {fp}/{n}")
per = {}
for r in rows:
    per.setdefault(r["case"], []).append(r)
for c, rs in per.items():
    print(f"  {c}: strict {sum(x['strict']=='yes' for x in rs)}/{len(rs)}, whole {sum(x['whole']=='yes' for x in rs)}/{len(rs)}, fp {sum(x['fp_midturn']=='yes' for x in rs)}/{len(rs)}")
PY

ev "## HTTP comparison: the same 6 cases over the server API (fresh session)"
HSID=$(python3.12 -I "$OCSOMA_PROBES/ocdrive.py" --base "$BASE" new --title p5-http)
hok=0; hn=0
while IFS='|' read -r cid token msg; do
  text=$(printf '%b' "$msg"); hn=$((hn+1))
  out=$(python3.12 -I "$OCSOMA_PROBES/ocdrive.py" --base "$BASE" turn --session "$HSID" --model "$MODEL" --text "$text" --timeout 300)
  ft=$(printf '%s' "$out" | jq -r '.final_text // ""')
  case "$ft" in *"$token"*) hok=$((hok+1)); st=ok;; *) st=miss;; esac
  ev "http $cid: $st"
done < "$SANDBOX/cases.txt"
ev "HTTP: $hok/$hn"
$TM kill-server 2>/dev/null
proxy_stop "$PROXY_PORT"

N=$(wc -l < "$res" | tr -d ' ')
DELIV=$(grep -c '"delivered":"yes"' "$res")
STRICT=$(grep -c '"strict":"yes"' "$res")
WHOLE=$(grep -c '"whole":"yes"' "$res")
FP=$(grep -c '"fp_midturn":"yes"' "$res")
ev "totals: delivered=$DELIV/$N strict=$STRICT/$N whole=$WHOLE/$N old-checker-midturn-false-positive=$FP/$N; HTTP=$hok/$hn"
if [ "$DELIV" = "$N" ] && [ "$hok" = "$hn" ] && { [ "$STRICT" -lt "$N" ] || [ "$WHOLE" -lt "$N" ] || [ "$FP" -gt 0 ]; }; then
  ev_verdict PARTIAL "send-keys DELIVERY is reliable ($DELIV/$N verbatim to the server over $ROUNDS rounds) and HTTP is $hok/$hn, but pane SCRAPING is lossy and can false-positive: strict scrape $STRICT/$N, old whole-line $WHOLE/$N, and the old checker matched the sidebar auto-title mid-turn $FP/$N times. The HTTP API supersedes the scrape."
elif [ "$DELIV" = "$N" ] && [ "$STRICT" = "$N" ] && [ "$FP" = 0 ]; then
  ev_verdict PASS "over $ROUNDS rounds, send-keys delivered $DELIV/$N verbatim and the strict scrape found every reply with no mid-turn false positive; HTTP $hok/$hn. (Single-sample runs still flip on the old checker; use the strict one.)"
else
  ev_verdict PARTIAL "delivered=$DELIV/$N strict=$STRICT/$N whole=$WHOLE/$N fp=$FP/$N http=$hok/$hn; see per-round lines."
fi
ev_redact "$EV_LOG"
tail -1 "$EV_LOG"

# post-hoc pane analysis across the saved round-1 panes
bash "$HERE/analyze.sh"
