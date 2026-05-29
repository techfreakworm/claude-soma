"""Convert GitHub-flavored markdown to Telegram-safe HTML.

Stdlib only: html, re. No third-party markdown parsers.

Conversion order (matters — fenced code extracted first to prevent
re-processing its contents):
  1. Extract fenced code blocks → placeholder tokens
  2. Extract inline code spans → placeholder tokens
  3. html.escape() the residual text (placeholders use \\x00 — safe)
  4. Tables (|a|b|\\n|-|-|\\n|1|2|) → <pre>-wrapped column-aligned monospace
  5. Headers (^#{1,6}\\s+) → <b>title</b>
  6. List bullets (^[*-+]\\s+) → • prefix  [BEFORE bold to consume leading *]
  7. Bold (**x** / __x__) → <b>x</b>
  8. Italic (*x* / _x_) → <i>x</i>  [careful with _ inside words]
  9. Links ([text](url)) → <a href="url">text</a>
 10. Re-inject code as <pre><code>...</code></pre> / <code>...</code>
     (html.escape applied to code contents here)
"""
from __future__ import annotations

import html
import re


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _convert_tables(text: str) -> str:
    """Replace GFM tables with <pre>-wrapped column-aligned monospace."""
    lines = text.split('\n')
    result: list[str] = []
    i = 0
    while i < len(lines):
        # Look ahead: current line is header, next is separator, then data rows
        if (
            i + 1 < len(lines)
            and re.match(r'^\s*\|', lines[i])
            and re.match(r'^\s*\|[\s\-|:]+\|\s*$', lines[i + 1])
        ):
            # Collect table rows
            table_lines: list[str] = []
            j = i
            while j < len(lines) and re.match(r'^\s*\|', lines[j]):
                table_lines.append(lines[j])
                j += 1
            # Skip separator row (index 1 in table_lines)
            data_rows: list[list[str]] = []
            for k, tl in enumerate(table_lines):
                if k == 1:
                    continue
                cells = [c.strip() for c in tl.strip().strip('|').split('|')]
                data_rows.append(cells)
            if data_rows:
                n_cols = max(len(r) for r in data_rows)
                col_widths = [0] * n_cols
                for row in data_rows:
                    for c_idx in range(min(len(row), n_cols)):
                        col_widths[c_idx] = max(col_widths[c_idx], len(row[c_idx]))
                pre_lines: list[str] = []
                for row in data_rows:
                    padded = []
                    for c_idx in range(n_cols):
                        cell = row[c_idx] if c_idx < len(row) else ''
                        padded.append(cell.ljust(col_widths[c_idx]))
                    pre_lines.append('  '.join(padded).rstrip())
                result.append('<pre>' + '\n'.join(pre_lines) + '</pre>')
                i = j
                continue
        result.append(lines[i])
        i += 1
    return '\n'.join(result)


def _get_open_tags(text: str) -> list[tuple[str, str]]:
    """Return list of (tagname, full_open_tag_string) for unclosed HTML tags."""
    VOID_TAGS = {'br', 'hr', 'img', 'input', 'meta', 'link', 'area', 'base', 'col'}
    stack: list[tuple[str, str]] = []
    for m in re.finditer(r'<(/?)([\w]+)([^>]*)>', text):
        slash, tag, attrs = m.group(1), m.group(2).lower(), m.group(3)
        if slash:
            for idx in range(len(stack) - 1, -1, -1):
                if stack[idx][0] == tag:
                    stack.pop(idx)
                    break
        elif tag not in VOID_TAGS:
            stack.append((tag, f'<{tag}{attrs}>'))
    return stack


