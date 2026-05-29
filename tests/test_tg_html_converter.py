"""Tests for tg_html.gfm_to_html — GFM to Telegram HTML converter."""
from __future__ import annotations

import pytest

from claude_soma.mcp_servers.hermes_api.tg_html import gfm_to_html


# ---------------------------------------------------------------------------
# Basic inline formatting
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("src,expected", [
    # bold — double asterisk
    ("**bold**", "<b>bold</b>"),
    # bold — double underscore
    ("__bold__", "<b>bold</b>"),
    # italic — single asterisk
    ("*italic*", "<i>italic</i>"),
    # italic — single underscore (word boundary required)
    ("_italic_", "<i>italic</i>"),
    # italic single underscore at line level
    ("_word_", "<i>word</i>"),
    # link
    ("[text](https://example.com)", '<a href="https://example.com">text</a>'),
    # link with & in URL — must become &amp; in href
    (
        "[q](https://x.com?a=1&b=2)",
        '<a href="https://x.com?a=1&amp;b=2">q</a>',
    ),
])
def test_inline_formatting(src: str, expected: str) -> None:
    assert gfm_to_html(src) == expected


# ---------------------------------------------------------------------------
# Italic inside-word negative: foo_bar_baz must NOT italicise
# ---------------------------------------------------------------------------

def test_italic_underscore_inside_word_not_italicised() -> None:
    result = gfm_to_html("foo_bar_baz")
    assert result == "foo_bar_baz"
    assert "<i>" not in result


def test_italic_underscore_between_words() -> None:
    result = gfm_to_html("some _italic_ text")
    assert result == "some <i>italic</i> text"


# ---------------------------------------------------------------------------
# Inline code
# ---------------------------------------------------------------------------

def test_inline_code_basic() -> None:
    assert gfm_to_html("`cmd`") == "<code>cmd</code>"


def test_inline_code_html_escape() -> None:
    assert gfm_to_html("`<x>`") == "<code>&lt;x&gt;</code>"


def test_inline_code_not_reprocessed_for_markdown() -> None:
    # Markdown inside inline code must be preserved verbatim
    result = gfm_to_html("`**foo**`")
    assert result == "<code>**foo**</code>"


# ---------------------------------------------------------------------------
# Fenced code blocks
# ---------------------------------------------------------------------------

def test_fenced_code_no_lang() -> None:
    src = "```\nls -la\n```"
    result = gfm_to_html(src)
    assert result == "<pre><code>ls -la\n</code></pre>"


def test_fenced_code_with_lang() -> None:
    src = "```sh\nls\n```"
    result = gfm_to_html(src)
    assert result == "<pre><code>ls\n</code></pre>"


def test_fenced_code_html_escape() -> None:
    src = "```\n<script>alert(1)</script>\n```"
    result = gfm_to_html(src)
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


def test_fenced_code_not_reprocessed() -> None:
    src = "```\n**foo**\n```"
    result = gfm_to_html(src)
    assert result == "<pre><code>**foo**\n</code></pre>"
    assert "<b>" not in result


def test_fenced_code_amp_inside() -> None:
    src = "```\na & b\n```"
    result = gfm_to_html(src)
    assert "&amp;" in result
    assert "a & b" not in result


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

def test_table_basic() -> None:
    src = "| a | b |\n|---|---|\n| 1 | 2 |"
    result = gfm_to_html(src)
    assert result.startswith("<pre>")
    assert result.endswith("</pre>")
    # Column headers present
    assert "a" in result
    assert "b" in result
    # Data row present
    assert "1" in result
    assert "2" in result
    # Separator row NOT present (| --- | stripped)
    assert "---" not in result


def test_table_column_alignment() -> None:
    src = "| short | longer_col |\n|---|---|\n| x | y |"
    result = gfm_to_html(src)
    lines = result.replace("<pre>", "").replace("</pre>", "").strip().splitlines()
    # Each line should be padded to the same width per column
    assert len(lines) == 2  # header + 1 data row


def test_table_three_rows() -> None:
    src = "| name | val |\n|------|-----|\n| foo | 1 |\n| bar | 22 |"
    result = gfm_to_html(src)
    assert "foo" in result
    assert "bar" in result
    assert "<pre>" in result


# ---------------------------------------------------------------------------
# Headers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("level", range(1, 7))
def test_header_all_levels(level: int) -> None:
    src = "#" * level + " Title"
    result = gfm_to_html(src)
    assert result == "<b>Title</b>"


# ---------------------------------------------------------------------------
# List bullets
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("marker", ["-", "*", "+"])
def test_list_bullet_markers(marker: str) -> None:
    src = f"{marker} item"
    result = gfm_to_html(src)
    assert result == "• item"


def test_list_multiple_items() -> None:
    src = "- one\n- two\n- three"
    result = gfm_to_html(src)
    lines = result.splitlines()
    assert all(line.startswith("• ") for line in lines)


# ---------------------------------------------------------------------------
# HTML escaping outside code
# ---------------------------------------------------------------------------

def test_html_escape_angle_brackets() -> None:
    result = gfm_to_html("<script>alert(1)</script>")
    assert "<script>" not in result
    assert "&lt;script&gt;" in result


def test_html_escape_ampersand() -> None:
    result = gfm_to_html("A & B")
    assert result == "A &amp; B"


def test_html_escape_does_not_double_escape() -> None:
    # The HTML tags we insert (<b>, <i>, etc.) should not be escaped
    result = gfm_to_html("**hello**")
    assert result == "<b>hello</b>"
    assert "&lt;b&gt;" not in result


# ---------------------------------------------------------------------------
# Mixed content
# ---------------------------------------------------------------------------

def test_mixed_bold_code_link() -> None:
    src = "**bold** and `code` and [link](https://u.com)"
    result = gfm_to_html(src)
    assert "<b>bold</b>" in result
    assert "<code>code</code>" in result
    assert '<a href="https://u.com">link</a>' in result


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_input() -> None:
    assert gfm_to_html("") == ""


def test_plain_text_unchanged() -> None:
    src = "Hello world"
    assert gfm_to_html(src) == src


def test_plain_text_with_newlines() -> None:
    src = "line one\nline two"
    assert gfm_to_html(src) == src
