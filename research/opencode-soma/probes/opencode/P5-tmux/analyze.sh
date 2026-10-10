#!/usr/bin/env bash
# P5 post-hoc pane analysis (no LLM): re-checks the panes run.sh saved, once with whole-line
# matching (what run.sh did live) and once column-aware (only the left transcript column, cut
# before the TUI's right sidebar), to separate scraper fragility from delivery failures.
set -u
export OCSOMA_PROBE_SCRIPT="research/opencode-soma/probes/opencode/P5-tmux/analyze.sh (after run.sh)"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROBE_ID=P5-tmux; source "$HERE/../sandbox.sh"
EVD="$OCSOMA_EVID/P5-tmux"
ev_open P5-tmux/pane-analysis.log "post-hoc: whole-line vs column-aware scraping of the saved TUI panes" "n/a (no LLM)"
ev "## sidebar layout: rows where transcript text and the right sidebar share a terminal line"
ev_run python3.12 -I - "$EVD/pane-short.txt" <<'PY'
import sys
for i, ln in enumerate(open(sys.argv[1], encoding="utf-8", errors="replace").read().splitlines()):
    if "TMUX-SHORT-OK" in ln:
        print(f"row {i}: {ln.rstrip()!r}")
PY
ev_run python3.12 -I - "$EVD" <<'PY'
import json, re, sys
evd = sys.argv[1]
cases = [("short", "TMUX-SHORT-OK"), ("long", "TMUX-LONG-OK"), ("multiline", "TMUX-MULTI-OK"),
         ("quotes", "TMUX-QUOTES-OK"), ("dollar", "TMUX-DOLLAR-OK"), ("longreply", "TMUX-LONGREPLY-END")]
def found(lines, tok):
    pat = re.compile(r"^[\s│┃║|]*[`'\"*]*" + re.escape(tok) + r"[`'\".*]*\s*$")
    return any(pat.match(l) for l in lines)
SIDEBAR_COL = 150  # transcript column ends before the sidebar on a 200-column pane
whole = col = 0
for cid, tok in cases:
    lines = open(f"{evd}/pane-{cid}.txt", encoding="utf-8", errors="replace").read().splitlines()
    w = found(lines, tok)
    c = found([l[:SIDEBAR_COL] for l in lines], tok)
    whole += w; col += c
    print(f"{cid:10} whole-line={'ok' if w else 'MISS'}  column-aware={'ok' if c else 'MISS'}")
print(f"whole-line {whole}/6, column-aware {col}/6")
PY
ok=$(grep -c 'column-aware 6/6' "$EV_LOG")
if [ "$ok" = 1 ]; then
  ev_verdict PARTIAL "every message was delivered intact and every reply reached the screen, but only column-aware parsing finds them all (the sidebar shares rows) and long replies are cut to the visible screen (alt-screen, no tmux scrollback)."
else
  ev_verdict FAIL "even column-aware parsing misses replies (see table)."
fi
tail -1 "$EV_LOG"
