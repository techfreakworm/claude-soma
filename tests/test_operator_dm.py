"""Tests for src/claude_soma/operator_dm.py — the central dual-route DM helper.

The autouse conftest fixture sets SOMA_DISCORD_DM_DISABLED=1 for every test; the
Discord-path tests delenv it explicitly and mock urllib so nothing hits the live
Discord API.
"""
from __future__ import annotations

from claude_soma import operator_dm


class _FakeResp:
    def __init__(self, status: int = 200, body: bytes = b'{"id": "999"}') -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *_a) -> bool:
        return False


def test_html_to_discord_translates_tags() -> None:
    out = operator_dm._html_to_discord(
        '<b>bold</b> <i>it</i> <code>c</code> '
        '<a href="https://x.io">link</a> &amp; &lt;ok&gt;'
    )
    assert "**bold**" in out
    assert "*it*" in out
    assert "`c`" in out
    assert "link (https://x.io)" in out
    assert "&amp;" not in out  # entity unescaped
    assert "<ok>" in out       # &lt;ok&gt; unescaped


def test_chunk_splits_long_text() -> None:
    chunks = operator_dm._chunk("a" * 5000, 2000)
    assert all(len(c) <= 2000 for c in chunks)
    assert "".join(chunks) == "a" * 5000


def test_load_discord_token_prefers_env(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "env-token")
    assert operator_dm._load_discord_token() == "env-token"


def test_discord_disabled_uses_telegram_fallback() -> None:
    # autouse fixture sets SOMA_DISCORD_DM_DISABLED=1
    calls: list[bool] = []

    def tg() -> int:
        calls.append(True)
        return 123

    assert operator_dm.send_operator_dm("hi", telegram_fallback=tg, is_html=False) == 123
    assert calls == [True]


def test_discord_success_skips_fallback(monkeypatch) -> None:
    monkeypatch.delenv("SOMA_DISCORD_DM_DISABLED", raising=False)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    monkeypatch.setattr(
        operator_dm.urllib.request, "urlopen",
        lambda *a, **k: _FakeResp(200, b'{"id":"42"}'),
    )
    fb: list[int] = []
    mid = operator_dm.send_operator_dm(
        "hi", telegram_fallback=lambda: (fb.append(1) or 7), is_html=False
    )
    assert mid == 42
    assert fb == []  # Discord won; fallback not used


def test_discord_failure_falls_back(monkeypatch) -> None:
    monkeypatch.delenv("SOMA_DISCORD_DM_DISABLED", raising=False)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")

    def boom(*_a, **_k):
        raise OSError("net down")

    monkeypatch.setattr(operator_dm.urllib.request, "urlopen", boom)
    assert operator_dm.send_operator_dm("hi", telegram_fallback=lambda: 55, is_html=False) == 55


def test_never_raises_when_all_routes_fail(monkeypatch) -> None:
    monkeypatch.delenv("SOMA_DISCORD_DM_DISABLED", raising=False)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")

    def boom(*_a, **_k):
        raise OSError("x")

    def tg_boom() -> int:
        raise RuntimeError("tg dead")

    monkeypatch.setattr(operator_dm.urllib.request, "urlopen", boom)
    assert operator_dm.send_operator_dm("hi", telegram_fallback=tg_boom, is_html=False) is None


def test_no_token_no_fallback_returns_none(monkeypatch) -> None:
    monkeypatch.delenv("SOMA_DISCORD_DM_DISABLED", raising=False)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "")
    monkeypatch.setattr(operator_dm, "_SECRETS_ENV", "/dev/null")
    monkeypatch.setattr(operator_dm, "_DISCORD_PLUGIN_ENV", "/dev/null")
    assert operator_dm.send_operator_dm("hi", telegram_fallback=None, is_html=False) is None
