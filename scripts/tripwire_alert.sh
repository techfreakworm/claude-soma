#!/usr/bin/env bash
# scripts/tripwire_alert.sh
#
# Daily AI-bubble tripwire check -> short Telegram DM + long writeup on the relay.
#
# Fired by claude-soma-tripwire-alert.timer at 09:30 IST (04:00 UTC), ahead of the
# operator's 10:00 IST reconnect.
#
# The tripwires come from the reference analysis transcript:
#   https://files.mayankgupta.in/general-worker/linkedin-alvinfsc-ai-bubble-transcript.md
# and the follow-up US-market AI-bubble video summary:
#   https://files.mayankgupta.in/general-worker/us-market-ai-bubble-video-summary.md
#   (1) hyperscaler capex guidance (MSFT/GOOGL/AMZN/META)
#   (2) GPU rental prices + datacenter utilisation
#   (3) OpenAI revenue
#   (4) AI-related debt issuance + debt-funded share of capex
#   (5) off-balance-sheet commitments + SPV financing
#   (6) Oracle credit rating + RPO/backlog
#   (7) circular / vendor financing (Nvidia -> labs -> neoclouds -> back)
#   (8) AI unit economics + $/token
#
# Design notes
# ------------
# * The actual checking is a headless `claude -p` run with WebSearch. A static curl
#   can't do this: the signals live in earnings calls, FT/Reuters reporting and GPU
#   marketplace pages, and judging "did this cross a threshold" needs synthesis.
#   Auth is the stored Max OAuth credential in ~/.claude/.credentials.json (claude
#   refreshes it itself) -- NOT CLAUDE_CODE_OAUTH_TOKEN, which is commented out in
#   /etc/claude-soma/secrets.env. That is why the unit must run as User=ubuntu with
#   HOME=/home/ubuntu.
# * claude is asked for strict JSON so the shell can render the DM deterministically
#   instead of forwarding free prose. If the JSON doesn't parse we send a short
#   FAILED notice -- we never fabricate a reading.
# * The previous run's JSON is fed back in so the model can answer "what changed in
#   the last ~24h" against a real baseline rather than guessing.
# * House rule: the DM carries all 8 tripwire status lines (one per marker) -- this is
#   intentional per the user's decision on 2026-10-22; the full writeup still goes to
#   the relay and the DM carries the link.
#
# Usage:
#   tripwire_alert.sh              normal run
#   tripwire_alert.sh --test       same, but the DM is prefixed [TEST]
#   tripwire_alert.sh --dry-run    run the check + publish, print the DM, don't send
#
# Retries: a fast failure (<120s) is retried twice, 60s then 240s apart. Every
# failure's stdout is kept at <state>/last-fail-N.txt -- claude reports errors on
# stdout, so that file is the first place to look when a morning goes missing.
#
# Exit: 0 ok | 2 notify failed | 3 check failed (claude/JSON) | 4 already running

set -uo pipefail

# ---------------------------------------------------------------- config -------
CLAUDE_BIN="${HERMES_CLAUDE_BIN:-/home/ubuntu/.local/bin/claude}"
SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${TRIPWIRE_STATE_DIR:-/var/lib/claude-soma/tripwire}"
WORK_DIR="${STATE_DIR}/work"          # clean cwd: keeps /opt/claude-soma/CLAUDE.md out of the prompt
STATE_FILE="${STATE_DIR}/state.json"  # last successful reading
HISTORY="${STATE_DIR}/history.jsonl"  # append-only audit trail
LOG="${TRIPWIRE_LOG:-/var/log/claude-soma/tripwire-alert.log}"
LOCK="${STATE_DIR}/.lock"
CHAT_ID="${TRIPWIRE_CHAT_ID:-935376085}"
TG_ENV_FILE="${SOMA_TG_ENV_FILE:-/home/ubuntu/.claude/channels/telegram/.env}"
SECRETS_FILE="${SOMA_SECRETS_FILE:-/etc/claude-soma/secrets.env}"
RELAY_LEAD="${TRIPWIRE_RELAY_LEAD:-general-worker}"
CLAUDE_TIMEOUT="${TRIPWIRE_CLAUDE_TIMEOUT:-420}"
REFERENCE_URL="https://files.mayankgupta.in/general-worker/linkedin-alvinfsc-ai-bubble-transcript.md"

