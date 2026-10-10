#!/usr/bin/env python3.12
"""Definition-of-Done gate for the opencode-soma research task (stdlib only).

    python3.12 -I research/opencode-soma/probes/check_dod.py [--rerun] [-v]

Prints one PASS/FAIL/SKIP line per check, then `GATE: PASS` or `GATE: FAIL`; exit 1 on any FAIL.
The authoritative run is the one with --rerun (without it C10 is SKIP).

Paths below are relative to research/opencode-soma/ unless they start with the repo root.
"Committed" means tracked by git and unmodified in the working tree; only committed files count
as evidence (*.log is gitignored repo-wide, so logs need `git add -f`).

C1  FEASIBILITY.md ARCHITECTURE.md MEMORY-DESIGN.md PLATFORM-MATRIX.md ROADMAP.md RISKS.md
    START-HERE.md exist, are non-empty and committed; ARCHITECTURE.md has a closed, non-empty
    ```mermaid block; probes/ and evidence/ each hold >=1 committed file.
C2  FEASIBILITY.md: first non-blank line is `# Title`; the next block is one prose paragraph
    (no heading/table/list/quote/fence lines, >=30 words, contains verdict|feasible|infeasible|
    feasibility). The first table after it is the parity table: header row, separator row, then
    exactly 12 rows whose first cell is 1..12 in order, each with as many cells as the header
    (escape | inside cells as \\|). The header has a column whose title contains "Verdict"; that
    cell holds exactly one of PARITY DEGRADED NEEDS-BUILD IMPOSSIBLE. Every code span shaped
    `path:N` or `path:N-M` (path has a / or a file extension, or is a tracked file) must resolve:
    repo-root-relative (evidence/... and probes/... resolve under research/opencode-soma/), file
    committed, N>=1, M>=N, file has >=M lines. Each row needs >=1 such citation of a claude-soma
    file (a tracked file outside research/opencode-soma/). Every `evidence/...` path in the row must
    exist and be committed (a directory must hold a committed non-empty file). Each row needs an
    existing evidence path OR the literal desk-research-only followed by a reason (>=3 words, same
    cell). PARITY/DEGRADED rows need >=1 committed non-empty evidence FILE outside evidence/verify/.
C3  For n in 1..7 a directory evidence/opencode/P<n>-*/ holds a committed, non-empty *.log (any
    depth) with a line starting `opencode-version: 1.18.8` and a line starting `VERDICT: <text>`
    (text not TODO/TBD/PENDING). No log under evidence/opencode/ may have a line starting
    `opencode-version:` with any other value.
C4  evidence/memory/bench-results.json (valid JSON, committed) has >=1 Neo4j row: an object with a
    string value starting "neo4j" (or nested under a key starting "neo4j") and a positive finite
    number under a timing key (key tokens ms s sec secs seconds us ns time timing latency duration
    elapsed wall p50 p90 p95 p99; nested objects allowed, lists not). evidence/memory/
    neo4j-container.json (committed) has a positive number whose key path has cold+start (or
    coldstart) and one whose key path has a token containing rss (rss_mb, java_vmrss_mb).
    Fallback when those are absent: MEMORY-DESIGN.md has a line starting `Neo4j rejected`
    (markdown emphasis/bullets allowed) whose paragraph carries >=1 number with a unit (ms s sec
    min MB MiB GB GiB KB KiB % x), and every such number appears in a committed file under
    evidence/memory/.
C5  ROADMAP.md: some heading containing the token P0 opens a section (to the next heading of the
    same or higher level) holding a line starting `Acceptance` (bullet/bold allowed) that has a
    non-empty code span, or whose next non-blank line opens a non-empty fenced block.
C6  RISKS.md: the risks are one top-level ordered list (`1.` at column 0, numbered 1..N with no gap
    or restart, N>=3; rank = order). Every item (up to the next item or heading) has the label
    `Cheapest early test` (or `Cheapest test`), case-insensitive, then `:` or a dash, then >=3
    words or a code span, on the same line or the next non-blank line.
C7  START-HERE.md: every command in fenced blocks tagged bash/sh/zsh/shell/console or untagged (tag
    other blocks, e.g. ```text) appears verbatim, as a whitespace-delimited unit, in a committed
    *.log under evidence/start-here/. Commands: non-blank, non-# lines (a leading `$ ` is dropped;
    in console blocks only `$ ` lines are commands); `\\` continuations and heredoc bodies join
    their command; whitespace is collapsed on both sides. Logs must hold the unexpanded text.
C8  git: origin/main resolves; the branch has >=1 commit; `git diff --name-only --no-renames
    origin/main...HEAD` is entirely under research/opencode-soma/; branch commit messages contain
    no Co-Authored-By, Claude-Session or Generated with (case-insensitive) and no emoji; every
    branch commit author equals git config user.name/user.email; the working tree has no modified,
    staged or untracked file and no gitignored file under evidence/ (evidence/verify/ exempt).
C9  evidence/opencode/isolation-check.log is committed and non-empty; names ~/.config/opencode,
    ~/.local/share/opencode, ~/.opencode and ~/.claude; every VERDICT: line starts PASS or
    UNCHANGED; no line has the word CHANGED (UNCHANGED is fine); it records `snapshot-utc:` and
    `date-utc:` (YYYY-MM-DDTHH:MM:SSZ) and every committed evidence *.log with a line starting
    `opencode-version:` (outside evidence/verify/) has a `date-utc:` inside that window.
C10 (--rerun) re-runs the pinned RERUN_LIST from a clean `git archive HEAD` copy, in fresh sandboxes
    under $TMPDIR/ocsoma-vf/ (OCSOMA_REAL_TMPDIR, TMPDIR, HOME and XDG_* point there), and requires
    each new list of VERDICT tokens (first word after `VERDICT:`) to equal the committed one;
    transcripts go to evidence/verify/rerun/. FAILs with "rerun list not yet pinned" until the
    list covers P1-resume, P2-gate, P3-scoping, P5-tmux and neo4j-timing.
C11 no file under research/opencode-soma/ (outside venv/cache dirs) contains a credential-shaped
    string (API keys, OAuth/bearer tokens, private keys); only file:line is printed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import math
import os
import re
import subprocess
import sys
import tarfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parents[3]
RS_REL = "research/opencode-soma"
RS = ROOT / RS_REL
EV_REL = f"{RS_REL}/evidence"
EV = ROOT / EV_REL
VERIFY_REL = f"{EV_REL}/verify"
OC_VERSION = "1.18.8"
DELIVERABLES = (
    "FEASIBILITY.md",
    "ARCHITECTURE.md",
    "MEMORY-DESIGN.md",
    "PLATFORM-MATRIX.md",
    "ROADMAP.md",
    "RISKS.md",
    "START-HERE.md",
)
VERDICTS = ("PARITY", "DEGRADED", "NEEDS-BUILD", "IMPOSSIBLE")
N_ITEMS = 12
PROBE_GROUPS = range(1, 8)
MAX_SHOWN = 8
SKIP_DIRS = {".venv", "venv", "node_modules", "__pycache__", ".git", ".mypy_cache", ".ruff_cache"}

# C10: pinned once the probe scripts land. Each entry: id, tags, argv, committed (repo-relative log
# in HEAD to compare with), produces (repo-relative path the script writes, inside the clean copy),
# env (extra variables), timeout. Placeholders in argv/env: {tree} = clean `git archive HEAD` copy,
# {base} = this entry's fresh directory under $TMPDIR/ocsoma-vf/rerun-<stamp>/.
RERUN_LIST: list[dict[str, Any]] = []
RERUN_REQUIRED_TAGS = ("P1-resume", "P2-gate", "P3-scoping", "P5-tmux", "neo4j-timing")
SECRET_ENV = re.compile(
    r"^(CLAUDE|ANTHROPIC|OPENAI|OPENCODE_|AWS_|GOOGLE_|GEMINI|GITHUB_|GH_|AZURE_|GROQ|MISTRAL)"
    r"|_API_KEY$|TOKEN|SECRET|PASSWORD|^TMUX$|^TMUX_PANE$"
)


class GitError(RuntimeError):
    pass


def git(*args: str, timeout: int = 120) -> str:
    cmd = ["git", "-C", str(ROOT), "-c", "core.quotepath=off", *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=True)
    except FileNotFoundError as exc:
        raise GitError("git binary not found") from exc
    except subprocess.CalledProcessError as exc:
        raise GitError(f"git {' '.join(args)} exited {exc.returncode}: {exc.stderr[-500:].strip()}")
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args)} timed out after {timeout}s") from exc
    return proc.stdout


class Repo:
    def __init__(self) -> None:
        self.tracked = {p for p in git("ls-files", "-z").split("\0") if p}
        self.status: dict[str, str] = {}
        out = git(
            "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=matching",
            "--no-renames",
        )
        for ent in out.split("\0"):
            if len(ent) > 3:
                self.status[ent[3:]] = ent[:2]

    def state(self, rel: str) -> str:
        code = self.status.get(rel)
        if code == "??":
            return "untracked"
        if code == "!!":
            return "gitignored and not committed (git add -f)"
        if code is not None:
            return f"has uncommitted changes ({code.strip()})"
        if rel not in self.tracked:
            return "not committed"
        return "ok"

    def committed_under(self, prefix: str) -> list[str]:
        pre = prefix.rstrip("/") + "/"
        return sorted(t for t in self.tracked if t.startswith(pre) and self.state(t) == "ok")


_REPO: Repo | None = None


def repo() -> Repo:
    global _REPO
    if _REPO is None:
        _REPO = Repo()
    return _REPO


@dataclass
class Result:
    cid: str
    status: str
    summary: str
    details: list[str] = field(default_factory=list)


def finish(cid: str, problems: list[str], ok_summary: str) -> Result:
    if not problems:
        return Result(cid, "PASS", ok_summary)
    shown = "; ".join(problems[:MAX_SHOWN])
    if len(problems) > MAX_SHOWN:
        shown += f" (+{len(problems) - MAX_SHOWN} more, run with -v)"
    return Result(cid, "FAIL", shown, problems)


def rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def line_count(p: Path) -> int:
    data = p.read_bytes()
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def committed_problem(p: Path) -> str | None:
    r = rel(p)
    if not p.is_file() or p.is_symlink():
        return f"{r} missing"
    st = repo().state(r)
    if st != "ok":
        return f"{r} {st}"
    return None


# ---------- markdown helpers ----------

FENCE_OPEN = re.compile(r"^(?P<ind>[ \t]*)(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
FENCE_CLOSE = re.compile(r"^[ \t]*(`{3,}|~{3,})[ \t]*$")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
CODE_SPAN = re.compile(r"`([^`]+)`")


@dataclass
class Block:
    lang: str
    lines: list[str]
    closed: bool
    start: int


def fenced_blocks(lines: list[str]) -> list[Block]:
    out: list[Block] = []
    i = 0
    while i < len(lines):
        m = FENCE_OPEN.match(lines[i])
        if not m or (m.group("fence")[0] == "`" and "`" in m.group("info")):
            i += 1
            continue
        fence = m.group("fence")
        ind = len(m.group("ind").expandtabs(4))
        info = m.group("info").strip()
        lang = info.split()[0].strip("{}.").lower() if info else ""
        body: list[str] = []
        j = i + 1
        closed = False
        while j < len(lines):
            c = FENCE_CLOSE.match(lines[j])
            if c and c.group(1)[0] == fence[0] and len(c.group(1)) >= len(fence):
                closed = True
                break
            line = lines[j]
            k = 0
            while k < ind and k < len(line) and line[k] == " ":
                k += 1
            body.append(line[k:])
            j += 1
        out.append(Block(lang, body, closed, i))
        i = j + 1
    return out


def fence_mask(lines: list[str]) -> list[bool]:
    mask = [False] * len(lines)
    for b in fenced_blocks(lines):
        end = b.start + len(b.lines) + (1 if b.closed else 0)
        for k in range(b.start, min(end + 1, len(lines))):
            mask[k] = True
    return mask


def split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", s)]


VERDICT_LINE = re.compile(r"^[ \t]*VERDICT:[ \t]*(\S.*?)\s*$", re.M)
VER_LINE = re.compile(r"^[ \t]*opencode-version:[ \t]*(\S*)", re.M)
PLACEHOLDERS = {"TODO", "TBD", "PENDING", "XXX", "FIXME"}


def verdict_lines(text: str) -> list[str]:
    return [m.group(1) for m in VERDICT_LINE.finditer(text)]


def verdict_token(v: str) -> str:
    m = re.match(r"[A-Za-z0-9_+-]+", v)
    return m.group(0).upper() if m else v.split()[0].upper()


# ---------- C1 ----------


def c1() -> Result:
    probs: list[str] = []
    for name in DELIVERABLES:
        p = RS / name
        prob = committed_problem(p)
        if prob:
            probs.append(prob)
        if p.is_file() and not read(p).strip():
            probs.append(f"{name} is empty")
    arch = RS / "ARCHITECTURE.md"
    if arch.is_file():
        blocks = fenced_blocks(read(arch).splitlines())
        if not any(b.lang == "mermaid" and b.closed and any(x.strip() for x in b.lines)
                   for b in blocks):
            probs.append("ARCHITECTURE.md has no closed, non-empty ```mermaid block")
    for d in ("probes", "evidence"):
        if not (RS / d).is_dir():
            probs.append(f"{d}/ missing")
        elif not repo().committed_under(f"{RS_REL}/{d}"):
            probs.append(f"{d}/ has no committed file")
    return finish("C1", probs, "7 deliverables committed, mermaid block present, probes/ and "
                               "evidence/ populated")


# ---------- C2 ----------

PROSE_BAD = re.compile(
    r"^\s{0,3}(#|\||>|[-*+]\s|\d+[.)]\s|```|~~~|<|(-{2,}|={2,}|\*{3,}|_{3,})\s*$)"
)
VERDICT_TOKEN = re.compile(r"(?<![\w-])(PARITY|DEGRADED|NEEDS-BUILD|IMPOSSIBLE)(?![\w-])")
CITE = re.compile(r"(?P<path>[A-Za-z0-9_./-]+):(?P<a>\d+)(?:-(?P<b>\d+))?")
EV_REF = re.compile(r"(?<![\w.-])evidence/[\w.-]+(?:/[\w.-]+)*/?")
TABLE_SEP = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
DESK = "desk-research-only"


def resolve_ref(path: str) -> tuple[str, str]:
    p = path
    while p.startswith("./"):
        p = p[2:]
    if p.startswith("/"):
        return "", "absolute path"
    if ".." in p.split("/"):
        return "", "'..' in path"
    if p.startswith(("evidence/", "probes/")):
        p = f"{RS_REL}/{p}"
    return p.rstrip("/"), ""


def check_citation(span: str) -> tuple[str, str | None]:
    m = CITE.fullmatch(span.strip())
    if not m:
        return "none", None
    path = m.group("path")
    if not ("/" in path or re.search(r"\.[A-Za-z][A-Za-z0-9]{0,7}$", path)
            or path in repo().tracked):
        return "none", None
    relp, err = resolve_ref(path)
    if err:
        return "bad", f"`{span}`: {err}"
    a, b = int(m.group("a")), int(m.group("b") or m.group("a"))
    if a < 1 or b < a:
        return "bad", f"`{span}`: bad line range"
    fp = ROOT / relp
    if not fp.is_file():
        return "bad", f"`{span}`: file not found"
    st = repo().state(relp)
    if st != "ok":
        return "bad", f"`{span}`: {relp} {st}"
    n = line_count(fp)
    if b > n:
        return "bad", f"`{span}`: {relp} has only {n} lines"
    return ("research" if relp.startswith(RS_REL + "/") else "soma"), None


def check_evidence_ref(ref: str) -> tuple[str, str | None]:
    r = ref.rstrip(".")
    relp, err = resolve_ref(r)
    if err:
        return "bad", f"{ref}: {err}"
    fp = ROOT / relp
    if fp.is_symlink():
        return "bad", f"{ref}: is a symlink"
    if fp.is_file():
        st = repo().state(relp)
        if st != "ok":
            return "bad", f"{ref}: {st}"
        if fp.stat().st_size == 0:
            return "bad", f"{ref}: empty file"
        return "file", None
    if fp.is_dir():
        if not any((ROOT / t).stat().st_size > 0 for t in repo().committed_under(relp)):
            return "bad", f"{ref}: directory holds no committed non-empty file"
        return "dir", None
    return "bad", f"{ref}: does not exist"


def c2() -> Result:
    p = RS / "FEASIBILITY.md"
    if not p.is_file():
        return finish("C2", ["FEASIBILITY.md missing"], "")
    lines = read(p).splitlines()
    probs: list[str] = []
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or not re.match(r"^#\s+\S", lines[i]):
        return finish("C2", ["first non-blank line must be the `# Title`"], "")
    i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    para: list[str] = []
    while i < len(lines) and lines[i].strip():
        para.append(lines[i])
        i += 1
    bad = [ln for ln in para if PROSE_BAD.match(ln)]
    text = " ".join(para)
    nwords = len(re.findall(r"[A-Za-z0-9][\w'-]*", text))
    if not para or bad:
        probs.append("the block after the title must be a prose verdict paragraph (no heading, "
                     "table, list, quote or fence lines)")
    elif nwords < 30:
        probs.append(f"verdict paragraph has {nwords} words (<30)")
    elif not re.search(r"(?i)\b(verdict|feasible|infeasible|feasibility)\b", text):
        probs.append("verdict paragraph must state the verdict (verdict/feasible/infeasible)")
    mask = fence_mask(lines)
    while i < len(lines) and (mask[i] or not lines[i].lstrip().startswith("|")):
        i += 1
    table: list[tuple[int, str]] = []
    while i < len(lines) and lines[i].lstrip().startswith("|"):
        table.append((i + 1, lines[i]))
        i += 1
    if len(table) < 2 or not TABLE_SEP.match(table[1][1]):
        probs.append("no markdown table (header + separator row) after the verdict paragraph")
        return finish("C2", probs, "")
    hdr = split_row(table[0][1])
    vcol = next((k for k, h in enumerate(hdr) if "verdict" in h.lower()), None)
    if vcol is None:
        probs.append("parity table header has no column titled Verdict")
    rows = table[2:]
    firsts = [split_row(r)[0] for _, r in rows]
    if firsts != [str(k) for k in range(1, N_ITEMS + 1)]:
        probs.append(f"parity table rows must be exactly | 1 | .. | {N_ITEMS} | in order; "
                     f"first cells are {firsts}")
    counts = {v: 0 for v in VERDICTS}
    for lineno, raw in rows:
        cells = split_row(raw)
        label = f"row {cells[0]} (line {lineno})"
        if len(cells) != len(hdr):
            probs.append(f"{label}: {len(cells)} cells but header has {len(hdr)} "
                         "(escape | inside cells as \\|)")
        verdict = None
        if vcol is not None:
            toks = sorted(set(VERDICT_TOKEN.findall(cells[vcol] if vcol < len(cells) else "")))
            if len(toks) != 1:
                probs.append(f"{label}: Verdict cell must hold exactly one of "
                             f"{'/'.join(VERDICTS)} (found {toks or 'none'})")
            else:
                verdict = toks[0]
                counts[verdict] += 1
        soma = 0
        for span in CODE_SPAN.findall(raw):
            kind, prob = check_citation(span)
            if prob:
                probs.append(f"{label}: {prob}")
            elif kind == "soma":
                soma += 1
        if soma == 0:
            probs.append(f"{label}: no valid claude-soma citation `path:line` or `path:line-line`")
        ev_path = ev_file = 0
        for m in EV_REF.finditer(raw):
            kind, prob = check_evidence_ref(m.group(0))
            if prob:
                probs.append(f"{label}: evidence {prob}")
                continue
            ev_path += 1
            if kind == "file" and not resolve_ref(m.group(0).rstrip("."))[0].startswith(
                    VERIFY_REL + "/"):
                ev_file += 1
        desk_ok = False
        if DESK in raw:
            rest = re.split(r"(?<!\\)\|", raw.split(DESK, 1)[1], maxsplit=1)[0]
            desk_ok = len(re.findall(r"[A-Za-z]{2,}", rest)) >= 3
            if not desk_ok:
                probs.append(f"{label}: {DESK} must be followed by a reason (>=3 words)")
        if verdict in ("PARITY", "DEGRADED"):
            if ev_file == 0:
                probs.append(f"{label}: {verdict} needs >=1 committed evidence file under "
                             f"evidence/ ({DESK} is not enough)")
        elif ev_path == 0 and not desk_ok:
            probs.append(f"{label}: needs an existing evidence/ path or {DESK} + reason")
    summary = ", ".join(f"{v} {n}" for v, n in counts.items())
    return finish("C2", probs, f"verdict paragraph ok; {N_ITEMS} rows valid ({summary})")


# ---------- C3 ----------


def c3() -> Result:
    probs: list[str] = []
    oc = EV / "opencode"
    logs = sorted(oc.rglob("*.log")) if oc.is_dir() else []
    for log in logs:
        wrong = [v for v in VER_LINE.findall(read(log)) if v != OC_VERSION]
        if wrong:
            probs.append(f"{rel(log)} reports opencode-version {wrong[0]!r} (want {OC_VERSION})")
    found: list[str] = []
    for n in PROBE_GROUPS:
        dirs = sorted(d for d in oc.glob(f"P{n}-*") if d.is_dir()) if oc.is_dir() else []
        if not dirs:
            probs.append(f"P{n}: no evidence/opencode/P{n}-*/ directory")
            continue
        good: list[str] = []
        why: list[str] = []
        for d in dirs:
            for log in sorted(d.rglob("*.log")):
                r = rel(log)
                st = repo().state(r)
                text = read(log)
                vers = VER_LINE.findall(text)
                vl = verdict_lines(text)
                if st != "ok":
                    why.append(f"{r} {st}")
                elif not text.strip():
                    why.append(f"{r} empty")
                elif OC_VERSION not in vers or any(v != OC_VERSION for v in vers):
                    why.append(f"{r} lacks `opencode-version: {OC_VERSION}`")
                elif not vl:
                    why.append(f"{r} has no `VERDICT:` line")
                elif any(verdict_token(v) in PLACEHOLDERS for v in vl):
                    why.append(f"{r} has a placeholder VERDICT")
                else:
                    good.append(r)
        if good:
            found.append(f"P{n}:{len(good)}")
        else:
            probs.append(f"P{n}: no qualifying log ({'; '.join(why[:3]) or 'no *.log files'})")
    return finish("C3", probs, "qualifying logs per group " + " ".join(found))


# ---------- C4 ----------

TIMING_TOKENS = {
    "ms", "msec", "millis", "milliseconds", "s", "sec", "secs", "seconds", "us", "usec", "ns",
    "time", "timing", "timings", "latency", "duration", "elapsed", "wall", "p50", "p90", "p95",
    "p99",
}


def key_tokens(path: tuple[str, ...]) -> list[str]:
    toks: list[str] = []
    for k in path:
        if k.startswith("["):
            continue
        k = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", k)
        toks += [t for t in re.split(r"[^A-Za-z0-9]+", k.lower()) if t]
    return toks


def num_leaves(obj: Any, path: tuple[str, ...] = (), into_lists: bool = True
               ) -> Iterator[tuple[tuple[str, ...], float]]:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        yield path, float(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from num_leaves(v, path + (str(k),), into_lists)
    elif isinstance(obj, list) and into_lists:
        for idx, v in enumerate(obj):
            yield from num_leaves(v, path + (f"[{idx}]",), into_lists)


def dicts(obj: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], dict[str, Any]]]:
    if isinstance(obj, dict):
        yield path, obj
        for k, v in obj.items():
            yield from dicts(v, path + (str(k),))
    elif isinstance(obj, list):
        for idx, v in enumerate(obj):
            yield from dicts(v, path + (f"[{idx}]",))


def positive(v: float) -> bool:
    return math.isfinite(v) and v > 0


def neo4j_rows(doc: Any) -> int:
    n = 0
    for path, d in dicts(doc):
        is_neo = any(isinstance(v, str) and v.strip().lower().startswith("neo4j")
                     for v in d.values())
        is_neo = is_neo or any(k.lower().startswith("neo4j") for k in path if not k.startswith("["))
        if not is_neo:
            continue
        if any(positive(v) and TIMING_TOKENS & set(key_tokens(sub))
               for sub, v in num_leaves(d, (), into_lists=False)):
            n += 1
    return n


def c4() -> Result:
    probs: list[str] = []
    mem = EV / "memory"
    bench, cont = mem / "bench-results.json", mem / "neo4j-container.json"
    primary: list[str] = []
    rows = 0
    for p in (bench, cont):
        prob = committed_problem(p)
        if prob:
            primary.append(prob)
    if not primary:
        try:
            bdoc = json.loads(read(bench))
            cdoc = json.loads(read(cont))
        except ValueError as exc:
            primary.append(f"invalid JSON: {exc}")
        else:
            rows = neo4j_rows(bdoc)
            if rows == 0:
                primary.append("bench-results.json has no neo4j row with a positive timing")
            keys = [set(key_tokens(k)) for k, v in num_leaves(cdoc) if positive(v)]
            if not any(("cold" in t and t & {"start", "startup"}) or "coldstart" in t
                       for t in keys):
                primary.append("neo4j-container.json has no positive cold-start number")
            if not any(any("rss" in x for x in t) for t in keys):
                primary.append("neo4j-container.json has no positive RSS number")
    if not primary:
        return finish("C4", [], f"{rows} neo4j bench row(s) with timings; container cold-start "
                                "and RSS present")
    md = RS / "MEMORY-DESIGN.md"
    lines = read(md).splitlines() if md.is_file() else []
    start = next((k for k, ln in enumerate(lines)
                  if re.match(r"^[\W_]*neo4j\s+(?:was\s+)?rejected\b", ln, re.I)), None)
    if start is None:
        probs += primary
        probs.append("and MEMORY-DESIGN.md has no `Neo4j rejected` line (fallback)")
        return finish("C4", probs, "")
    para: list[str] = []
    for ln in lines[start:]:
        if not ln.strip():
            break
        para.append(ln)
    nums = re.findall(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:ms|s|sec|secs|seconds|min|MB|MiB|GB|GiB|"
                      r"KB|KiB|%|x)(?![A-Za-z])", " ".join(para))
    if not nums:
        return finish("C4", ["`Neo4j rejected` paragraph in MEMORY-DESIGN.md carries no number "
                             "with a unit"], "")
    texts = [read(ROOT / t) for t in repo().committed_under(f"{EV_REL}/memory")]
    for num in sorted(set(nums)):
        pat = re.compile(r"(?<![\d.])" + re.escape(num) + r"(?!\d|\.\d)")
        if not any(pat.search(t) for t in texts):
            probs.append(f"Neo4j rejection number {num} not found in committed evidence/memory/")
    return finish("C4", probs, f"Neo4j rejected with {len(set(nums))} number(s), all in "
                               "evidence/memory/")


# ---------- C5 ----------

ACCEPT = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)?(?:\*\*|__)?\s*Acceptance\b", re.I)


def c5() -> Result:
    p = RS / "ROADMAP.md"
    if not p.is_file():
        return finish("C5", ["ROADMAP.md missing"], "")
    lines = read(p).splitlines()
    mask = fence_mask(lines)
    blocks = {b.start: b for b in fenced_blocks(lines)}
    heads = [(k, len(m.group(1))) for k, ln in enumerate(lines)
             if not mask[k] and (m := HEADING.match(ln))
             and re.search(r"(?<![A-Za-z0-9])P0(?![0-9])", m.group(2))]
    if not heads:
        return finish("C5", ["ROADMAP.md has no heading containing P0"], "")
    for k0, level in heads:
        end = len(lines)
        for k in range(k0 + 1, len(lines)):
            m = HEADING.match(lines[k]) if not mask[k] else None
            if m and len(m.group(1)) <= level:
                end = k
                break
        for k in range(k0 + 1, end):
            if mask[k] or not ACCEPT.match(lines[k]):
                continue
            if re.search(r"`[^`]*\S[^`]*`", lines[k]):
                return finish("C5", [], f"P0 acceptance command at ROADMAP.md:{k + 1}")
            n = k + 1
            while n < end and not lines[n].strip():
                n += 1
            b = blocks.get(n)
            if b and any(x.strip() for x in b.lines):
                return finish("C5", [], f"P0 acceptance block at ROADMAP.md:{n + 1}")
    where = ", ".join(f"ROADMAP.md:{k + 1}" for k, _ in heads)
    return finish("C5", [f"no P0 section ({where}) has an `Acceptance` line with a code span or "
                         "a following fenced block"], "")


# ---------- C6 ----------

ITEM = re.compile(r"^(\d+)[.)]\s+\S")
LABEL = re.compile(r"cheapest\s+(?:early\s+)?test\b", re.I)


def test_text_ok(s: str) -> bool:
    return bool(re.search(r"`[^`]*\S[^`]*`", s)) or len(re.findall(r"[A-Za-z0-9]{2,}", s)) >= 3


def c6() -> Result:
    p = RS / "RISKS.md"
    if not p.is_file():
        return finish("C6", ["RISKS.md missing"], "")
    lines = read(p).splitlines()
    mask = fence_mask(lines)
    items = [(k, int(m.group(1))) for k, ln in enumerate(lines)
             if not mask[k] and (m := ITEM.match(ln))]
    if not items:
        return finish("C6", ["RISKS.md has no top-level ordered list (`1. ` at column 0)"], "")
    probs: list[str] = []
    nums = [n for _, n in items]
    if nums != list(range(1, len(nums) + 1)):
        probs.append(f"ranks must run 1..N with no gap or restart (one list only); found {nums}")
    if len(items) < 3:
        probs.append(f"only {len(items)} ranked risk(s) (<3)")
    for idx, (k, n) in enumerate(items):
        end = items[idx + 1][0] if idx + 1 < len(items) else len(lines)
        for j in range(k + 1, end):
            if not mask[j] and HEADING.match(lines[j]):
                end = j
                break
        body = lines[k:end]
        ok = False
        for bi, ln in enumerate(body):
            m = LABEL.search(ln)
            if not m:
                continue
            sep = re.match(r"^[\s*_]*[:\u2014\u2013-][\s*_]*", ln[m.end():])
            if not sep:
                continue
            rest = ln[m.end() + sep.end():]
            if not rest.strip():
                rest = next((x for x in body[bi + 1:] if x.strip()), "")
            if test_text_ok(rest):
                ok = True
                break
        if not ok:
            probs.append(f"risk {n} (RISKS.md:{k + 1}) has no `Cheapest early test:` with a test "
                         "(>=3 words or a code span)")
    return finish("C6", probs, f"{len(items)} ranked risks, each with a cheapest early test")


# ---------- C7 ----------

SHELL_LANGS = {"", "bash", "sh", "zsh", "shell", "console", "shell-session", "sh-session"}
CONSOLE_LANGS = {"console", "shell-session", "sh-session"}
HEREDOC = re.compile(r"(?<!<)<<-?(?!<)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def norm(s: str) -> str:
    s = ANSI.sub("", s)
    s = re.sub(r"\\\r?\n", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def block_commands(b: Block) -> list[str]:
    console = b.lang in CONSOLE_LANGS
    cmds: list[list[str]] = []
    cur: list[str] | None = None
    heredoc: str | None = None
    for raw in b.lines:
        line = raw.rstrip()
        if heredoc is not None and cur is not None:
            cur.append(line)
            if line.strip() == heredoc:
                heredoc = None
                cmds.append(cur)
                cur = None
            continue
        if cur is not None and cur[-1].endswith("\\"):
            cur.append(line)
            part = line
        else:
            if cur is not None:
                cmds.append(cur)
                cur = None
            if console:
                m = re.match(r"^\s*[$%]\s+(.*)$", line)
                if not m:
                    continue
                part = m.group(1)
            else:
                part = line.strip()
                if not part or part.startswith("#"):
                    continue
                part = re.sub(r"^[$%]\s+", "", part)
            cur = [part]
        hd = HEREDOC.search(part)
        if hd:
            heredoc = hd.group(2)
    if cur is not None:
        cmds.append(cur)
    return ["\n".join(c) for c in cmds]


def c7() -> Result:
    p = RS / "START-HERE.md"
    if not p.is_file():
        return finish("C7", ["START-HERE.md missing"], "")
    cmds: list[tuple[int, str]] = []
    for b in fenced_blocks(read(p).splitlines()):
        if b.lang in SHELL_LANGS:
            cmds += [(b.start + 1, c) for c in block_commands(b)]
    if not cmds:
        return finish("C7", ["START-HERE.md has no shell commands in fenced blocks"], "")
    logs = [ROOT / t for t in repo().committed_under(f"{EV_REL}/start-here") if t.endswith(".log")]
    if not logs:
        return finish("C7", ["no committed *.log under evidence/start-here/"], "")
    texts = [norm(read(x)) for x in logs]
    probs: list[str] = []
    for start, c in cmds:
        pat = re.compile(r"(?<!\S)" + re.escape(norm(c)) + r"(?!\S)")
        if not any(pat.search(t) for t in texts):
            short = norm(c)
            probs.append(f"block@START-HERE.md:{start} command not in any start-here log: "
                         f"{short[:100]}")
    return finish("C7", probs, f"{len(cmds)} commands all found in {len(logs)} start-here log(s)")


# ---------- C8 ----------

FORBIDDEN_TRAILERS = ("co-authored-by", "claude-session", "generated with")
EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]")


def c8() -> Result:
    probs: list[str] = []
    try:
        git("rev-parse", "--verify", "-q", "origin/main^{commit}")
    except GitError:
        return finish("C8", ["origin/main does not resolve"], "")
    names = [n for n in git("diff", "--name-only", "--no-renames", "-z",
                            "origin/main...HEAD").split("\0") if n]
    outside = [n for n in names if not n.startswith(RS_REL + "/")]
    if outside:
        probs.append(f"{len(outside)} changed path(s) outside {RS_REL}/: {outside[:5]}")
    count = int(git("rev-list", "--count", "origin/main..HEAD").strip() or 0)
    if count == 0:
        probs.append("no commits on the branch beyond origin/main")
    msgs = git("log", "origin/main..HEAD", "--format=%H%n%B%x00")
    for chunk in msgs.split("\0"):
        if not chunk.strip():
            continue
        sha, _, body = chunk.strip().partition("\n")
        low = body.lower()
        for t in FORBIDDEN_TRAILERS:
            if t in low:
                probs.append(f"commit {sha[:9]} message contains {t!r}")
        if EMOJI.search(body):
            probs.append(f"commit {sha[:9]} message contains emoji")
    try:
        want = f"{git('config', 'user.name').strip()} <{git('config', 'user.email').strip()}>"
    except GitError:
        want = ""
    authors = {a for a in git("log", "origin/main..HEAD", "--format=%an <%ae>").splitlines() if a}
    foreign = sorted(a for a in authors if want and a != want)
    if foreign:
        probs.append(f"branch commits by {foreign} (operator is {want})")
    elif not want and len(authors) > 1:
        probs.append(f"multiple commit authors {sorted(authors)}")
    dirty = []
    for path, code in sorted(repo().status.items()):
        if path.startswith(VERIFY_REL + "/"):
            continue
        if code == "!!":
            if path.startswith(EV_REL + "/"):
                dirty.append(f"{path} (gitignored, not committed)")
            continue
        dirty.append(f"{path} ({code.strip()})")
    if dirty:
        probs.append(f"{len(dirty)} uncommitted path(s): {', '.join(dirty[:6])}")
    return finish("C8", probs, f"{count} commit(s), {len(names)} path(s) all under {RS_REL}/, "
                               "clean trailers, single author, clean tree")


# ---------- C9 ----------

UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"
REAL_DIRS = (
    ("~/.config/opencode", r"\.config/opencode"),
    ("~/.local/share/opencode", r"\.local/share/opencode"),
    ("~/.opencode", r"(?:~|/)\.opencode(?![\w-])"),
    ("~/.claude", r"(?:~|/)\.claude(?![\w-])"),
)


def utc_field(text: str, key: str) -> dt.datetime | None:
    m = re.search(rf"^{key}:[ \t]*(\S+)", text, re.M)
    if not m:
        return None
    try:
        return dt.datetime.strptime(m.group(1), UTC_FMT)
    except ValueError:
        return None


def c9() -> Result:
    iso = EV / "opencode" / "isolation-check.log"
    prob = committed_problem(iso)
    if prob:
        return finish("C9", [prob], "")
    text = read(iso)
    probs: list[str] = []
    if not text.strip():
        return finish("C9", ["isolation-check.log is empty"], "")
    for name, pat in REAL_DIRS:
        if not re.search(pat, text):
            probs.append(f"does not cover {name}")
    toks = [verdict_token(v) for v in verdict_lines(text)]
    if not toks:
        probs.append("no `VERDICT:` line")
    elif any(t not in ("PASS", "UNCHANGED") for t in toks):
        probs.append(f"VERDICT tokens {toks} (want PASS/UNCHANGED only)")
    changed = [ln.strip() for ln in text.splitlines() if re.search(r"(?<![A-Za-z])CHANGED\b", ln)]
    if changed:
        probs.append(f"reports a change: {changed[0][:100]!r}")
    snap, done = utc_field(text, "snapshot-utc"), utc_field(text, "date-utc")
    if snap is None or done is None:
        probs.append(f"must record `snapshot-utc:` and `date-utc:` as {UTC_FMT}")
    else:
        n = 0
        for t in repo().committed_under(EV_REL):
            if t.startswith(VERIFY_REL + "/") or t == rel(iso) or not t.endswith(".log"):
                continue
            body = read(ROOT / t)
            if not VER_LINE.search(body):
                continue
            n += 1
            when = utc_field(body, "date-utc")
            if when is None:
                probs.append(f"{t} ran opencode but has no `date-utc:` line")
            elif not snap <= when <= done:
                probs.append(f"{t} ran at {when:%Y-%m-%dT%H:%M:%SZ}, outside the isolation "
                             f"window [{snap:%Y-%m-%dT%H:%M:%SZ}, {done:%Y-%m-%dT%H:%M:%SZ}]")
        if not probs:
            return finish("C9", [], f"real dirs unchanged; {n} opencode log(s) inside the window")
    return finish("C9", probs, "")


# ---------- C10 ----------


def rerun_entry(e: dict[str, Any], tree: Path, base: Path, out_dir: Path) -> tuple[str, str | None]:
    eid = str(e["id"])
    sb = base / eid
    sb.mkdir(parents=True)
    subst = {"tree": str(tree), "base": str(sb)}
    try:
        committed = git("show", f"HEAD:{e['committed']}")
    except GitError:
        return f"{eid} no committed log", f"{eid}: committed log {e['committed']} not in HEAD"
    produced = tree / str(e.get("produces", e["committed"]))
    if produced.exists():
        produced.unlink()
    env = dict(os.environ)
    env.update({"OCSOMA_REAL_TMPDIR": str(sb), "TMPDIR": str(sb) + "/", "HOME": str(sb / "home")})
    for x in ("CONFIG", "DATA", "CACHE", "STATE"):
        env[f"XDG_{x}_HOME"] = str(sb / "xdg" / x.lower())
    for d in ("home", "xdg/config", "xdg/data", "xdg/cache", "xdg/state"):
        (sb / d).mkdir(parents=True, exist_ok=True)
    for k in list(env):
        if SECRET_ENV.search(k):
            del env[k]
    env.update({k: str(v).format(**subst) for k, v in e.get("env", {}).items()})
    argv = [str(a).format(**subst) for a in e["argv"]]
    t0 = time.time()
    try:
        proc = subprocess.run(argv, cwd=tree, env=env, capture_output=True, text=True,
                              timeout=int(e.get("timeout", 1800)))
        rc: object = proc.returncode
        out, err = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        rc = "timeout"
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        err = exc.stderr if isinstance(exc.stderr, str) else ""
    except OSError as exc:
        rc, out, err = "oserror", "", str(exc)
    wall = time.time() - t0
    new_text = read(produced) if produced.is_file() else ""
    old_v = [verdict_token(v) for v in verdict_lines(committed)]
    new_v = [verdict_token(v) for v in verdict_lines(new_text)]
    vers = VER_LINE.findall(new_text)
    want_ver = bool(VER_LINE.search(committed))
    stamp = dt.datetime.now(dt.UTC).strftime(UTC_FMT)
    transcript = "\n".join([
        f"# rerun {eid}", f"date-utc: {stamp}", f"argv: {argv}", f"cwd: {tree}",
        f"sandbox-base: {sb}", f"committed-log: {e['committed']}", f"exit: {rc}",
        f"wall-s: {wall:.1f}", f"committed-verdicts: {old_v}", f"rerun-verdicts: {new_v}",
        "---- stdout ----", out, "---- stderr ----", err, "---- produced log ----", new_text,
    ])
    (out_dir / f"{eid}.log").write_text(transcript, encoding="utf-8")
    line = f"{eid} committed={old_v} rerun={new_v} exit={rc} wall={wall:.0f}s"
    if not old_v:
        return line, f"{eid}: committed log has no VERDICT line"
    if not new_v:
        return line, f"{eid}: rerun produced no VERDICT (exit {rc})"
    if want_ver and (OC_VERSION not in vers or any(v != OC_VERSION for v in vers)):
        return line, f"{eid}: rerun log lacks opencode-version {OC_VERSION}"
    if old_v != new_v:
        return line, f"{eid}: VERDICT mismatch committed={old_v} rerun={new_v}"
    return line + " MATCH", None


def c10(run: bool) -> Result:
    if not run:
        return Result("C10", "SKIP", "re-runs only with --rerun (the final gate uses --rerun)")
    if not RERUN_LIST:
        return Result("C10", "FAIL", "rerun list not yet pinned")
    tags = {t for e in RERUN_LIST for t in e.get("tags", ())}
    missing = [t for t in RERUN_REQUIRED_TAGS if t not in tags]
    if missing:
        return Result("C10", "FAIL", f"rerun list not yet pinned: no entry tagged {missing}")
    probs: list[str] = []
    for e in RERUN_LIST:
        for a in e["argv"]:
            if str(a).startswith("{tree}/"):
                srel = str(a)[len("{tree}/"):]
                if repo().state(srel) != "ok":
                    probs.append(f"{e['id']}: script {srel} {repo().state(srel)}")
    if probs:
        return finish("C10", probs, "")
    real_tmp = os.environ.get("TMPDIR", "/tmp").rstrip("/")
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    base = Path(real_tmp) / "ocsoma-vf" / f"rerun-{stamp}-{os.getpid()}"
    tree = base / "tree"
    tree.mkdir(parents=True)
    try:
        arc = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", "HEAD"],
                             capture_output=True, timeout=600, check=True).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return finish("C10", [f"git archive HEAD failed: {exc}"], "")
    with tarfile.open(fileobj=io.BytesIO(arc)) as tf:
        tf.extractall(tree, filter="data")
    out_dir = ROOT / VERIFY_REL / "rerun"
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"rerun base: {base}", f"HEAD: {git('rev-parse', 'HEAD').strip()}"]
    for e in RERUN_LIST:
        line, prob = rerun_entry(e, tree, base, out_dir)
        lines.append(line)
        if prob:
            probs.append(prob)
    lines.append("RESULT: " + ("FAIL" if probs else "PASS"))
    (out_dir / "SUMMARY.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return finish("C10", probs, f"{len(RERUN_LIST)} probe(s) re-run, all VERDICTs match "
                                "(evidence/verify/rerun/)")


# ---------- C11 ----------

SECRET_PATTERNS = [
    ("Anthropic key", r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    ("OpenAI-style key", r"(?<![\w-])sk-(?:proj-|svcacct-)?[A-Za-z0-9_\-]{32,}"),
    ("GitHub token", r"(?<![\w-])gh[pousr]_[A-Za-z0-9]{30,}"),
    ("GitHub PAT", r"github_pat_[A-Za-z0-9_]{40,}"),
    ("Slack token", r"(?<![\w-])xox[abprs]-[A-Za-z0-9-]{10,}"),
    ("AWS key id", r"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])"),
    ("Google API key", r"(?<![\w-])AIza[0-9A-Za-z_\-]{35}(?![\w-])"),
    ("Telegram bot token", r"(?<![\w:])\d{8,10}:[A-Za-z0-9_\-]{35}(?![\w-])"),
    ("private key", "-" * 5 + r"BEGIN [A-Z ]*PRIVATE KEY" + "-" * 5),
    ("JWT", r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    ("bearer token", r"(?i)\bbearer\s+[A-Za-z0-9._~+/\-]{24,}"),
    ("OAuth JSON token", r'"(?:access|refresh|access_token|refresh_token)"\s*:\s*'
                         r'"[A-Za-z0-9._\-]{24,}"'),
    ("secret env assignment", r"\b(?:CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY|OPENAI_API_KEY|"
                              r"AUTH_GITHUB_SECRET|AUTH_SECRET|TELEGRAM_BOT_TOKEN|GITHUB_TOKEN|"
                              r"GH_TOKEN)\s*[=:]\s*['\"]?[A-Za-z0-9._\-]{16,}"),
]


def c11() -> Result:
    pats = [(name, re.compile(rx)) for name, rx in SECRET_PATTERNS]
    probs: list[str] = []
    nfiles = 0
    for dirpath, dirnames, filenames in os.walk(RS):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            fp = Path(dirpath) / fn
            if fp.is_symlink() or not fp.is_file():
                continue
            nfiles += 1
            text = fp.read_bytes().decode("latin-1")
            for name, rx in pats:
                m = rx.search(text)
                if m:
                    lineno = text.count("\n", 0, m.start()) + 1
                    probs.append(f"{rel(fp)}:{lineno} looks like a {name}")
    return finish("C11", probs, f"{nfiles} files scanned, no credential-shaped strings")


# ---------- main ----------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="opencode-soma Definition-of-Done gate")
    ap.add_argument("--rerun", action="store_true", help="run C10 (re-run pinned probes)")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every problem")
    args = ap.parse_args(argv)
    checks: list[tuple[str, Callable[[], Result]]] = [
        ("C1", c1), ("C2", c2), ("C3", c3), ("C4", c4), ("C5", c5), ("C6", c6), ("C7", c7),
        ("C8", c8), ("C9", c9), ("C10", lambda: c10(args.rerun)), ("C11", c11),
    ]
    failed = False
    for cid, fn in checks:
        try:
            res = fn()
        except Exception as exc:  # any crash is a FAIL, never a pass
            res = Result(cid, "FAIL", f"internal error: {type(exc).__name__}: {exc}")
        failed = failed or res.status == "FAIL"
        print(f"{res.status} {res.cid}: {res.summary}", flush=True)
        if args.verbose and res.status == "FAIL":
            for d in res.details:
                print(f"    - {d}")
    print("GATE: FAIL" if failed else "GATE: PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
