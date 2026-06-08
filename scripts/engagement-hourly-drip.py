#!/usr/bin/env python3
"""Mechanical hourly drip: pops 1 X + 1 LinkedIn from engagement queue,
marks pending_review, regenerates review page, DMs operator.
Zero LLM tokens on this path.

Modes (via argv):
  (no flag)          -- run hourly drip (legacy entry point; same as --source=any)
  --source=fresh     -- pop only drafts with freshly_drafted_at >= start_ts
                        (or the most-recent freshly_drafted_at if start_ts unset);
                        used by engagement-hourly-dispatch.sh after the
                        browse+draft subagent succeeds. DM banner: FRESH.
  --source=any       -- pop any queued draft, oldest queued_at first
                        (legacy mechanical behavior). DM banner: POOLED.
  --fallback         -- alias for --source=any. DM banner adds a
                        POOLED FALLBACK marker naming the upstream reason
                        passed via --fallback-reason "<text>".
  --fallback-reason "<text>"
                       -- annotates the DM banner when running in --fallback
                        mode, so the operator immediately sees why fresh
                        failed (playwright expired, timeout, zero drafts, etc.).
  --start-ts <epoch> -- when used with --source=fresh, only drafts whose
                        freshly_drafted_at >= start-ts are eligible.
  --regen-only       -- regenerate review page only; no queue mutation
  --approve <id...>  -- mark ids approved; regenerate review page
  --approve-all      -- mark all pending_review approved; regenerate
  --posted <id> <permalink>       -- mark id posted with permalink
  --posted-error <id> <msg>       -- mark id failed with error message
  --decline <id> [--reason <txt>] -- mark id declined
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# FI-QUEUE-DEDUP-LOCK helpers
# ---------------------------------------------------------------------------

_STATUS_PRECEDENCE: dict[str, int] = {
    "posted": 6,
    "failed": 5,
    "declined": 4,
    "approved": 3,
    "pending_review": 2,
    "queued": 1,
}


def _status_rank(entry: dict) -> int:
    """Return the precedence rank for an entry's status. Unknown → 0."""
    return _STATUS_PRECEDENCE.get(str(entry.get("status") or ""), 0)


def _entry_latest_ts(entry: dict) -> float:
    """Return the latest timestamp among all timestamped fields in the entry.
    Used as a tiebreaker when two rows share the same status rank."""
    candidates = [
        entry.get("posted_at"),
        entry.get("declined_at"),
        entry.get("approved_at"),
        entry.get("released_at"),
        entry.get("queued_at"),
    ]
    valid = [float(v) for v in candidates if v is not None and v != ""]
    return max(valid) if valid else 0.0


def _dedup_entries(entries: list[dict]) -> list[dict]:
    """Collapse duplicate id rows to one canonical row per id.

    Selection rule (in priority order):
      1. Highest _STATUS_PRECEDENCE rank wins.
      2. Tie in rank → latest timestamp (max of all ts fields) wins.

    Rows with no id or empty id pass through verbatim and are appended
    after the de-duplicated id rows (legacy/malformed rows; never drop them).

    Output order: first-seen id insertion order, then no-id rows.
    """
    # best[id] = the current winner entry for that id
    best: dict[str, dict] = {}
    no_id_rows: list[dict] = []

    for entry in entries:
        eid = entry.get("id")
        if not eid:
            no_id_rows.append(entry)
            continue
        eid = str(eid)
        if eid not in best:
            best[eid] = entry
        else:
            existing = best[eid]
            existing_rank = _status_rank(existing)
            new_rank = _status_rank(entry)
            if new_rank > existing_rank:
                best[eid] = entry
            elif new_rank == existing_rank:
                if _entry_latest_ts(entry) > _entry_latest_ts(existing):
                    best[eid] = entry

    return list(best.values()) + no_id_rows


@contextlib.contextmanager
def queue_locked(path: str | Path):
    """Exclusive write-lock on <path>.lock (a sibling file, NOT queue.jsonl).

    Using a separate lockfile is intentional: write_queue_atomic uses
    os.replace(), which changes the inode at `path` on every write. A lock
    on the queue file itself would become a lock on an unlinked (orphaned)
    inode the moment a writer replaced the file, defeating the mutual
    exclusion guarantee. The sibling .lock file is never replaced, so its
    inode is stable and fcntl.LOCK_EX semantics hold correctly across all
    concurrent writers.
    """
    lock_path = Path(path).parent / (Path(path).name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _read_secrets_var(name: str, secrets: str = "/etc/claude-soma/secrets.env") -> str:
    """Mirror of scripts/soma-relay's _read_secrets_var. Returns the LAST
    occurrence of `^name=value` in the secrets file (operators sometimes
    append a fix below a stale value), stripping outer double quotes.
    Empty string if the file is unreadable or the key is absent — the
    canonical place for these is /etc/claude-soma/secrets.env (0600,
    ubuntu-owned).
    """
    try:
        with open(secrets, encoding="utf-8") as fh:
            last = ""
            for line in fh:
                if not line.startswith(name + "="):
                    continue
                v = line[len(name) + 1:].rstrip("\n").rstrip("\r")
                if v.startswith('"') and v.endswith('"'):
                    v = v[1:-1]
                last = v
            return last
    except OSError:
        return ""


def _resolve_review_url() -> str:
    """Resolve the engagement-review URL the operator clicks from the DM.
    Precedence (mirrors scripts/soma-relay's _resolve_relay_domain so the
    drip surfaces the same URL the soma-publish tooling does):

      1. HERMES_ENGAGEMENT_REVIEW_URL env — explicit override (full URL).
      2. SOMA_RELAY_DOMAIN= in secrets.env → https://<domain>/engagement-review.md.
      3. FILES_DOMAIN= in secrets.env → same shape.
      4. SOMA_DOMAIN= in secrets.env → https://files.<domain>/engagement-review.md.
      5. Empty (legacy behavior — DM shows `Review:` with no URL).

    The relay file itself is written to /var/lib/claude-soma/relay/engagement-review.md
    by regenerate_review_page() and served by the markserv/Caddy stack at the
    resolved URL. No soma-publish call needed — the relay root IS the served
    directory.
    """
    explicit = os.environ.get("HERMES_ENGAGEMENT_REVIEW_URL", "").strip()
    if explicit:
        return explicit
    page_basename = "engagement-review.md"
    relay = _read_secrets_var("SOMA_RELAY_DOMAIN")
    if relay:
        return f"https://{relay}/{page_basename}"
    files_domain = _read_secrets_var("FILES_DOMAIN")
    if files_domain:
        return f"https://{files_domain}/{page_basename}"
    soma_domain = _read_secrets_var("SOMA_DOMAIN")
    if soma_domain:
        base = soma_domain[5:] if soma_domain.startswith("soma.") else soma_domain
        return f"https://files.{base}/{page_basename}"
    return ""


def _cfg() -> dict[str, Any]:
    return {
        "queue_path": os.environ.get(
            "HERMES_ENGAGEMENT_QUEUE",
            "/var/lib/claude-soma/engagement/queue.jsonl",
        ),
        "pause_path": os.environ.get(
            "HERMES_ENGAGEMENT_PAUSE",
            "/var/lib/claude-soma/engagement/PAUSE",
        ),
        "refill_flag": os.environ.get(
            "HERMES_ENGAGEMENT_REFILL_FLAG",
            "/var/lib/claude-soma/engagement/REFILL_NEEDED",
        ),
        "refill_threshold": int(
            os.environ.get("HERMES_ENGAGEMENT_REFILL_THRESHOLD", "6")
        ),
        "review_page": os.environ.get(
            "HERMES_ENGAGEMENT_REVIEW_PAGE",
            "/var/lib/claude-soma/relay/engagement-review.md",
        ),
        "log_path": os.environ.get(
            "HERMES_ENGAGEMENT_LOG",
            "/var/log/claude-soma/engagement-drip.log",
        ),
        "tg_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        "tg_chat_id": (
            os.environ.get("HERMES_NOTIFY_CHAT_ID")
            or os.environ.get("TELEGRAM_CHAT_ID", "")
        ),
        # FI-DRIP-REVIEW-LINK (2026-06-05): every engagement notification
        # MUST carry the review URL so the operator can open the full
        # pending-review doc on the relay instead of relying on the DM's
        # one-line summary. Derived from secrets.env when not explicitly
        # set so the URL is never silently empty.
        "review_url": _resolve_review_url(),
        # FI-TARGET-DEDUP-LEDGER (Bundle 2): persistent record of posted
        # targets so the dedup pass can block re-drafts on already-commented
        # posts across queue regenerations.
        "posted_ledger": os.environ.get(
            "HERMES_ENGAGEMENT_POSTED_LEDGER",
            "/var/lib/claude-soma/engagement/posted-targets.jsonl",
        ),
    }