MODE="run"
case "${1:-}" in
    --test)    MODE="test" ;;
    --dry-run) MODE="dry" ;;
    "")        ;;
    *) echo "unknown arg: $1" >&2; exit 64 ;;
esac

mkdir -p "$STATE_DIR" "$WORK_DIR" "$(dirname "$LOG")" 2>/dev/null
log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$LOG"; }

# Single-flight: a wedged run must not stack on tomorrow's.
exec 9>"$LOCK"
if ! flock -n 9; then
    log "another run holds the lock; exiting"
    exit 4
fi

DATE_IST="$(TZ=Asia/Kolkata date +%Y-%m-%d)"
DATE_HUMAN="$(TZ=Asia/Kolkata date '+%d %b')"
STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ---------------------------------------------------------------- notify -------
# notify_lib gives us soma_notify (Discord primary). The operator explicitly wants
# this one on Telegram, so Telegram is primary here and soma_notify is the fallback.
NOTIFY_LIB="${NOTIFY_LIB:-${SCRIPTS_DIR}/notify_lib.sh}"
if [[ -r "$NOTIFY_LIB" ]]; then
    # shellcheck source=/dev/null
    source "$NOTIFY_LIB"
fi

_read_var() {   # _read_var FILE NAME -> last NAME=value, quotes stripped. Never sources.
    local file="$1" name="$2" line val=""
    [[ -r "$file" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
        case "$line" in "$name="*) val="${line#*=}" ;; esac
    done < "$file"
    val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
    printf '%s' "$val"
}

# tw_notify "msg" -> 0 if delivered. Telegram (explicit chat_id) first, then
# notify_lib's dual route. Token is never echoed or logged.
tw_notify() {
    local msg="$1" token http
    if [[ "$MODE" == "dry" ]]; then
        printf '%s\n' "--- dry-run, would notify ---" "$msg" "------"
        log "dry-run: suppressed notify (${#msg} chars)"
        return 0
    fi
    token="${TELEGRAM_BOT_TOKEN:-}"
    [[ -z "$token" ]] && token="$(_read_var "$TG_ENV_FILE" TELEGRAM_BOT_TOKEN)"
    [[ -z "$token" ]] && token="$(_read_var "$SECRETS_FILE" TELEGRAM_BOT_TOKEN)"
    if [[ -n "$token" ]]; then
        http="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 \
            -X POST "https://api.telegram.org/bot${token}/sendMessage" \
            --data-urlencode "chat_id=${CHAT_ID}" \
            --data-urlencode "text=${msg}" \
            --data-urlencode "disable_web_page_preview=true" 2>/dev/null)"
        token=""
        if [[ "$http" == "200" ]]; then log "telegram ok (chat ${CHAT_ID})"; return 0; fi
        log "telegram failed (http ${http:-000})"
    else
        log "telegram token not found"
    fi
    if declare -F soma_notify >/dev/null 2>&1 && soma_notify "$msg"; then
        log "fell back to soma_notify"
        return 0
    fi
    return 1
}

# ---------------------------------------------------------------- prompt -------
PRIOR='(none - this is the first run, establish the baseline)'
if [[ -s "$STATE_FILE" ]]; then
    PRIOR="$(cat "$STATE_FILE")"
fi

