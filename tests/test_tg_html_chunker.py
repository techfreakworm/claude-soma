"""Tests for tg_html.chunk_html_for_telegram — HTML-aware chunker."""
from __future__ import annotations

import pytest

from claude_soma.mcp_servers.hermes_api.tg_html import chunk_html_for_telegram


# ---------------------------------------------------------------------------
# Trivial cases
# ---------------------------------------------------------------------------

def test_short_input_returns_single_chunk() -> None:
    text = "hello world"
    chunks = chunk_html_for_telegram(text)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_input_exactly_at_limit_returns_single_chunk() -> None:
    text = "x" * 4096
    chunks = chunk_html_for_telegram(text, limit=4096)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_empty_input() -> None:
    chunks = chunk_html_for_telegram("")
    assert chunks == [""]


# ---------------------------------------------------------------------------
# Single-byte-over limit — paragraph split
# ---------------------------------------------------------------------------

def test_4097_chars_splits_at_paragraph_boundary() -> None:
    # Build two paragraphs that together exceed 4096 by 1
    para1 = "a" * 2000
    para2 = "b" * (4096 - 2000 - 2 + 1)  # 2095 chars; total = 2000+2+2095 = 4097
    src = para1 + "\n\n" + para2
    assert len(src) == 4097

    chunks = chunk_html_for_telegram(src, limit=4096)
    assert len(chunks) == 2
    # First chunk should end with para1 (plus possibly the \n\n)
    assert "a" * 2000 in chunks[0]
    assert "b" * (4096 - 2000 - 2 + 1) in chunks[1]


# ---------------------------------------------------------------------------
# Multi-paragraph — split near 4096 on paragraph boundary
# ---------------------------------------------------------------------------

def test_multi_paragraph_splits_at_paragraph_boundary() -> None:
    # Three paragraphs, each ~1500 chars, separated by \n\n — total ~4506
    para = "p" * 1500
    src = para + "\n\n" + para + "\n\n" + para
    assert len(src) > 4096

    chunks = chunk_html_for_telegram(src, limit=4096)
    # All chunks must be within the limit
    for chunk in chunks:
        assert len(chunk) <= 4096
    # Reassembled content (ignoring open/close tag overhead) still has all p's
    combined = "".join(chunks)
    assert combined.count("p") == 4500


# ---------------------------------------------------------------------------
# Line-level split when no paragraph breaks
# ---------------------------------------------------------------------------

def test_no_paragraph_breaks_splits_on_newline() -> None:
    # One long block with only \n separating lines — total > 4096
    line = "x" * 200 + "\n"
    src = line * 25  # 201 * 25 = 5025 chars
    assert len(src) > 4096

    chunks = chunk_html_for_telegram(src, limit=4096)
    for chunk in chunks:
        assert len(chunk) <= 4096
    # No chunk should end in the middle of a line (all split at \n)
    for chunk in chunks[:-1]:
        assert chunk.endswith("\n") or chunk.endswith("\n")


# ---------------------------------------------------------------------------
# Force-split on a single very long line (no breaks at all)
# ---------------------------------------------------------------------------

def test_no_breaks_force_splits() -> None:
    src = "z" * 5000
    chunks = chunk_html_for_telegram(src, limit=4096)
    for chunk in chunks:
        assert len(chunk) <= 4096
    assert "".join(chunks) == src


# ---------------------------------------------------------------------------
# Open tag at chunk boundary — tags must be closed/reopened
# ---------------------------------------------------------------------------

def test_open_bold_tag_closed_and_reopened() -> None:
    # Build a string where <b> is opened, then a lot of content pushes us over 4096
    # before </b>, so the chunker must close </b> at the boundary and reopen <b>.
    opening = "<b>"
    closing = "</b>"
    # Content inside the tag: 4097 chars so chunker must split mid-tag
    inner = "x" * 4097
    src = opening + inner + closing

    chunks = chunk_html_for_telegram(src, limit=4096)
    assert len(chunks) >= 2
    # First chunk must end with </b>
    assert chunks[0].endswith("</b>")
    # Second chunk must start with <b>
    assert chunks[1].startswith("<b>")
    # All chunks within limit
    for chunk in chunks:
        assert len(chunk) <= 4096


def test_open_code_tag_closed_and_reopened() -> None:
    inner = "y" * 4200
    src = "<code>" + inner + "</code>"
    chunks = chunk_html_for_telegram(src, limit=4096)
    for chunk in chunks:
        assert len(chunk) <= 4096
    assert chunks[0].endswith("</code>")
    assert chunks[1].startswith("<code>")


# ---------------------------------------------------------------------------
# Combined: tags + HTML escape chars + multi-paragraph
# ---------------------------------------------------------------------------

def test_combined_tags_escape_and_paragraphs() -> None:
    # Build a message with bold, escaped chars, and multiple paragraphs
    para_a = "<b>" + "a" * 1000 + "</b>"
    para_b = "safe &amp; text " * 100  # 1600 chars
    para_c = "<i>" + "c" * 1500 + "</i>"
    src = para_a + "\n\n" + para_b + "\n\n" + para_c

    chunks = chunk_html_for_telegram(src, limit=4096)
    for chunk in chunks:
        assert len(chunk) <= 4096
    # No chunk should contain unmatched open tags (naive check: count opens vs closes)
    for chunk in chunks:
        for tag in ('b', 'i', 'code', 'pre'):
            opens = chunk.count(f'<{tag}>') + chunk.count(f'<{tag} ')
            closes = chunk.count(f'</{tag}>')
            assert opens == closes, (
                f"Unmatched <{tag}> in chunk of len {len(chunk)}: "
                f"opens={opens} closes={closes}"
            )
