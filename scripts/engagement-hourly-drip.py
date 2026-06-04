#!/usr/bin/env python3
"""Mechanical hourly drip: pops 1 X + 1 LinkedIn from engagement queue,
marks pending_review, regenerates review page, DMs operator.
Zero LLM tokens on this path.

Modes (via argv):
  (no flag)          -- run hourly drip
  --regen-only       -- regenerate review page only; no queue mutation
  --approve <id...>  -- mark ids approved; regenerate review page
  --approve-all      -- mark all pending_review approved; regenerate
  --posted <id> <permalink>       -- mark id posted with permalink
  --posted-error <id> <msg>       -- mark id failed with error message
  --decline <id> [--reason <txt>] -- mark id declined
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
        "review_url": os.environ.get("HERMES_ENGAGEMENT_REVIEW_URL", ""),
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
    return entries


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


def regenerate_review_page(
    entries: list[dict], out_path: str | Path, review_url: str
) -> None:
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    pending = [e for e in entries if e.get("status") == "pending_review"]
    approved = [e for e in entries if e.get("status") == "approved"]
    posted = sorted(
        [e for e in entries if e.get("status") == "posted"],
        key=lambda e: e.get("posted_at") or 0,
    )[-10:]

    lines: list[str] = []
    lines.append("# Engagement Review")
    lines.append("")
    lines.append(f"_Last regenerated: {now_str}_")
    lines.append("")

    if not pending and not approved:
        lines.append(
            "No drafts awaiting review at the moment. "
            "Run the drip script (`systemctl start claude-soma-engagement-drip.service`) "
            "or wait for the next hourly fire."
        )
    else:
        lines.append("Reply via Telegram to act on drafts:")
        lines.append("- `approve <id>` — approve a single draft")
        lines.append("- `approve all` — approve every pending_review draft")
        lines.append("- `decline <id>` — decline a draft (won't post)")
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(f"## Pending Review ({len(pending)})")
        lines.append("")
        for e in pending:
            eid = e.get("id", "unknown")
            plat = _platform_label(e.get("platform", ""))
            author = e.get("source_author", "")
            lines.append(f"### #{eid} · {plat} · {author}")
            lines.append("")
            lines.append(f"**Source:** {e.get('source_permalink', '')}")
            lines.append("")
            lines.append(f"**Why engage:** {e.get('why_engage', '')}")
            lines.append("")
            lines.append("**Source excerpt:**")
            lines.append(f"> {e.get('source_excerpt', '')}")
            lines.append("")
            lines.append("**Draft:**")
            lines.append(f"> {e.get('draft_text', '')}")
            lines.append("")
            lines.append(f"`approve {eid}` | `decline {eid}`")
            lines.append("")
            lines.append("---")
            lines.append("")

        lines.append(f"## Approved · awaiting post ({len(approved)})")
        lines.append("")
        for e in approved:
            eid = e.get("id", "unknown")
            plat = _platform_label(e.get("platform", ""))
            author = e.get("source_author", "")
            approved_at = e.get("approved_at")
            approved_str = (
                datetime.fromtimestamp(approved_at, tz=timezone.utc).strftime("%H:%M")
                if approved_at
                else "—"
            )
            lines.append(f"### #{eid} · {plat} · {author}")
            lines.append("")
            lines.append(f"**Source:** {e.get('source_permalink', '')}")
            lines.append("")
            lines.append("**Draft:**")
            lines.append(f"> {e.get('draft_text', '')}")
            lines.append("")
            lines.append(f"_Approved at {approved_str}_")
            lines.append("")
            lines.append("---")
            lines.append("")

    lines.append("")
    lines.append("## Recently posted (showing last 10)")
    lines.append("")
    if posted:
        lines.append("| ID | Platform | Posted at | Permalink |")
        lines.append("|---|---|---|---|")
        for e in posted:
            eid = e.get("id", "unknown")
            plat = _platform_label(e.get("platform", ""))
            posted_str = _fmt_ts(e.get("posted_at"))
            permalink = e.get("post_permalink") or "—"
            lines.append(f"| {eid} | {plat} | {posted_str} | {permalink} |")
    else:
        lines.append("_No posts yet._")
    lines.append("")

    content = "\n".join(lines)
    out_path = Path(out_path)
    tmp = Path(str(out_path) + ".tmp")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.replace(tmp, out_path)


def send_telegram_dm(token: str, chat_id: str, text: str) -> bool:
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(url, data=data)
    urllib.request.urlopen(req, timeout=5)
    return True


def drip(cfg: dict) -> int:
    log = cfg["log_path"]

    if Path(cfg["pause_path"]).exists():
        _log(log, "drip: paused (PAUSE file present); exiting")
        return 0

    entries = read_queue(cfg["queue_path"])
    _log(log, f"drip: read {len(entries)} entries from queue")

    queued = [e for e in entries if e.get("status") == "queued"]
    by_platform: dict[str, list[dict]] = {}
    for e in queued:
        plat = e.get("platform", "")
        by_platform.setdefault(plat, []).append(e)

    for plat in by_platform:
        by_platform[plat].sort(key=lambda e: e.get("queued_at") or 0)

    to_pop: list[dict] = []
    for plat in ("x", "linkedin"):
        if by_platform.get(plat):
            to_pop.append(by_platform[plat][0])

    if not to_pop:
        _log(log, "drip: no queued drafts; exiting")
        return 0

    now = time.time()
    for entry in to_pop:
        entry["status"] = "pending_review"
        entry["released_at"] = now
        _log(log, f"drip: popped id={entry['id']} platform={entry.get('platform')}")

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

    dm_lines = ["Engagement drafts ready for review:", ""]
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
    entries = read_queue(cfg["queue_path"])
    id_set = set(ids) if ids else set()
    now = time.time()
    approved_ids: list[str] = []
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
    entries = read_queue(cfg["queue_path"])
    now = time.time()
    for e in entries:
        if e.get("id") == entry_id:
            e["status"] = "posted"
            e["post_permalink"] = permalink
            e["posted_at"] = now
            break
    write_queue_atomic(entries, cfg["queue_path"])
    print(f"Marked posted: {entry_id} -> {permalink}")
    _log(cfg["log_path"], f"posted: id={entry_id} permalink={permalink}")
    try:
        regenerate_review_page(entries, cfg["review_page"], cfg["review_url"])
    except Exception as exc:
        print(f"WARNING: review page write failed: {exc}", file=sys.stderr)
    return 0


def mark_posted_error(cfg: dict, entry_id: str, error_msg: str) -> int:
    entries = read_queue(cfg["queue_path"])
    now = time.time()
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
    entries = read_queue(cfg["queue_path"])
    now = time.time()
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


def main() -> int:
    cfg = _cfg()
    args = sys.argv[1:]

    if "--regen-only" in args:
        return regen_only(cfg)

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

    return drip(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