read -r -d '' THRESHOLDS <<'EOT'
TRIPWIRE 1 - HYPERSCALER CAPEX GUIDANCE
  Watch: MSFT and GOOGL (both report on ~28 Oct 2026), plus AMZN and META capex
  guidance, and any credible reporting on big-tech datacenter build plans.
  Baseline from the reference analysis (FY2026 plans): AMZN ~$220B; GOOGL $195-205B
  (raised twice from an initial $175-185B); MSFT ~$175B; META $130-145B. Combined
  2026 ~= $720-745B against ~$375B in 2025.
  GREEN = guidance maintained or raised; no credible report of a cut.
  AMBER = guidance held but the language turns cautious; a credible report of a
          pause, delay or "digestion" phase; a signed datacenter lease shelved.
  RED   = any of the four LOWERS its capex guidance range, or guides next-year capex
          below this year's actual; or two or more credibly report slowing/pausing
          builds.

TRIPWIRE 2 - GPU RENTAL PRICES + DATACENTER UTILISATION
  Watch: H100 / H200 / B200 hourly on-demand and spot rental rates (Vast.ai, RunPod,
  Lambda, CoreWeave, SFCompute, Together) plus utilisation and idle-capacity
  reporting. The reference analysis calls falling rental prices + idle chips the
  dotcom-bust tell.
  GREEN = rates flat or rising; capacity still described as tight or sold out.
  AMBER = H100 on-demand drifts down more than 10% month-over-month, or credible
          reports of softening utilisation or lengthening sales cycles.
  RED   = H100 on-demand falls more than 20% month-over-month, or credible reporting
          of idle fleets, cancelled GPU leases, or capacity being resold at a loss.

TRIPWIRE 3 - OPENAI REVENUE
  Baseline: late-Sep 2026 reports put the annualised run-rate near $70B; the
  Financial Times put it closer to $50B; OpenAI has since told investors it expects
  $70B or more by year-end 2026. OpenAI sits at the centre of the circular-financing
  loop (the IMF counted 14 such deals since 2023: ~$300B equity + $650B debt into
  weaker AI firms against $1.4T of PROMISED future revenue).
  GREEN = OpenAI or credible reporting reaffirms the ~$70B+ year-end trajectory.
  AMBER = conflicting numbers resurface; growth described as decelerating; a major
          compute commitment is restructured.
  RED   = run-rate revised DOWN materially (toward or below ~$50B), a funding round
          is pulled or downsized, or a flagship compute deal is cancelled.

TRIPWIRE 4 - AI-RELATED DEBT ISSUANCE + DEBT-FUNDED SHARE OF CAPEX
  Watch: new bond issuance by MSFT/GOOGL/AMZN/META/ORCL tagged to AI/datacenter spend,
  the share of capex funded by debt rather than cash flow, and any hyperscaler turning
  cash-flow negative. Reference video: the five issued $375B of debt from 2023 to
  Sept 2026 (more in three years than the $306B of 2008-2022 combined); ~30% of 2025
  capex and ~33% of planned 2026 capex is debt-funded; $143B issued by June 2026; of
  the future $3.4T, ~$1.4T is expected to be debt. Google turned cash-flow negative in
  Q2 2026 (a first since IPO) and issued an $85B FPO.
  GREEN = debt-funded share of capex flat/declining; issuance at or below prior yields.
  AMBER = debt-funded share climbs above ~33%, a new large tranche prices at a notably
          higher yield, or one more hyperscaler reports negative free cash flow.
  RED   = a hyperscaler's AI build is throttled by financing cost or access, a planned
          issuance is pulled/downsized, or two or more are debt-funding the majority of
          capex.