def _log(log_path: str, msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts} {msg}\n"
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass
    print(line, end="", file=sys.stderr)


def read_queue(path: str | Path) -> list[dict]:
    """Read and return all entries from the queue file.

    Applies _dedup_entries so callers always see at most one row per id,
    with terminal-status precedence applied. Readers do not need to hold
    queue_locked() — os.replace() is atomic (POSIX), so a reader sees
    either the complete old file or the complete new file, never a partial
    write. Dedup-on-read makes readers robust to any historic duplicate rows
    that existed before FI-QUEUE-DEDUP-LOCK was deployed.
    """
    entries: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entries.append(json.loads(raw))
                except json.JSONDecodeError as exc:
                    print(
                        f"WARNING: queue line {lineno} invalid JSON: {exc}",
                        file=sys.stderr,
                    )
    except FileNotFoundError:
        pass
    return _dedup_entries(entries)


def write_queue_atomic(entries: list[dict], path: str | Path) -> None:
    path = Path(path)
    tmp = Path(str(path) + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
    os.replace(tmp, path)


def _fmt_ts(epoch: float | None) -> str:
    if epoch is None:
        return "—"
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _platform_label(platform: str) -> str:
    return {"x": "X", "linkedin": "LinkedIn"}.get(platform.lower(), platform)


# FI-ENGAGEMENT-SCHEMA-V1 (2026-06-06). docs/engagement-schema.md is the
# single source of truth; this constant pins the renderer's contract.
SCHEMA_VERSION = "engagement.v1"

# Frozen v1 topic tag set. Anything outside this set renders as
# "(uncategorized)" and is logged so producer drift is visible.
TOPIC_TAGS = frozenset({
    "claude-code",
    "agents",
    "mcp",
    "infra",
    "dev-tooling",
    "ai-research",
    "voice",
    "other",
})


def _excerpt(entry: dict) -> str:
    """Read the source excerpt, accepting the v0 legacy field name for one
    schema version. v1 producers MUST emit `source_excerpt`; the legacy
    `source_post_excerpt` fallback exists only so historical queue rows
    still render correctly. Drop in v2."""
    val = entry.get("source_excerpt")
    if val:
        return str(val)
    legacy = entry.get("source_post_excerpt")
    if legacy:
        return str(legacy)
    return ""


def _topic(entry: dict) -> str:
    val = (entry.get("topic") or "").strip()
    if val and val in TOPIC_TAGS:
        return val
    return ""  # rendered as "(uncategorized)" by the renderer


def _sort_key(entry: dict) -> tuple:
    """Deterministic ordering: oldest queued_at first; ties broken by id
    lexicographic. Same data → same order, every regen."""
    return (float(entry.get("queued_at") or 0), str(entry.get("id") or ""))


def _render_entry_block(e: dict) -> list[str]:
    """One entry block, identical layout in both Pending and Queued
    sections. Order: header → Topic → optional Note → Source → Why engage
    → Source excerpt → Draft → action hint → divider. Locked by v1
    schema; missing fields render with neutral placeholders so v0 rows
    still display but visually flag themselves."""
    eid = str(e.get("id") or "unknown")
    plat = _platform_label(str(e.get("platform") or ""))
    author = str(e.get("source_author") or "")
    topic = _topic(e) or "(uncategorized)"
    note = (e.get("relevance_note") or "").strip()
    source = str(e.get("source_permalink") or "")
    why = (e.get("why_engage") or "").strip() or "(no rationale)"
    excerpt = _excerpt(e) or "(no excerpt)"
    draft = str(e.get("draft_text") or "")

    out: list[str] = []
    out.append(f"### {eid} · {plat} · {author}")
    out.append("")
    out.append(f"- **Topic:** {topic}")
    if note:
        out.append(f"- **Note:** {note}")
    out.append(f"- **Source:** {source}")
    out.append(f"- **Why engage:** {why}")
    out.append("- **Source excerpt:**")
    out.append(f"  > {excerpt}")
    out.append("- **Draft:**")
    out.append(f"  > {draft}")
    out.append("")
    out.append(f"`approve {eid}` | `decline {eid}`")
    out.append("")
    out.append("---")
    out.append("")
    return out


def regenerate_review_page(
    entries: list[dict], out_path: str | Path, review_url: str
) -> None:
    """FI-ENGAGEMENT-SCHEMA-V1 (2026-06-06) renderer.

    The doc is DETERMINISTIC: same input data → byte-identical output,
    except for the single "Last regenerated" timestamp pinned to the
    FOOTER so the top of the doc never churns. The schema is frozen at
    docs/engagement-schema.md.

    Structure (every regen, every state):

      # Engagement Review
      _Schema version: engagement.v1_
      **Actionable totals** ...
      Reply via Telegram ...
      ---
      ## Pending Review (N)
      <entry blocks, or "_None._">
      ## Queued (next up — M)
      <entry blocks, or "_None._">
      ---
      _Last regenerated: <iso>_

    FI-REVIEW-DOC-ACTIONABLE-ONLY: only `queued` / `pending_review`
    entries render. Approved / posted / failed / declined are excluded.
    """
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    pending = sorted(
        (e for e in entries if e.get("status") == "pending_review"),
        key=_sort_key,
    )
    queued = sorted(
        (e for e in entries if e.get("status") == "queued"),
        key=_sort_key,
    )

    def _counts(rows: list[dict]) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in rows:
            p = str(e.get("platform") or "?")
            out[p] = out.get(p, 0) + 1
        return out

    def _fmt_counts(d: dict[str, int]) -> str:
        if not d:
            return "0"
        order = ["x", "linkedin"]
        parts = [
            f"{_platform_label(p)}: {d.get(p, 0)}"
            for p in order
            if p in d
        ]
        for p in sorted(d):
            if p not in order:
                parts.append(f"{_platform_label(p)}: {d[p]}")
        return ", ".join(parts) or "0"

    lines: list[str] = []
    lines.append("# Engagement Review")
    lines.append("")
    lines.append(f"_Schema version: {SCHEMA_VERSION}_")
    lines.append("")
    lines.append(
        f"**Actionable totals** — Pending review: {len(pending)} "
        f"({_fmt_counts(_counts(pending))})  ·  Queued: {len(queued)} "
        f"({_fmt_counts(_counts(queued))})"
    )
    lines.append("")
    lines.append("Reply via Telegram to act on drafts:")
    lines.append("- `approve <id>` — approve a single draft")
    lines.append("- `approve all` — approve every pending_review draft")
    lines.append("- `decline <id>` — decline a draft (won't post)")
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append(f"## Pending Review ({len(pending)})")
    lines.append("")
    if not pending:
        lines.append("_None._")
        lines.append("")
    else:
        for e in pending:
            lines.extend(_render_entry_block(e))

    lines.append(f"## Queued (next up — {len(queued)})")
    lines.append("")
    if not queued:
        lines.append("_None._")
        lines.append("")
    else:
        for e in queued:
            lines.extend(_render_entry_block(e))

    # Footer: the ONLY part that churns between regens (intentionally
    # pinned to the bottom so the top of the doc is byte-stable for
    # identical queue contents).
    lines.append("---")
    lines.append("")
    lines.append(f"_Last regenerated: {now_str}_")
    lines.append("")

    content = "\n".join(lines)
    out_path = Path(out_path)
    tmp = Path(str(out_path) + ".tmp")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.replace(tmp, out_path)


def render_review_body(entries: list[dict]) -> str:
    """Render the v1 doc body WITHOUT the regen-timestamp footer.

    This is the byte-stable surface. Same `entries` (status/order/fields
    unchanged) → same string, always. Tests rely on this to verify
    determinism without needing a clock freeze."""
    pending = sorted(
        (e for e in entries if e.get("status") == "pending_review"),
        key=_sort_key,
    )
    queued = sorted(
        (e for e in entries if e.get("status") == "queued"),
        key=_sort_key,
    )

    def _counts(rows: list[dict]) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in rows:
            p = str(e.get("platform") or "?")
            out[p] = out.get(p, 0) + 1
        return out

    def _fmt_counts(d: dict[str, int]) -> str:
        if not d:
            return "0"
        order = ["x", "linkedin"]
        parts = [
            f"{_platform_label(p)}: {d.get(p, 0)}"
            for p in order
            if p in d
        ]
        for p in sorted(d):
            if p not in order:
                parts.append(f"{_platform_label(p)}: {d[p]}")
        return ", ".join(parts) or "0"

    lines: list[str] = [
        "# Engagement Review",
        "",
        f"_Schema version: {SCHEMA_VERSION}_",
        "",
        (
            f"**Actionable totals** — Pending review: {len(pending)} "
            f"({_fmt_counts(_counts(pending))})  ·  Queued: {len(queued)} "
            f"({_fmt_counts(_counts(queued))})"
        ),
        "",
        "Reply via Telegram to act on drafts:",
        "- `approve <id>` — approve a single draft",
        "- `approve all` — approve every pending_review draft",
        "- `decline <id>` — decline a draft (won't post)",
        "",
        "---",
        "",
        f"## Pending Review ({len(pending)})",
        "",
    ]
    if not pending:
        lines.extend(["_None._", ""])
    else:
        for e in pending:
            lines.extend(_render_entry_block(e))
    lines.append(f"## Queued (next up — {len(queued)})")
    lines.append("")
    if not queued:
        lines.extend(["_None._", ""])
    else:
        for e in queued:
            lines.extend(_render_entry_block(e))
    return "\n".join(lines)


def _emit_empty_dm(cfg: dict, *, banner: str, fallback_reason: str) -> None:
    """Send a "no drafts this hour" DM so the operator is never silent.

    The user explicitly required (sign-off 2026-06-05) that every hourly
    dispatch produces exactly one DM, even when zero drafts were popped.
    A silent hour is what the BUG-DRIP-SILENT-FAILURE entry tracked and
    this branch is the explicit fix for the "fresh failed AND pool is
    empty" combination.
    """
    log = cfg["log_path"]
    reason_suffix = f": {fallback_reason}" if fallback_reason else ""
    # FI-DRIP-REVIEW-LINK: include the relay review URL in the empty-hour DM
    # too, so the operator can still open the full pending-review doc (it may
    # still hold drafts from prior hours that haven't been approved yet — the
    # "no NEW drafts" message shouldn't imply nothing is reviewable).
    review_line = (
        f"\nReview queue: {cfg['review_url']}\n"
        if cfg.get("review_url")
        else ""
    )
    body = (
        f"[{banner}{reason_suffix}] No engagement drafts this hour.\n"
        "\n"
        "The fresh browse+draft pass did not produce any drafts and the "
        "pooled queue is also empty.\n"
        f"{review_line}"
        "\n"
        "Investigate:\n"
        "  - playwright X / LinkedIn sessions: are state files still warm?\n"
        "  - run scripts/pw-refresh on the VNC desktop if needed\n"
        "  - check /var/log/claude-soma/engagement-dispatch.jsonl for the "
        "subagent exit code and stderr\n"
        "  - manual refill: run social-manager 'draft N engagement comments'\n"
    )
    try:
        if send_telegram_dm(cfg["tg_token"], cfg["tg_chat_id"], body):
            _log(log, "drip: empty-hour DM sent (NEEDS_INTERVENTION)")
        else:
            _log(
                log,
                "WARNING: empty-hour DM skipped — TELEGRAM_BOT_TOKEN or "
                "HERMES_NOTIFY_CHAT_ID missing in env",
            )
    except Exception as exc:
        _log(log, f"WARNING: empty-hour DM failed: {exc}")


def send_telegram_dm(token: str, chat_id: str, text: str) -> bool:
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(url, data=data)
    urllib.request.urlopen(req, timeout=5)
    return True


def _hours_since_last_li_queued_append(
    entries: list[dict], now: float | None = None
) -> float | None:
    """Return hours since the most recent LinkedIn entry was appended to the
    queue (status doesn't matter — we want the freshest queued_at timestamp).
    Returns None when no LinkedIn entries exist at all.

    Used by the hybrid drip's stalled-warning logic: if social-manager hasn't
    refilled the LI pool in N hours, the DM surfaces a "LI refill stalled —
    check social-manager" line in-band so the operator doesn't have to dig
    through logs to discover the silent failure.
    """
    now = time.time() if now is None else now
    li_queued_ts = [
        float(e.get("queued_at") or 0)
        for e in entries
        if e.get("platform") == "linkedin"
        and (e.get("queued_at") or 0) > 0
    ]
    if not li_queued_ts:
        return None
    return (now - max(li_queued_ts)) / 3600.0


def drip_hybrid(
    cfg: dict,
    *,
    start_ts: float,
    x_source: str = "fresh",
    li_source: str = "any",
    li_stalled_hours: float = 3.0,
) -> int:
    """FI-ENGAGEMENT-HYBRID (2026-06-05): pop X under one policy and LinkedIn
    under another, in ONE pass, with ONE Telegram DM.

    The operator signed off on:
      - X uses --source=fresh with start_ts gating (the proven fresh-ephemeral
        subagent path; works great).
      - LinkedIn uses --source=any (pool drafts from social-manager's warm
        playwright-linkedin MCP session; the empirically reliable LI path).

    The single DM aggregates both popped drafts + a "LI refill stalled"
    warning line when LinkedIn hasn't been refilled in `li_stalled_hours`
    hours (default 3). The warning rides ON THE EXISTING DM, never a
    separate one, so the "exactly one DM per hour" contract holds.

    Empty-hour behavior: if NEITHER X nor LI yields a draft, calls
    _emit_empty_dm with banner=POOLED FALLBACK so the silent-hour contract
    (BUG-DRIP-SILENT-FAILURE) holds.
    """
    log = cfg["log_path"]

    if Path(cfg["pause_path"]).exists():
        _log(log, "drip[hybrid]: paused (PAUSE file present); exiting")
        return 0

    entries = read_queue(cfg["queue_path"])
    _log(
        log,
        f"drip[hybrid]: read {len(entries)} entries "
        f"(x={x_source} start_ts={start_ts}, linkedin={li_source})",
    )

    queued = [e for e in entries if e.get("status") == "queued"]
    by_platform: dict[str, list[dict]] = {"x": [], "linkedin": []}
    for e in queued:
        plat = e.get("platform", "")
        if plat in by_platform:
            by_platform[plat].append(e)

    # Per-platform eligibility filter + sort.
    def _eligible(platform: str, source: str) -> list[dict]:
        pool = list(by_platform.get(platform, []))
        if source == "fresh":
            pool = [
                e for e in pool
                if float(e.get("freshly_drafted_at") or 0) >= start_ts
            ]
            pool.sort(
                key=lambda e: e.get("freshly_drafted_at") or 0,
                reverse=True,
            )
        else:
            pool.sort(key=lambda e: e.get("queued_at") or 0)
        return pool

    x_pool = _eligible("x", x_source)
    li_pool = _eligible("linkedin", li_source)
    _log(
        log,
        f"drip[hybrid]: eligible x={len(x_pool)} ({x_source}), "
        f"linkedin={len(li_pool)} ({li_source})",
    )

    to_pop: list[dict] = []
    if x_pool:
        to_pop.append(x_pool[0])
    if li_pool:
        to_pop.append(li_pool[0])

    # Compute stalled-LI warning state BEFORE mutating the queue.
    li_age_hours = _hours_since_last_li_queued_append(entries)
    li_stalled_line = ""
    if li_age_hours is None:
        li_stalled_line = (
            "\n[WARNING] No LinkedIn drafts have ever been queued — "
            "social-manager may not be picking up the refill request. "
            "Check the social-manager lead's health.\n"
        )
    elif li_age_hours >= li_stalled_hours:
        li_stalled_line = (
            f"\n[WARNING] No LinkedIn drafts have been queued in "
            f"{li_age_hours:.1f}h (threshold {li_stalled_hours:.0f}h). "
            "social-manager's LI refill loop may be stalled — check the "
            "social-manager lead's health.\n"
        )

    if not to_pop:
        _log(log, "drip[hybrid]: no drafts eligible from either platform")
        # Empty-hour DM — extend with the stalled warning if relevant.
        _emit_empty_dm(
            cfg,
            banner="POOLED FALLBACK",
            fallback_reason=(
                "hybrid: 0 X fresh + 0 LI pool"
                + (
                    f"; LI stalled {li_age_hours:.1f}h"
                    if li_age_hours is not None and li_age_hours >= li_stalled_hours
                    else ""
                )
            ),
        )
        return 0

    with queue_locked(cfg["queue_path"]):
        entries = read_queue(cfg["queue_path"])
        now = time.time()
        pop_ids = {e["id"] for e in to_pop}
        to_pop = []
        for e in entries:
            if e.get("id") in pop_ids and e.get("status") == "queued":
                e["status"] = "pending_review"
                e["released_at"] = now
                to_pop.append(e)
                _log(
                    log,
                    f"drip[hybrid]: popped id={e['id']} platform={e.get('platform')}",
                )
        write_queue_atomic(entries, cfg["queue_path"])
    _log(log, "drip[hybrid]: queue written atomically")

    try:
        regenerate_review_page(entries, cfg["review_page"], cfg["review_url"])
        _log(
            log,
            f"drip[hybrid]: review page written to {cfg['review_page']}",
        )
    except Exception as exc:
        _log(log, f"WARNING: review page write failed: {exc}")

    # Build the DM. Single message per hour (the contract).
    dm_lines = [
        "[FRESH-X + POOL-LI] Engagement drafts ready for review:",
        "",
    ]
    for entry in to_pop:
        plat = _platform_label(entry.get("platform", ""))
        author = entry.get("source_author", "")
        eid = entry.get("id", "?")
        dm_lines.append(f"{plat}: {author} (id: {eid})")
    if li_stalled_line:
        dm_lines.append(li_stalled_line.rstrip("\n"))
    dm_lines.append("")
    if cfg.get("review_url"):
        dm_lines.append(f"Review: {cfg['review_url']}")
        dm_lines.append("")
    dm_lines.append("Commands: approve <id> | approve all | decline <id>")
    dm_text = "\n".join(dm_lines)

    try:
        if send_telegram_dm(cfg["tg_token"], cfg["tg_chat_id"], dm_text):
            _log(log, "drip[hybrid]: Telegram DM sent")
        else:
            missing = []
            if not cfg["tg_token"]:
                missing.append("TELEGRAM_BOT_TOKEN")
            if not cfg["tg_chat_id"]:
                missing.append("HERMES_NOTIFY_CHAT_ID")
            _log(
                log,
                "WARNING: Telegram DM skipped — missing in environment: "
                + ", ".join(missing),
            )
    except Exception as exc:
        _log(log, f"WARNING: Telegram DM failed: {exc}")

    _log(log, "drip[hybrid]: done")
    return 0


def drip(
    cfg: dict,
    *,
    source: str = "any",
    start_ts: float | None = None,
    banner: str = "POOLED",
    on_empty_emit_dm: bool = False,
    fallback_reason: str = "",
) -> int:
    """Pop one queued draft per platform into pending_review and DM the operator.

    Parameters
    ----------
    source : "any" or "fresh"
        "any"  → all queued entries are eligible (legacy mechanical behavior).
        "fresh" → only entries with freshly_drafted_at >= start_ts (if set)
                   or a non-empty freshly_drafted_at field (if start_ts None).
        Returning to "any" is what --fallback uses after a failed fresh pass.
    start_ts : float | None
        Lower bound on freshly_drafted_at when source="fresh". Set by
        engagement-hourly-dispatch.sh to the dispatch's start time so a
        stale fresh draft from a prior hour can't be popped.
    banner : str
        Goes into the Telegram DM's first line so the operator sees the
        provenance at a glance ("FRESH" / "POOLED" / "POOLED FALLBACK").
    on_empty_emit_dm : bool
        When True, still send a Telegram DM even if no drafts were popped —
        used by the FALLBACK path so a silent hour can't recur. The DM
        explicitly says "no drafts this hour" and names fallback_reason
        if set, so the operator can intervene.
    fallback_reason : str
        Short human-readable reason the fresh path didn't produce drafts
        (only used in banner / empty-DM text).
    """
    log = cfg["log_path"]

    if Path(cfg["pause_path"]).exists():
        _log(log, "drip: paused (PAUSE file present); exiting")
        return 0

    entries = read_queue(cfg["queue_path"])
    _log(
        log,
        f"drip: read {len(entries)} entries from queue "
        f"(source={source}, start_ts={start_ts}, banner={banner})",
    )

    queued = [e for e in entries if e.get("status") == "queued"]
    if source == "fresh":
        if start_ts is not None:
            queued = [
                e for e in queued
                if float(e.get("freshly_drafted_at") or 0) >= start_ts
            ]
        else:
            queued = [e for e in queued if e.get("freshly_drafted_at")]
        _log(log, f"drip: filtered to {len(queued)} fresh entries")

    by_platform: dict[str, list[dict]] = {}
    for e in queued:
        plat = e.get("platform", "")
        by_platform.setdefault(plat, []).append(e)

    if source == "fresh":
        # Newest-first so we surface the most recent humanized draft.
        for plat in by_platform:
            by_platform[plat].sort(
                key=lambda e: e.get("freshly_drafted_at") or 0,
                reverse=True,
            )
    else:
        for plat in by_platform:
            by_platform[plat].sort(key=lambda e: e.get("queued_at") or 0)

    to_pop: list[dict] = []
    for plat in ("x", "linkedin"):
        if by_platform.get(plat):
            to_pop.append(by_platform[plat][0])

    if not to_pop:
        _log(log, "drip: no queued drafts matched the filter")
        if on_empty_emit_dm:
            _emit_empty_dm(cfg, banner=banner, fallback_reason=fallback_reason)
        return 0

    with queue_locked(cfg["queue_path"]):
        entries = read_queue(cfg["queue_path"])
        now = time.time()
        pop_ids = {e["id"] for e in to_pop}
        to_pop = []
        for e in entries:
            if e.get("id") in pop_ids and e.get("status") == "queued":
                e["status"] = "pending_review"
                e["released_at"] = now
                to_pop.append(e)
                _log(log, f"drip: popped id={e['id']} platform={e.get('platform')}")
        write_queue_atomic(entries, cfg["queue_path"])
    _log(log, "drip: queue written atomically")

    try:
        regenerate_review_page(entries, cfg["review_page"], cfg["review_url"])
        _log(log, f"drip: review page written to {cfg['review_page']}")
    except Exception as exc:
        _log(log, f"WARNING: review page write failed: {exc}")

    remaining_queued = [e for e in entries if e.get("status") == "queued"]
    remaining_count = len(remaining_queued)
    x_count = sum(1 for e in remaining_queued if e.get("platform") == "x")
    li_count = sum(1 for e in remaining_queued if e.get("platform") == "linkedin")
    _log(
        log,
        f"drip: remaining queued={remaining_count} (x={x_count}, linkedin={li_count})",
    )

    refill_flag = Path(cfg["refill_flag"])
    if remaining_count < cfg["refill_threshold"]:
        breakdown = {"x": x_count, "linkedin": li_count}
        flag_content = (
            json.dumps({"ts": now, "remaining_queued": remaining_count, "breakdown": breakdown})
            + "\n"
        )
        try:
            refill_flag.parent.mkdir(parents=True, exist_ok=True)
            refill_flag.write_text(flag_content, encoding="utf-8")
            _log(log, f"drip: REFILL_NEEDED written (remaining={remaining_count})")
        except OSError as exc:
            _log(log, f"WARNING: could not write REFILL_NEEDED: {exc}")
    else:
        if refill_flag.exists():
            try:
                refill_flag.unlink()
                _log(log, "drip: REFILL_NEEDED removed (queue above threshold)")
            except OSError as exc:
                _log(log, f"WARNING: could not remove REFILL_NEEDED: {exc}")

    banner_text = f"[{banner}]"
    if fallback_reason and banner.startswith("POOLED FALLBACK"):
        banner_text = f"[{banner}: {fallback_reason}]"
    dm_lines = [f"{banner_text} Engagement drafts ready for review:", ""]
    for entry in to_pop:
        plat = _platform_label(entry.get("platform", ""))
        author = entry.get("source_author", "")
        eid = entry.get("id", "?")
        dm_lines.append(f"{plat}: {author} (id: {eid})")
    dm_lines.append("")
    dm_lines.append(f"Review: {cfg['review_url']}")
    dm_lines.append("")
    dm_lines.append("Commands: approve <id> | approve all | decline <id>")
    dm_text = "\n".join(dm_lines)

    try:
        if send_telegram_dm(cfg["tg_token"], cfg["tg_chat_id"], dm_text):
            _log(log, "drip: Telegram DM sent")
        else:
            missing = []
            if not cfg["tg_token"]:
                missing.append("TELEGRAM_BOT_TOKEN")
            if not cfg["tg_chat_id"]:
                missing.append("HERMES_NOTIFY_CHAT_ID")
            _log(
                log,
                "WARNING: Telegram DM skipped — missing in environment: "
                + ", ".join(missing)
                + " (set in /etc/claude-soma/secrets.env and restart engagement-drip)",
            )
    except Exception as exc:
        _log(log, f"WARNING: Telegram DM failed: {exc}")

    _log(log, "drip: done")
    return 0


def regen_only(cfg: dict) -> int:
    entries = read_queue(cfg["queue_path"])
    try:
        regenerate_review_page(entries, cfg["review_page"], cfg["review_url"])
    except Exception as exc:
        print(f"WARNING: review page write failed: {exc}", file=sys.stderr)
        return 1
    return 0


def approve_entries(
    cfg: dict,
    ids: list[str] | None = None,
    all_pending: bool = False,
) -> int:
    id_set = set(ids) if ids else set()
    now = time.time()
    approved_ids: list[str] = []
    with queue_locked(cfg["queue_path"]):
        entries = read_queue(cfg["queue_path"])
        for e in entries:
            if all_pending:
                if e.get("status") == "pending_review":
                    e["status"] = "approved"
                    e["approved_at"] = now
                    approved_ids.append(e["id"])
            elif e.get("id") in id_set and e.get("status") == "pending_review":
                e["status"] = "approved"
                e["approved_at"] = now
                approved_ids.append(e["id"])
        write_queue_atomic(entries, cfg["queue_path"])
    n = len(approved_ids)
    word = "entry" if n == 1 else "entries"
    summary = (
        f"Approved {n} {word}: {' '.join(approved_ids)}"
        if approved_ids
        else f"Approved {n} {word}: (none matched)"
    )
    print(summary)
    _log(cfg["log_path"], f"approve: {summary}")
    try:
        regenerate_review_page(entries, cfg["review_page"], cfg["review_url"])
    except Exception as exc:
        print(f"WARNING: review page write failed: {exc}", file=sys.stderr)
    return 0


def mark_posted(cfg: dict, entry_id: str, permalink: str) -> int:
    now = time.time()
    with queue_locked(cfg["queue_path"]):
        entries = read_queue(cfg["queue_path"])
        posted_entry: dict | None = None
        for e in entries:
            if e.get("id") == entry_id:
                e["status"] = "posted"
                e["post_permalink"] = permalink
                e["posted_at"] = now
                posted_entry = e
                break
        if posted_entry is not None:
            # FI-TARGET-DEDUP-LEDGER #94: record in ledger (always, even if
            # already present — option (a): a force-post is a real engagement
            # that should block future re-drafts).
            append_posted_ledger(cfg["posted_ledger"], posted_entry, permalink, now)
            # Auto-decline all other entries that target the same post.
            declined_k = 0
            for e in entries:
                if e.get("id") == entry_id:
                    continue
                if e.get("status") not in {"queued", "pending_review", "approved"}:
                    continue
                if targets_match(e, posted_entry):
                    e["status"] = "declined"
                    e["declined_at"] = now
                    e["decline_reason"] = f"duplicate_target:{entry_id}"
                    declined_k += 1
        write_queue_atomic(entries, cfg["queue_path"])
    sibling_info = f" auto-declined {declined_k} sibling(s) sharing target" if (
        posted_entry is not None
    ) else ""
    print(f"Marked posted: {entry_id} -> {permalink}{sibling_info}")
    _log(
        cfg["log_path"],
        f"posted: id={entry_id} permalink={permalink}{sibling_info}",
    )
    try:
        regenerate_review_page(entries, cfg["review_page"], cfg["review_url"])
    except Exception as exc:
        print(f"WARNING: review page write failed: {exc}", file=sys.stderr)
    return 0


def mark_posted_error(cfg: dict, entry_id: str, error_msg: str) -> int:
    now = time.time()
    with queue_locked(cfg["queue_path"]):
        entries = read_queue(cfg["queue_path"])
        for e in entries:
            if e.get("id") == entry_id:
                e["status"] = "failed"
                e["post_error"] = error_msg
                e["posted_at"] = now
                break
        write_queue_atomic(entries, cfg["queue_path"])
    print(f"Marked failed: {entry_id}: {error_msg}")
    _log(cfg["log_path"], f"failed: id={entry_id} error={error_msg}")
    try:
        regenerate_review_page(entries, cfg["review_page"], cfg["review_url"])
    except Exception as exc:
        print(f"WARNING: review page write failed: {exc}", file=sys.stderr)
    return 0


def decline_entry(cfg: dict, entry_id: str, reason: str | None = None) -> int:
    now = time.time()
    with queue_locked(cfg["queue_path"]):
        entries = read_queue(cfg["queue_path"])
        for e in entries:
            if e.get("id") == entry_id:
                e["status"] = "declined"
                e["declined_at"] = now
                if reason:
                    e["decline_reason"] = reason
                break
        write_queue_atomic(entries, cfg["queue_path"])
    print(f"Declined: {entry_id}")
    _log(cfg["log_path"], f"declined: id={entry_id}")
    try:
        regenerate_review_page(entries, cfg["review_page"], cfg["review_url"])
    except Exception as exc:
        print(f"WARNING: review page write failed: {exc}", file=sys.stderr)
    return 0


def purge_null_permalink_fresh(cfg: dict, start_ts: float) -> int:
    """Purge fresh null-permalink drafts from the queue, under the write lock.

    Removes entries where ALL three conditions hold:
      - status == "queued"
      - freshly_drafted_at >= start_ts  (i.e., produced in this dispatch run)
      - source_permalink is None, empty, or not a non-empty string

    Entries that are NOT freshly drafted (freshly_drafted_at < start_ts or
    missing) are kept even if their permalink is null — those are old pool
    drafts that the operator can manually fix. Only the current-run drafts
    are purged because they were produced by a subagent that may have failed
    to resolve the URL.
    """
    log = cfg["log_path"]
    with queue_locked(cfg["queue_path"]):
        entries = read_queue(cfg["queue_path"])
        kept: list[dict] = []
        dropped = 0
        for e in entries:
            if (
                e.get("status") == "queued"
                and float(e.get("freshly_drafted_at") or 0) >= start_ts
                and not (
                    isinstance(e.get("source_permalink"), str)
                    and e["source_permalink"].strip()
                )
            ):
                dropped += 1
                _log(log, f"purge-null-permalink: dropped id={e.get('id')} (no permalink, fresh)")
            else:
                kept.append(e)
        write_queue_atomic(kept, cfg["queue_path"])
    _log(log, f"purge-null-permalink: done, dropped={dropped} kept={len(kept)}")
    print(f"dispatcher: purged {dropped} null-permalink fresh draft(s)", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# FI-TARGET-DEDUP-LEDGER (Bundle 2) — target key helpers + ledger
# ---------------------------------------------------------------------------

# Regex patterns for extracting canonical IDs from social permalinks.
_LI_URN_RE = re.compile(r"urn:li:(?:activity|share|ugcPost):(\d+)")
_X_STATUS_RE = re.compile(r"/status/(\d+)")


def target_keys(entry: dict) -> set[str]:
    """Return the set of canonical keys identifying the social post an entry
    targets.  Two entries are considered the "same target" iff their key sets
    intersect (i.e. they share at least one key).

    Key scheme
    ----------
    Primary key (when permalink resolves):
      li:<numeric_id>   — LinkedIn; URN type deliberately stripped so
                          urn:li:activity:N, urn:li:share:N, urn:li:ugcPost:N
                          all collapse to the same key when the numeric part
                          matches.
      x:<status_id>     — X (Twitter) tweet status ID.

    Fallback key (ALWAYS included):
      c:<platform>:<author_lower>:<sha256hex16>
      where the hash covers the first 200 chars of the lowercased,
      whitespace-collapsed source excerpt.  This catches the LinkedIn
      activity/share offset case (same post, slightly different numeric IDs)
      and the no-permalink case.
    """
    keys: set[str] = set()
    platform = str(entry.get("platform") or "").lower()
    permalink = str(entry.get("source_permalink") or "")

    # --- Primary key ---
    if platform == "linkedin":
        m = _LI_URN_RE.search(permalink)
        if m:
            keys.add(f"li:{m.group(1)}")
    elif platform == "x":
        m = _X_STATUS_RE.search(permalink)
        if m:
            keys.add(f"x:{m.group(1)}")

    # --- Fallback key (always) ---
    author_lower = str(entry.get("source_author") or "").strip().lower()
    raw_excerpt = _excerpt(entry)
    excerpt_norm = re.sub(r"\s+", " ", raw_excerpt.lower()).strip()[:200]
    h = hashlib.sha256(excerpt_norm.encode()).hexdigest()[:16]
    keys.add(f"c:{platform}:{author_lower}:{h}")

    return keys


def targets_match(a: dict, b: dict) -> bool:
    """Return True iff entries `a` and `b` target the same social post."""
    return bool(target_keys(a) & target_keys(b))


def load_posted_keys(ledger_path: str | Path) -> set[str]:
    """Union all `keys` arrays from the posted-target ledger into one set.

    Missing file → empty set.  Malformed lines are silently skipped.
    """
    result: set[str] = set()
    try:
        with open(ledger_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    for k in row.get("keys") or []:
                        if k:
                            result.add(str(k))
                except (json.JSONDecodeError, TypeError):
                    pass
    except FileNotFoundError:
        pass
    return result


def append_posted_ledger(
    ledger_path: str | Path,
    entry: dict,
    permalink: str,
    posted_at: float,
) -> None:
    """Append one row to the posted-target ledger.

    Caller MUST hold queue_locked() so this append is serialized with all
    other queue mutations.  Opens in "a" mode so existing rows are preserved.
    """
    row = {
        "keys": sorted(target_keys(entry)),
        "permalink": permalink,
        "posted_at": posted_at,
        "draft_id": entry.get("id"),
        "platform": entry.get("platform"),
    }
    ledger_path = Path(ledger_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")


def backfill_posted_ledger(cfg: dict) -> int:
    """CLI --backfill-posted-ledger: populate ledger from queue posted rows.

    Idempotent: re-running adds nothing (checked by draft_id).
    """
    ledger_path = cfg["posted_ledger"]
    with queue_locked(cfg["queue_path"]):
        entries = read_queue(cfg["queue_path"])
        # Build set of draft_ids already in the ledger.
        existing_ids: set[str] = set()
        try:
            with open(ledger_path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        did = row.get("draft_id")
                        if did:
                            existing_ids.add(str(did))
                    except (json.JSONDecodeError, TypeError):
                        pass
        except FileNotFoundError:
            pass

        added = 0
        already = 0
        for e in entries:
            if e.get("status") != "posted":
                continue
            eid = e.get("id")
            if not eid:
                continue
            if str(eid) in existing_ids:
                already += 1
                continue
            permalink = e.get("post_permalink") or e.get("source_permalink") or ""
            posted_at = float(e.get("posted_at") or 0)
            append_posted_ledger(ledger_path, e, permalink, posted_at)
            added += 1

    print(
        f"backfilled {added} posted targets into ledger "
        f"({already} already present)"
    )
    return 0


def dedup_fresh_against_targets(cfg: dict, start_ts: float) -> int:
    """Drop fresh queued drafts that target an already-known post.

    "Already known" means the target's keys intersect any key in:
      - all non-fresh queued entries (drafted in a prior run)
      - all pending_review / approved / posted entries
      - the persistent posted-target ledger

    Fresh entries (freshly_drafted_at >= start_ts) are processed in queue
    order: first one that survives is kept, later ones for the same target
    are dropped.  This handles the @mudler_it 4-drafts case.

    Non-fresh and non-queued rows are never dropped.
    """
    log = cfg["log_path"]
    with queue_locked(cfg["queue_path"]):
        entries = read_queue(cfg["queue_path"])

        # Build blocking key set from all non-fresh rows.
        blocking: set[str] = load_posted_keys(cfg["posted_ledger"])
        for e in entries:
            status = e.get("status", "")
            is_queued = status == "queued"
            is_fresh = float(e.get("freshly_drafted_at") or 0) >= start_ts
            if status in {"posted", "approved", "pending_review"}:
                blocking.update(target_keys(e))
            elif is_queued and not is_fresh:
                blocking.update(target_keys(e))

        # Process fresh rows in queue order.
        kept: list[dict] = []
        dropped = 0
        for e in entries:
            is_queued = e.get("status") == "queued"
            is_fresh = float(e.get("freshly_drafted_at") or 0) >= start_ts
            if is_queued and is_fresh:
                ek = target_keys(e)
                if ek & blocking:
                    dropped += 1
                    _log(
                        log,
                        f"dedup_fresh: dropped id={e.get('id')} "
                        f"(duplicate target keys={sorted(ek & blocking)})",
                    )
                    continue
                # Keep this entry and add its keys to block subsequent dups.
                blocking.update(ek)
                kept.append(e)
            else:
                kept.append(e)

        write_queue_atomic(kept, cfg["queue_path"])

    _log(log, f"dedup_fresh: dropped {dropped} duplicate-target fresh draft(s)")
    print(f"dedup_fresh: dropped {dropped} duplicate-target fresh draft(s)",
          file=sys.stderr)
    return 0


def _parse_value_flag(args: list[str], name: str) -> str | None:
    """Return the value for --name=value or --name value, else None."""
    eq_prefix = f"{name}="
    for arg in args:
        if arg.startswith(eq_prefix):
            return arg[len(eq_prefix):]
    if name in args:
        idx = args.index(name)
        if idx + 1 < len(args):
            return args[idx + 1]
    return None


def drip_single(
    cfg: dict,
    *,
    platform: str,
    source: str = "any",
    start_ts: float | None = None,
) -> int:
    """FI-DRIP-IST-WINDOW (2026-06-06): pop EXACTLY 1 draft from the
    specified platform and DM the operator. Replaces the hybrid mode for
    the new 12-fires/day schedule where each hour surfaces a single draft
    and the dispatcher alternates platforms by IST hour parity.

    source="fresh": filter X drafts by freshly_drafted_at >= start_ts
                    (only meaningful for platform="x" on a subagent-run hour).
    source="any":   pool mode; sort by queued_at ascending, oldest first.
                    (LinkedIn always uses this; X falls back here when no
                    fresh subagent ran this hour.)

    Always emits exactly one DM. When the chosen pool is empty, the DM
    explicitly says so + reports the LI-pool age so the operator can spot
    a stalled refill without scrolling logs.
    """
    log = cfg["log_path"]
    if Path(cfg["pause_path"]).exists():
        _log(log, f"drip[single:{platform}]: paused; exiting")
        return 0
    if platform not in ("x", "linkedin"):
        _log(log, f"drip[single]: invalid platform={platform!r}; exiting")
        return 1

    entries = read_queue(cfg["queue_path"])
    _log(
        log,
        f"drip[single:{platform}]: read {len(entries)} entries "
        f"(source={source}, start_ts={start_ts})",
    )

    pool = [e for e in entries if e.get("status") == "queued"
            and e.get("platform") == platform]
    if source == "fresh" and start_ts is not None:
        pool = [
            e for e in pool
            if float(e.get("freshly_drafted_at") or 0) >= start_ts
        ]
        pool.sort(key=lambda e: e.get("freshly_drafted_at") or 0, reverse=True)
    else:
        pool.sort(key=lambda e: e.get("queued_at") or 0)
    _log(log, f"drip[single:{platform}]: eligible={len(pool)}")

    li_age_hours = _hours_since_last_li_queued_append(entries)
    li_stalled_line = ""
    if platform == "linkedin":
        if li_age_hours is None:
            li_stalled_line = (
                "\n[WARNING] No LinkedIn drafts have ever been queued — "
                "social-manager may not be picking up refills. Check the "
                "social-manager lead's health.\n"
            )
        elif li_age_hours >= 3.0:
            li_stalled_line = (
                f"\n[WARNING] No LinkedIn drafts queued in "
                f"{li_age_hours:.1f}h. social-manager refill loop may be "
                "stalled — check the lead.\n"
            )

    if not pool:
        _log(log, f"drip[single:{platform}]: nothing eligible — empty DM")
        plat_label = _platform_label(platform)
        body = (
            f"[{plat_label} hour] No engagement draft available this hour.\n"
            "\n"
            f"The {plat_label} pool is empty"
            + (" and the fresh subagent produced nothing." if source == "fresh"
               else ".")
            + "\n"
        )
        if li_stalled_line:
            body += li_stalled_line
        if cfg.get("review_url"):
            body += f"\nReview queue: {cfg['review_url']}\n"
        try:
            if send_telegram_dm(cfg["tg_token"], cfg["tg_chat_id"], body):
                _log(log, f"drip[single:{platform}]: empty-hour DM sent")
        except Exception as exc:
            _log(log, f"WARNING: empty-hour DM failed: {exc}")
        return 0

    with queue_locked(cfg["queue_path"]):
        entries = read_queue(cfg["queue_path"])
        pool = [e for e in entries if e.get("status") == "queued"
                and e.get("platform") == platform]
        if source == "fresh" and start_ts is not None:
            pool = [
                e for e in pool
                if float(e.get("freshly_drafted_at") or 0) >= start_ts
            ]
            pool.sort(key=lambda e: e.get("freshly_drafted_at") or 0, reverse=True)
        else:
            pool.sort(key=lambda e: e.get("queued_at") or 0)
        if not pool:
            _log(log, f"drip[single:{platform}]: pool empty after re-read under lock (race); skipping pop")
            entry = None
        else:
            entry = pool[0]
            entry["status"] = "pending_review"
            entry["released_at"] = time.time()
            _log(
                log,
                f"drip[single:{platform}]: popped id={entry['id']} "
                f"platform={entry.get('platform')}",
            )
            write_queue_atomic(entries, cfg["queue_path"])
            _log(log, f"drip[single:{platform}]: queue written atomically")

    if entry is None:
        return 0

    try:
        regenerate_review_page(entries, cfg["review_page"], cfg["review_url"])
        _log(log, f"drip[single:{platform}]: review page written")
    except Exception as exc:
        _log(log, f"WARNING: review page write failed: {exc}")

    plat_label = _platform_label(entry.get("platform", ""))
    author = entry.get("source_author", "")
    eid = entry.get("id", "?")
    src_marker = "FRESH" if source == "fresh" else "POOL"
    dm_lines = [
        f"[{src_marker} {plat_label}] Engagement draft ready for review:",
        "",
        f"{plat_label}: {author} (id: {eid})",
    ]
    if li_stalled_line:
        dm_lines.append(li_stalled_line.rstrip("\n"))
    dm_lines.append("")
    if cfg.get("review_url"):
        dm_lines.append(f"Review: {cfg['review_url']}")
        dm_lines.append("")
    dm_lines.append("Commands: approve <id> | approve all | decline <id>")
    dm_text = "\n".join(dm_lines)

    try:
        if send_telegram_dm(cfg["tg_token"], cfg["tg_chat_id"], dm_text):
            _log(log, f"drip[single:{platform}]: Telegram DM sent")
        else:
            missing = []
            if not cfg["tg_token"]:
                missing.append("TELEGRAM_BOT_TOKEN")
            if not cfg["tg_chat_id"]:
                missing.append("HERMES_NOTIFY_CHAT_ID")
            _log(
                log,
                f"WARNING: Telegram DM skipped — missing env: "
                + ", ".join(missing),
            )
    except Exception as exc:
        _log(log, f"WARNING: Telegram DM failed: {exc}")

    return 0


def main() -> int:
    cfg = _cfg()
    args = sys.argv[1:]

    if "--regen-only" in args:
        return regen_only(cfg)

    if "--backfill-posted-ledger" in args:
        return backfill_posted_ledger(cfg)

    dedup_ts_raw = _parse_value_flag(args, "--dedup-fresh-against-targets")
    if dedup_ts_raw is not None:
        try:
            dedup_ts = float(dedup_ts_raw)
        except ValueError:
            print(
                f"ERROR: --dedup-fresh-against-targets requires an epoch float, "
                f"got {dedup_ts_raw!r}",
                file=sys.stderr,
            )
            return 1
        return dedup_fresh_against_targets(cfg, dedup_ts)

    purge_ts_raw = _parse_value_flag(args, "--purge-null-permalink-fresh")
    if purge_ts_raw is not None:
        try:
            purge_ts = float(purge_ts_raw)
        except ValueError:
            print(
                f"ERROR: --purge-null-permalink-fresh requires an epoch float, "
                f"got {purge_ts_raw!r}",
                file=sys.stderr,
            )
            return 1
        return purge_null_permalink_fresh(cfg, purge_ts)

    if "--approve-all" in args:
        return approve_entries(cfg, all_pending=True)

    if "--approve" in args:
        idx = args.index("--approve")
        ids = args[idx + 1 :]
        if not ids:
            print("ERROR: --approve requires at least one id", file=sys.stderr)
            return 1
        return approve_entries(cfg, ids=ids)

    if "--posted-error" in args:
        idx = args.index("--posted-error")
        try:
            entry_id = args[idx + 1]
            error_msg = args[idx + 2]
        except IndexError:
            print("ERROR: --posted-error requires <id> <msg>", file=sys.stderr)
            return 1
        return mark_posted_error(cfg, entry_id, error_msg)

    if "--posted" in args:
        idx = args.index("--posted")
        try:
            entry_id = args[idx + 1]
            permalink = args[idx + 2]
        except IndexError:
            print("ERROR: --posted requires <id> <permalink>", file=sys.stderr)
            return 1
        return mark_posted(cfg, entry_id, permalink)

    if "--decline" in args:
        idx = args.index("--decline")
        try:
            entry_id = args[idx + 1]
        except IndexError:
            print("ERROR: --decline requires <id>", file=sys.stderr)
            return 1
        reason: str | None = None
        if "--reason" in args:
            ridx = args.index("--reason")
            try:
                reason = args[ridx + 1]
            except IndexError:
                pass
        return decline_entry(cfg, entry_id, reason)

    # FI-DRIP-IST-WINDOW (2026-06-06): single-platform mode for the 12 fires/day
    # IST 10-21 schedule. Dispatcher picks the platform by IST hour parity.
    single_platform = _parse_value_flag(args, "--single-platform")
    if single_platform is not None:
        source_flag = _parse_value_flag(args, "--source") or "any"
        start_ts_raw = _parse_value_flag(args, "--start-ts")
        start_ts: float | None = None
        if start_ts_raw is not None:
            try:
                start_ts = float(start_ts_raw)
            except ValueError:
                print(
                    f"ERROR: --start-ts must be an epoch float, got {start_ts_raw!r}",
                    file=sys.stderr,
                )
                return 1
        return drip_single(
            cfg,
            platform=single_platform.lower(),
            source=source_flag,
            start_ts=start_ts,
        )

    # FI-ENGAGEMENT-HYBRID (2026-06-05): one drip call, X-fresh + LI-pool,
    # one DM. Used by the new engagement-hourly-dispatch.sh. The --start-ts
    # parameter still gates X's freshness window.
    if "--hybrid" in args:
        start_ts_raw = _parse_value_flag(args, "--start-ts")
        if start_ts_raw is None:
            print(
                "ERROR: --hybrid requires --start-ts <epoch>",
                file=sys.stderr,
            )
            return 1
        try:
            start_ts = float(start_ts_raw)
        except ValueError:
            print(
                f"ERROR: --start-ts must be an epoch float, got {start_ts_raw!r}",
                file=sys.stderr,
            )
            return 1
        return drip_hybrid(cfg, start_ts=start_ts)

    # New hourly-dispatch flags (FI-ENGAGEMENT-FRESH-DRIP, 2026-06-05).
    source_flag = _parse_value_flag(args, "--source")
    start_ts_raw = _parse_value_flag(args, "--start-ts")
    fallback_reason = _parse_value_flag(args, "--fallback-reason") or ""
    is_fallback = "--fallback" in args

    start_ts: float | None = None
    if start_ts_raw:
        try:
            start_ts = float(start_ts_raw)
        except ValueError:
            print(f"ERROR: --start-ts must be an epoch float, got {start_ts_raw!r}",
                  file=sys.stderr)
            return 1

    if source_flag == "fresh":
        return drip(cfg, source="fresh", start_ts=start_ts, banner="FRESH")
    if source_flag == "any" or is_fallback:
        banner = "POOLED FALLBACK" if is_fallback else "POOLED"
        return drip(
            cfg,
            source="any",
            banner=banner,
            on_empty_emit_dm=is_fallback,
            fallback_reason=fallback_reason,
        )

    # Back-compat: no flag = legacy pooled-only behavior, no empty-DM.
    return drip(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
