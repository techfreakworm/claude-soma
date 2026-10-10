"""Deterministic secret scrubber for the memory write path (runs before any model sees text, and
again on model output before anything is stored). Pattern list = credential formats + context
phrases + a high-entropy fallback. Returns the scrubbed text and the number of redactions.

    from secret_scrub import scrub
    clean, n = scrub("The NAS admin password is hunter2, IP 10.0.4.23")
"""
from __future__ import annotations

import math
import re

MARK = "[REDACTED]"
PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"),
    re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])"),
    re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b"),
]
URL_CRED = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^/\s:@]+:)([^@\s/]+)(@)", re.I)
PHRASE = re.compile(r"(?i)\b(pass(?:word|code|phrase)?|pwd|secret|api[ _-]?key|token)\b([^\n.]{0,40}?\b(?:is|:|=)\s*)(\S+)")
OTP = re.compile(r"(?i)\b(one[- ]time|otp|verification|login|security|2fa|mfa)\b([^\n]{0,30}?)\b(\d{4,8})\b")
WORD = re.compile(r"[A-Za-z0-9_\-+/=]{24,}")


def _entropy(s: str) -> float:
    counts = {c: s.count(c) for c in set(s)}
    return -sum(n / len(s) * math.log2(n / len(s)) for n in counts.values())


def scrub(text: str) -> tuple[str, int]:
    n = 0
    for p in PATTERNS:
        text, k = p.subn(MARK, text)
        n += k
    text, k = URL_CRED.subn(lambda m: m.group(1) + MARK + m.group(3), text)
    n += k
    text, k = PHRASE.subn(lambda m: m.group(1) + m.group(2) + (MARK if m.group(3) != MARK else MARK), text)
    n += k
    text, k = OTP.subn(lambda m: m.group(1) + m.group(2) + MARK, text)
    n += k

    def hi(m: re.Match) -> str:
        w = m.group(0)
        if any(c.isdigit() for c in w) and any(c.isalpha() for c in w) and _entropy(w) > 3.8:
            return MARK
        return w
    out = WORD.sub(hi, text)
    n += out.count(MARK) - text.count(MARK)
    return out, n