TRIPWIRE 5 - OFF-BALANCE-SHEET COMMITMENTS + SPV FINANCING
  Watch: growth in footnoted purchase commitments and signed-but-not-started leases, and
  new or existing data-centre SPVs (special-purpose vehicles). Reference video: ~$2.7T of
  real obligation sits off balance sheet; purchase commitments of Alphabet ~$200.7B,
  Microsoft $199B (by June 2027), Meta $135B (by end-2027); Meta/Blue Owl formed the
  Beignet Investor LLC SPV for the Hyperion DC, which issued $27B of A+ bonds (one notch
  below Meta's AA-) against a Meta 25-year 100%-capacity rental guarantee.
  GREEN = no new mega-SPV; SPV ratings stable; commitment footnotes growing in line with
          disclosed capex.
  AMBER = a new multi-billion SPV is formed, an SPV is placed on negative outlook, or
          off-B/S commitments jump materially faster than on-balance-sheet capex.
  RED   = an SPV is downgraded, a hyperscaler signals it will not renew/walks an SPV
          lease, or an SPV bond is accelerated/defaults.

TRIPWIRE 6 - ORACLE CREDIT RATING + RPO/BACKLOG
  Watch: Oracle (ORCL) S&P/Moody's rating and outlook, its RPO/backlog disclosure, its
  bond prices/yields, and the status of Stargate / Project Jupiter. Reference video:
  $638B RPO (~half earmarked for OpenAI alone); S&P BBB- (one notch above junk); bonds
  trading ~18% below par (6.9% coupon now yielding ~8.45%); a force-majeure notice sent
  to Project Jupiter builders over power delays; Stargate itself delayed.
  GREEN = rating and outlook held; RPO maintained; no new Stargate/Jupiter slippage.
  AMBER = a negative outlook is assigned, spreads widen further, or RPO conversion slips.
  RED   = a second agency cuts Oracle to junk (forcing by-law sellers), the RPO/backlog is
          materially written down, or an OpenAI/Stargate commitment is cancelled.

TRIPWIRE 7 - CIRCULAR / VENDOR FINANCING
  Watch: vendor-financing loops where a supplier funds its own customers' purchases
  (Nvidia -> OpenAI/Anthropic/neoclouds -> back to Nvidia) and the funders propping up the
  labs. Reference video: Nvidia put $35B into OpenAI and $10B into Anthropic (Huang
  indicated both were likely its last before IPO); the IMF counted 14 such deals since
  2023 (~$300B equity + $650B debt into weaker AI firms against $1.4T of PROMISED future
  revenue); SoftBank funded a $10B tranche of its $64B commitment by selling its Nvidia
  and T-Mobile stakes, pledging ~85% of Arm, a $40B bridge loan and $11B of junk bonds
  (total debt ~$159B, $40B to refinance this year).
  GREEN = no circular deal unwinds; funders reaffirm or extend their commitments.
  AMBER = a circular/vendor-financing deal is restructured or downsized, or a key funder
          (Nvidia, SoftBank) signals it is tapped out.
  RED   = a circular deal collapses or is pulled, a funding round for OpenAI/Anthropic
          goes unfilled, or a major funder cannot meet a committed tranche.

TRIPWIRE 8 - AI UNIT ECONOMICS + $/TOKEN
  Watch: per-seat/per-token unit economics and signs enterprises are pulling back AI spend.
  Reference video: a $20/mo subscriber can burn ~$700 of compute; a $200/mo ChatGPT Pro
  seat can still consume ~$14,000 (Anthropic ~$8,000); OpenAI would have needed a ~2.5x
  price rise ($20 -> ~$55) to break even on 2025's loss; Uber burned a full year's AI
  budget in three months then concluded more usage raised cost without raising output;
  routine work is migrating to cheaper open-source models with ~zero switching friction
  ("the era of token maxing is over").
  GREEN = enterprise AI spend holding/growing; no forced price hikes; margins described
          as improving.
  AMBER = credible reporting of enterprises cutting AI budgets or capping seats, or token
          prices falling further with no offsetting margin improvement.
  RED   = a major lab is forced into a steep price hike or usage cap to stem losses, or
          multiple large enterprises publicly cut AI spend on poor ROI.

CRACK WATCH (context, not a tripwire)
  The reference analysis names CoreWeave (CRWV) the weak link - four tests, four
  fails: GAAP loss-making, 3 customers = 72% of revenue (largest 36%), ~$35B debt
  plus ~$16B operating leases, and the whole business IS AI capex. Offsetting: ~$104B
  contracted backlog at end-June plus 25B+ added early Q3. Note anything material
  about CRWV, and about AMD/Broadcom/Bloom warrant-funded demand, in one line.
EOT

read -r -d '' CONTRACT <<'EOT'
OUTPUT CONTRACT - reply with RAW JSON ONLY. No prose before or after, no code
fences. Exactly this shape:

{
  "asof": "YYYY-MM-DD",
  "tripwires": [
    {
      "id": "capex",
      "label": "Capex",
      "status": "GREEN",
      "line": "one line, MAX 95 chars, concrete - numbers and dates over adjectives; say what changed in the last ~24h, or 'no change' if nothing material",
      "detail": "2-5 sentences of what you found and why it scores that way",
      "sources": ["https://..."]
    },
    { "id": "gpu", "label": "GPU rent", "status": "...", "line": "...", "detail": "...", "sources": [] },
    { "id": "openai", "label": "OpenAI rev", "status": "...", "line": "...", "detail": "...", "sources": [] },
    { "id": "debt", "label": "Debt load", "status": "...", "line": "...", "detail": "...", "sources": [] },
    { "id": "offbalance", "label": "Off-B/S", "status": "...", "line": "...", "detail": "...", "sources": [] },
    { "id": "oracle", "label": "Oracle", "status": "...", "line": "...", "detail": "...", "sources": [] },
    { "id": "circular", "label": "Circular fin", "status": "...", "line": "...", "detail": "...", "sources": [] },
    { "id": "unitecon", "label": "Unit econ", "status": "...", "line": "...", "detail": "...", "sources": [] }
  ],
  "red_flags": ["only for a threshold ACTUALLY crossed per the rules above; empty array if none"],
  "crack_watch": "one line on CoreWeave / warrant-funded demand, or 'nothing new'",
  "changed_24h": true,
  "summary": "2-4 sentences: the overall state of the three tripwires today"
}

RULES
- status is exactly one of GREEN, AMBER, RED, per the thresholds. Do not invent a
  fourth value.
- red_flags is for a threshold crossing under the stated rules, NOT for general
  bearish news. An empty array is the correct and expected answer most days.
- "line" MUST be <= 95 characters. It is rendered into a Telegram DM verbatim.
- Every factual claim needs a real URL you actually retrieved in "sources". If you
  could not verify a tripwire, say so in "line" and "detail" and set status to the
  prior run's status - do NOT guess a number.
- Prefer primary sources (investor-relations pages, earnings transcripts, the
  marketplace's own pricing page) over aggregator commentary.
EOT

PROMPT="You are the daily monitor for three AI-bubble tripwires. Today is ${DATE_IST} (IST).

Use web search to establish the CURRENT status of each tripwire below, then judge it
against the stated thresholds. Search for news from the last 24-48 hours first, then
confirm the underlying numbers. Be skeptical of recycled headlines: check the date on
anything you cite.

Reference analysis this watchlist is derived from: ${REFERENCE_URL}

${THRESHOLDS}

PREVIOUS RUN (your baseline for 'what changed in the last ~24h'):
${PRIOR}

${CONTRACT}"

# ---------------------------------------------------------------- check --------
log "run start (mode=${MODE}, date=${DATE_IST})"

# Retry policy. A *fast* failure (< FAST_FAIL_SECS) means the request was rejected
# before any real work started -- a racing OAuth refresh, a usage-limit window, a
# transient 5xx. Those are worth retrying with backoff: on 2026-10-19 and -10-20
# the run exited rc=1 in ~5s while the same command worked by hand minutes later.
# A *slow* failure is a research run that genuinely timed out; retrying that just
# burns another timeout, so we stop and report.
FAST_FAIL_SECS="${TRIPWIRE_FAST_FAIL_SECS:-120}"
# shellcheck disable=SC2206
BACKOFFS=(${TRIPWIRE_BACKOFFS:-60 240})
RAW=""; RC=0; ATTEMPT=0; SNIP=""
while :; do
    ATTEMPT=$((ATTEMPT + 1))
    T0=$SECONDS
    RAW="$(cd "$WORK_DIR" && timeout --kill-after=30 "$CLAUDE_TIMEOUT" \
        "$CLAUDE_BIN" -p "$PROMPT" --allowedTools "WebSearch,WebFetch" --output-format text 2>>"$LOG")"
    RC=$?
    ELAPSED=$((SECONDS - T0))
    if [[ $RC -eq 0 && -n "$RAW" ]]; then
        log "attempt ${ATTEMPT} ok (${ELAPSED}s)"
        break
    fi
    # Keep every failure's output. claude prints its error to STDOUT, not stderr,
    # so discarding RAW here is exactly what made the first two failures
    # undiagnosable (67 and 59 chars, both lost).
    printf '%s' "$RAW" > "${STATE_DIR}/last-fail-${ATTEMPT}.txt"
    SNIP="$(printf '%s' "$RAW" | tr '\n' ' ' | cut -c1-200)"
    log "attempt ${ATTEMPT} FAILED (rc=${RC}, ${ELAPSED}s, ${#RAW} chars): ${SNIP:-<no output>}"
    IDX=$((ATTEMPT - 1))
    if [[ $ELAPSED -ge $FAST_FAIL_SECS ]]; then
        log "slow failure (${ELAPSED}s >= ${FAST_FAIL_SECS}s) - not retrying"
        break
    fi
    if [[ $IDX -ge ${#BACKOFFS[@]} ]]; then
        log "retry budget exhausted after ${ATTEMPT} attempts"
        break
    fi
    log "fast failure - retrying in ${BACKOFFS[$IDX]}s"
    sleep "${BACKOFFS[$IDX]}"
done

if [[ $RC -ne 0 || -z "$RAW" ]]; then
    # Say how stale the picture now is. A bare "FAILED" hides a multi-day gap,
    # which is precisely when the gap matters.
    STALE="no prior reading on file"
    if [[ -s "$STATE_FILE" ]]; then
        LAST_OK="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("asof","?"))' "$STATE_FILE" 2>/dev/null)"
        STALE="last good reading: ${LAST_OK:-unknown}"
    fi
    log "check FAILED after ${ATTEMPT} attempt(s); ${STALE}"
    tw_notify "⚠️ AI tripwires ${DATE_HUMAN} — check FAILED after ${ATTEMPT} attempt(s), rc=${RC}. ${STALE}. Error: ${SNIP:-<no output>}"
    exit 3
fi
printf '%s' "$RAW" > "${STATE_DIR}/last-raw.txt"
rm -f "${STATE_DIR}"/last-fail-*.txt

# Extract the outermost JSON object (tolerates stray prose or ``` fences).
JSON="$(python3 - "${STATE_DIR}/last-raw.txt" <<'PY'
import json, sys
t = open(sys.argv[1], encoding='utf-8').read()
i, j = t.find('{'), t.rfind('}')
if i == -1 or j <= i:
    sys.exit(1)
obj = json.loads(t[i:j+1])
ids = {x.get('id') for x in obj.get('tripwires', [])}
if not {'capex', 'gpu', 'openai', 'debt', 'offbalance', 'oracle', 'circular', 'unitecon'} <= ids:
    sys.exit(2)
for x in obj['tripwires']:
    if x.get('status') not in ('GREEN', 'AMBER', 'RED'):
        sys.exit(3)
json.dump(obj, sys.stdout)
PY
)"
if [[ $? -ne 0 || -z "$JSON" ]]; then
    log "JSON parse/validate FAILED; raw kept at ${STATE_DIR}/last-raw.txt"
    tw_notify "⚠️ AI tripwire check ${DATE_HUMAN} — ran, but the result did not parse. Raw output kept at ${STATE_DIR}/last-raw.txt"
    exit 3
fi
printf '%s' "$JSON" > "${STATE_DIR}/latest.json"
log "check ok ($(printf '%s' "$JSON" | wc -c) bytes json)"

# ---------------------------------------------------------------- writeup ------
WRITEUP="${WORK_DIR}/ai-tripwires-${DATE_IST}.md"
python3 - "${STATE_DIR}/latest.json" "$WRITEUP" "$DATE_IST" "$STAMP" "$REFERENCE_URL" <<'PY'
import json, sys
src, out, date_ist, stamp, ref = sys.argv[1:6]
d = json.load(open(src, encoding='utf-8'))
DOT = {'GREEN': '🟢', 'AMBER': '🟡', 'RED': '🔴'}
L = []
L.append(f"# AI-bubble tripwires — {date_ist}\n")
L.append(f"_Generated {stamp} by `tripwire_alert.sh` (headless Claude + web search). "
         f"Watchlist derived from [the reference analysis]({ref})._\n")
flags = d.get('red_flags') or []
if flags:
    L.append("## 🚨 Red flags\n")
    for f in flags:
        L.append(f"- **{f}**")
    L.append("")
else:
    L.append("**No threshold crossed today.**\n")
if d.get('summary'):
    L.append(f"{d['summary']}\n")
L.append("| # | Tripwire | Status | Reading |")
L.append("|---|----------|--------|---------|")
for n, t in enumerate(d.get('tripwires', []), 1):
    s = t.get('status', '?')
    line = (t.get('line') or '').replace('|', '\\|')
    L.append(f"| {n} | {t.get('label','?')} | {DOT.get(s,'⚪')} {s} | {line} |")
L.append("")
L.append("## Detail\n")
for n, t in enumerate(d.get('tripwires', []), 1):
    s = t.get('status', '?')
    L.append(f"### {n}. {t.get('label','?')} — {DOT.get(s,'⚪')} {s}\n")
    L.append(f"{t.get('detail','')}\n")
    srcs = t.get('sources') or []
    if srcs:
        L.append("Sources:\n")
        for u in srcs:
            L.append(f"- {u}")
        L.append("")
if d.get('crack_watch'):
    L.append("## Crack watch — CoreWeave & warrant-funded demand\n")
    L.append(f"{d['crack_watch']}\n")
L.append("---\n")
L.append("### Thresholds in force\n")
L.append("**1 Capex** — RED if MSFT/GOOGL/AMZN/META lowers its capex guidance range, "
         "guides next-year capex below this year's actual, or 2+ credibly report "
         "slowing builds. AMBER on cautious language, a pause/delay report, or a "
         "shelved lease.\n")
L.append("**2 GPU rent** — RED if H100 on-demand falls >20% MoM, or on credible "
         "reporting of idle fleets / cancelled GPU leases / capacity resold at a "
         "loss. AMBER at >10% MoM or softening utilisation.\n")
L.append("**3 OpenAI revenue** — RED if the run-rate is revised down toward or below "
         "~$50B, a round is pulled/downsized, or a flagship compute deal is "
         "cancelled. AMBER on conflicting numbers or a restructured commitment.\n")
L.append("**4 Debt load** — RED if an AI build is throttled by financing cost/access, "
         "an issuance is pulled/downsized, or 2+ hyperscalers debt-fund the majority of "
         "capex. AMBER if the debt-funded share tops ~33%, a tranche prices notably "
         "higher, or one more reports negative free cash flow.\n")
L.append("**5 Off-B/S** — RED if an SPV is downgraded, defaulted or walked, or a bond is "
         "accelerated. AMBER on a new multi-billion SPV, an SPV negative outlook, or "
         "off-balance-sheet commitments jumping faster than disclosed capex.\n")
L.append("**6 Oracle** — RED if a second agency cuts ORCL to junk, the RPO/backlog is "
         "materially written down, or a Stargate/OpenAI commitment is cancelled. AMBER "
         "on a negative outlook, widening spreads, or RPO-conversion slippage.\n")
L.append("**7 Circular fin** — RED if a circular/vendor-financing deal collapses or is "
         "pulled, an OpenAI/Anthropic round goes unfilled, or a major funder cannot meet "
         "a committed tranche. AMBER on a restructured/downsized deal or a funder "
         "signalling it is tapped out.\n")
L.append("**8 Unit econ** — RED if a lab is forced into a steep price hike or usage cap, "
         "or multiple large enterprises cut AI spend on poor ROI. AMBER on reported "
         "enterprise AI-budget cuts or token prices falling with no margin improvement.\n")
L.append(f"\n_changed in last ~24h: {d.get('changed_24h')}_\n")
open(out, 'w', encoding='utf-8').write("\n".join(L))
PY
if [[ ! -s "$WRITEUP" ]]; then
    log "writeup render failed"
fi

# ---------------------------------------------------------------- publish ------
URL=""
if [[ -s "$WRITEUP" ]]; then
    PUB_OUT="$(HERMES_LEAD_NAME="$RELAY_LEAD" "${SCRIPTS_DIR}/soma-publish" "$WRITEUP" 2>&1)"
    URL="$(printf '%s' "$PUB_OUT" | grep -oE 'https://[^[:space:]]+' | tail -1)"
    if [[ -n "$URL" ]]; then
        log "published ${URL}"
    else
        log "publish FAILED: ${PUB_OUT}"
    fi
fi

# ---------------------------------------------------------------- DM -----------
MSG="$(python3 - "${STATE_DIR}/latest.json" "$DATE_HUMAN" "${URL}" "$MODE" <<'PY'
import json, sys
src, human, url, mode = sys.argv[1:5]
d = json.load(open(src, encoding='utf-8'))
DOT = {'GREEN': '🟢', 'AMBER': '🟡', 'RED': '🔴'}
out = []
if mode == 'test':
    out.append('[TEST] 🔭 AI tripwires · ' + human)
else:
    out.append('🔭 AI tripwires · ' + human)
for t in d.get('tripwires', []):
    line = (t.get('line') or '').strip()
    if len(line) > 110:
        line = line[:107].rstrip() + '...'
    out.append(f"{DOT.get(t.get('status'),'⚪')} {t.get('label','?')}: {line}")
flags = d.get('red_flags') or []
if flags:
    out.append('🚨 RED FLAG: ' + flags[0])
    if len(flags) > 1:
        out.append(f'   (+{len(flags)-1} more in the writeup)')
elif not d.get('changed_24h'):
    out.append('Nothing material in 24h. No threshold crossed.')
else:
    out.append('No threshold crossed.')
if url:
    out.append('→ ' + url)
print("\n".join(out))
PY
)"

if [[ -z "$MSG" ]]; then
    log "DM render failed"
    tw_notify "⚠️ AI tripwire ${DATE_HUMAN} — check ran but the DM failed to render. Writeup: ${URL:-none}"
    exit 3
fi

if [[ "$MODE" == "dry" ]]; then
    echo "--- would send (${#MSG} chars) ---"
    echo "$MSG"
    echo "--- writeup: ${WRITEUP} ---"
    echo "--- url: ${URL:-none} ---"
    log "dry-run complete"
    exit 0
fi

# ---------------------------------------------------------------- state --------
# Persist only after a successful check so tomorrow diffs against a real baseline.
cp -f "${STATE_DIR}/latest.json" "$STATE_FILE"
python3 - "$STATE_FILE" "$STAMP" "$URL" >> "$HISTORY" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
d['_run_at'] = sys.argv[2]
d['_url'] = sys.argv[3]
print(json.dumps(d, separators=(',', ':')))
PY

if tw_notify "$MSG"; then
    log "run ok (${#MSG} chars dm, url=${URL:-none})"
    exit 0
fi
log "notify FAILED (telegram + fallback) (${#MSG} chars)"
exit 2
