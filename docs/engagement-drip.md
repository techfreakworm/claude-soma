# Engagement Drip System

Mechanical hourly system that pops social engagement comment drafts from a queue
and routes them through an operator-review step before posting. Zero LLM tokens
on the hourly path — all intelligence lives in the queue entries themselves, which
are written by the `social-manager` agent separately.

---

## Lifecycle

```
queued  →  pending_review  →  approved  →  posted
                          ↘               ↘
                           declined        failed
```

1. **queued** — `social-manager` appends entries to `queue.jsonl`.
2. **pending_review** — hourly drip pops 1 X + 1 LinkedIn entry, sets status,
   regenerates the review page, and DMs the operator via Telegram.
3. **approved** — operator replies `approve <id>` or `approve all`; orchestrator
   calls `engagement-approve.sh`.
4. **posted** — after successful posting, orchestrator calls
   `engagement-posted.sh <id> <permalink>`.
5. **failed** — on post error, orchestrator calls
   `engagement-posted.sh <id> --error '<msg>'`.
6. **declined** — operator replies `decline <id>`;
   orchestrator calls `engagement-decline.sh <id>`.

---

## File layout

```
/var/lib/claude-soma/engagement/
  queue.jsonl          # append-only JSONL; mutated atomically
  PAUSE                # touch to halt drip; rm to resume
  REFILL_NEEDED        # written when queued count < threshold

/var/lib/claude-soma/relay/
  engagement-review.md # regenerated on every drip + approve/decline

/var/log/claude-soma/
  engagement-drip.log  # one ISO-prefixed line per operation
```

The review page is served publicly at:
`https://files.mayankgupta.in/engagement-review.md`

---

## Queue.jsonl schema

Each line is a JSON object:

```jsonc
{
  "id": "<uuid4 or short hash>",
  "platform": "x" | "linkedin",
  "source_permalink": "<url>",
  "source_author": "<handle or name>",
  "source_excerpt": "<short excerpt, max ~300 chars>",
  "why_engage": "<short reason, max ~200 chars>",
  "draft_text": "<the comment to post>",
  "status": "queued",         // queued | pending_review | approved | posted | failed | declined
  "queued_at": 1748900000.0,  // epoch float; assigned at append time
  "released_at": null,        // set when drip pops it
  "approved_at": null,
  "posted_at": null,
  "post_permalink": null,
  "post_error": null,
  "declined_at": null,
  "decline_reason": null
}
```

---

## Operator install (run once after deploy)

```bash
sudo mkdir -p /var/lib/claude-soma/engagement
sudo chown ubuntu:ubuntu /var/lib/claude-soma/engagement
sudo touch /var/lib/claude-soma/engagement/queue.jsonl
sudo chown ubuntu:ubuntu /var/lib/claude-soma/engagement/queue.jsonl
sudo chmod 644 /var/lib/claude-soma/engagement/queue.jsonl
```

---

## systemd timer install

```bash
sudo cp /opt/claude-soma/systemd/claude-soma-engagement-drip.service \
        /etc/systemd/system/
sudo cp /opt/claude-soma/systemd/claude-soma-engagement-drip.timer \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now claude-soma-engagement-drip.timer
```

### Inspect

```bash
systemctl status claude-soma-engagement-drip.timer
systemctl list-timers claude-soma-engagement-drip.timer
journalctl -u claude-soma-engagement-drip.service -n 50
```

---

## Env knobs (set in /etc/claude-soma/secrets.env)

| Variable | Default |
|---|---|
| `HERMES_ENGAGEMENT_QUEUE` | `/var/lib/claude-soma/engagement/queue.jsonl` |
| `HERMES_ENGAGEMENT_PAUSE` | `/var/lib/claude-soma/engagement/PAUSE` |
| `HERMES_ENGAGEMENT_REFILL_FLAG` | `/var/lib/claude-soma/engagement/REFILL_NEEDED` |
| `HERMES_ENGAGEMENT_REFILL_THRESHOLD` | `6` |
| `HERMES_ENGAGEMENT_REVIEW_PAGE` | `/var/lib/claude-soma/relay/engagement-review.md` |
| `HERMES_ENGAGEMENT_LOG` | `/var/log/claude-soma/engagement-drip.log` |
| `HERMES_ENGAGEMENT_REVIEW_URL` | `https://files.mayankgupta.in/engagement-review.md` |
| `TELEGRAM_BOT_TOKEN` | (required for DM) |
| `HERMES_NOTIFY_CHAT_ID` | (required for DM; fallback: `TELEGRAM_CHAT_ID`) |

---

## Helpers

### Approve

```bash
# Approve specific IDs
scripts/engagement-approve.sh <id> [<id> ...]

# Approve all pending_review entries
scripts/engagement-approve.sh --all
```

Prints `Approved N entries: <id list>` to stdout. Regenerates review page.

### Mark posted

```bash
# Successful post
scripts/engagement-posted.sh <id> https://x.com/handle/status/12345

# Failed post
scripts/engagement-posted.sh <id> --error "rate limited"
```

### Decline

```bash
scripts/engagement-decline.sh <id>
scripts/engagement-decline.sh <id> --reason "off-brand"
```

---

## PAUSE switch

```bash
# Halt drip (next timer fire will no-op)
touch /var/lib/claude-soma/engagement/PAUSE

# Resume
rm /var/lib/claude-soma/engagement/PAUSE
```

The drip script checks for the PAUSE file at start. Queue state is not mutated.

---

## Queue refill

When remaining `queued` entries after a drip fall below the threshold (default 6),
`REFILL_NEEDED` is written:

```json
{"ts": 1748900000.0, "remaining_queued": 4, "breakdown": {"x": 2, "linkedin": 2}}
```

The orchestrator monitors this file and dispatches `social-manager` to refill the
queue. When the count rises above threshold again, the file is removed.

---

## Token-cost note

- **Hourly drip path**: zero LLM tokens. Pure Python file I/O + Telegram HTTP call.
- **Queue refill**: batched LLM cost, run only when `REFILL_NEEDED` is present, via
  the `social-manager` agent (separate brief).

---

## Telegram DM template

When entries are popped, the operator receives:

```
Engagement drafts ready for review:

X: @handle (id: <id>)
LinkedIn: Name (id: <id>)

Review: https://files.mayankgupta.in/engagement-review.md

Commands: approve <id> | approve all | decline <id>
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| No DM, no queue mutation | PAUSE file present | `rm /var/lib/claude-soma/engagement/PAUSE` |
| No DM, "no queued drafts" in log | Queue empty or all non-queued | Run `social-manager` to refill |
| Queue mutated but no DM | Telegram credentials missing/wrong | Check `TELEGRAM_BOT_TOKEN` + `HERMES_NOTIFY_CHAT_ID` in secrets.env |
| Review page not updating | `/var/lib/claude-soma/relay/` permissions | `sudo chown -R ubuntu:ubuntu /var/lib/claude-soma/relay/` |
| Timer not firing | Timer not enabled | `sudo systemctl enable --now claude-soma-engagement-drip.timer` |