def _find_split_pos(text: str, effective_limit: int) -> int:
    """Find the best position to split `text` at or before `effective_limit`.

    Priority: paragraph boundary (\\n\\n), line boundary (\\n),
    last close-tag that follows content (>), force-split.

    Guarantees progress: always returns a position > 0 and advances past at
    least one non-tag character so the chunker loop cannot stall.
    """
    total = len(text)
    cap = min(effective_limit, total)

    # Ensure we're not splitting inside a tag <...>
    substr = text[:cap]
    last_open = substr.rfind('<')
    last_close = substr.rfind('>')
    if last_open > last_close:
        # Would split inside a tag — back up to just before the <
        cap = last_open
    if cap <= 0:
        # Pathological: the very first char opens a tag longer than effective_limit.
        # Find the closing > and split right after it, even if that exceeds
        # effective_limit — better one slightly-oversized chunk than infinite loop.
        gt = text.find('>', 0)
        if gt >= 0:
            return gt + 1
        return min(effective_limit, total)

    # Prefer paragraph boundary
    pp = text.rfind('\n\n', 0, cap)
    if pp > 0:
        return pp + 2

    # Fall back to line boundary
    nl = text.rfind('\n', 0, cap)
    if nl > 0:
        return nl + 1

    # Fall back to last close-tag that is followed by (or follows) actual content.
    # To avoid returning a position that splits right after an open tag with no
    # body yet (e.g. "<b>" with cap=3 → gt=2 → pos=3 → no progress after
    # reopen-tag injection), find the rightmost > that has plain text between it
    # and the previous >.
    gt = text.rfind('>', 0, cap)
    if gt > 0:
        # Find the tag that ends at gt
        tag_start = text.rfind('<', 0, gt)
        # If there is plain text between the previous close-tag and this tag,
        # or this is a closing tag (starts with </), it's a safe split point.
        prev_close = text.rfind('>', 0, tag_start) if tag_start > 0 else -1
        between = text[prev_close + 1:tag_start] if prev_close >= 0 else text[:tag_start]
        is_closing = tag_start >= 0 and tag_start + 1 < len(text) and text[tag_start + 1] == '/'
        if between.strip() or is_closing:
            return gt + 1

    # Force split at cap — may land in the middle of text but never inside <...>
    return max(1, cap)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def gfm_to_html(text: str) -> str:
    """Convert GitHub-flavored markdown to Telegram-safe HTML.

    Supported: **bold**, *italic*, `code`, fenced code blocks, [text](url),
    tables (→ <pre>), headers (→ <b>), list bullets, HTML escaping.
    Stdlib only.
    """
    if not text:
        return text

    # Track extracted code regions: list of ('fenced'|'inline', content, lang)
    code_regions: list[tuple[str, str, str]] = []

    def _save_fenced(m: re.Match[str]) -> str:
        lang = (m.group(1) or '').strip()
        content = m.group(2)
        idx = len(code_regions)
        code_regions.append(('fenced', content, lang))
        return f'\x00FENCED_{idx}\x00'

    def _save_inline(m: re.Match[str]) -> str:
        content = m.group(1)
        idx = len(code_regions)
        code_regions.append(('inline', content, ''))
        return f'\x00INLINE_{idx}\x00'

    # Step 1: Extract fenced code blocks (``` ... ```)
    text = re.sub(r'```([^\n]*)\n(.*?)```', _save_fenced, text, flags=re.DOTALL)

    # Step 2: Extract inline code spans (`...`)
    text = re.sub(r'`([^`\n]+)`', _save_inline, text)

    # Step 3: Escape HTML entities in residual text.
    # Placeholder bytes (\x00...\x00) are not in the HTML entity set — safe.
    text = html.escape(text, quote=False)

    # Step 4: Tables
    text = _convert_tables(text)

    # Step 5: Headers (# through ######)
    text = re.sub(r'^#{1,6}\s+(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)

    # Step 6: List bullets — BEFORE bold so `* item` doesn't match italic
    text = re.sub(r'^[*\-+]\s+(.+)$', r'• \1', text, flags=re.MULTILINE)

    # Step 7: Bold (**x** and __x__)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text, flags=re.DOTALL)

    # Step 8: Italic (*x* and _x_)
    # For *, use non-greedy and avoid matching ** (already consumed as bold markers)
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
    # For _, require non-word boundary on at least one side to avoid foo_bar_baz
    text = re.sub(r'(?<!\w)_(.+?)_(?!\w)', r'<i>\1</i>', text)

    # Step 9: Links ([text](url))
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)

    # Step 10: Re-inject code regions (with html.escape applied to their contents)
    for idx, (kind, content, _lang) in enumerate(code_regions):
        escaped = html.escape(content, quote=False)
        if kind == 'fenced':
            html_str = f'<pre><code>{escaped}</code></pre>'
            text = text.replace(f'\x00FENCED_{idx}\x00', html_str)
        else:
            html_str = f'<code>{escaped}</code>'
            text = text.replace(f'\x00INLINE_{idx}\x00', html_str)

    return text


def chunk_html_for_telegram(html_text: str, limit: int = 4096) -> list[str]:
    """Split HTML into chunks ≤ limit chars without breaking tags.

    Never splits inside <...>. Tracks open tags and closes them at the
    chunk boundary, re-opening them at the start of the next chunk.

    Split priority: paragraph boundary (\\n\\n) → line (\\n) →
    last close-tag (>) → force-split.

    Leaves ~64 chars of headroom below `limit` for close/reopen tag overhead.
    """
    if len(html_text) <= limit:
        return [html_text]

    HEADROOM = 64
    effective_limit = max(1, limit - HEADROOM)

    chunks: list[str] = []
    remaining = html_text

    while len(remaining) > limit:
        pos = _find_split_pos(remaining, effective_limit)
        if pos <= 0:
            pos = max(1, effective_limit)

        part = remaining[:pos]
        remaining = remaining[pos:]

        open_tags = _get_open_tags(part)
        close_str = ''.join(f'</{t[0]}>' for t in reversed(open_tags))
        reopen_str = ''.join(t[1] for t in open_tags)

        chunks.append(part + close_str)
        remaining = reopen_str + remaining

    chunks.append(remaining)
    return chunks
