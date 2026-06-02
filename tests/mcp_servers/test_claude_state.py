from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from claude_soma.mcp_servers.hermes_api.claude_state import (
    list_transcript_threads,
    read_memory,
    read_transcript,
)


def _make_projects_root(tmp_path: Path) -> Path:
    root = tmp_path / ".claude" / "projects"
    root.mkdir(parents=True)
    return root


def test_list_threads_globs_direct(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = _make_projects_root(tmp_path)
    proj = root / "-fake-proj"
    proj.mkdir()
    f1 = proj / "abc.jsonl"
    f2 = proj / "def.jsonl"
    f1.write_text('{"a": 1}\n')
    # ensure distinct mtimes
    time.sleep(0.01)
    f2.write_text('{"b": 2}\n')

    monkeypatch.setenv("HERMES_CLAUDE_PROJECTS_ROOT", str(root))

    result = list_transcript_threads()
    assert len(result) == 2
    for entry in result:
        assert entry["project"] == "-fake-proj"
    thread_ids = {e["thread_id"] for e in result}
    assert thread_ids == {"abc", "def"}
    # sorted desc by mtime: def should come first
    assert result[0]["thread_id"] == "def"


def test_list_threads_skips_missing_proj(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing = tmp_path / "no-such-dir"
    monkeypatch.setenv("HERMES_CLAUDE_PROJECTS_ROOT", str(missing))

    result = list_transcript_threads()
    assert result == []


def test_read_transcript_no_subdir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _make_projects_root(tmp_path)
    proj = root / "-fake-proj"
    proj.mkdir()
    lines = [{"type": "say", "text": "hello"}, {"type": "say", "text": "world"}]
    (proj / "abc.jsonl").write_text("\n".join(json.dumps(l) for l in lines) + "\n")

    monkeypatch.setenv("HERMES_CLAUDE_PROJECTS_ROOT", str(root))

    result = read_transcript("abc", "-fake-proj")
    assert len(result) == 2
    assert result[0] == {"type": "say", "text": "hello"}
    assert result[1] == {"type": "say", "text": "world"}


def test_read_memory_via_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = _make_projects_root(tmp_path)
    mem_dir = root / "-home-ubuntu-projects-fake" / "memory"
    mem_dir.mkdir(parents=True)
    content = "# H1\n## S1\nbody\n## S2\n"
    (mem_dir / "MEMORY.md").write_text(content)

    monkeypatch.setenv("HERMES_CLAUDE_PROJECTS_ROOT", str(root))

    result = read_memory("fake", cwd="/home/ubuntu/projects/fake")

    assert result["project"] == "fake"
    assert result["text"] == content
    stats = result["stats"]
    assert stats["headings"] == 1
    assert stats["sections"] == 2
    assert stats["bytes"] > 0
    assert stats["lines"] > 0
    assert stats["chars"] == len(content)
    assert stats["last_modified"] > 0.0
    assert "-home-ubuntu-projects-fake" in stats["path"]
    assert stats["path"].endswith("MEMORY.md")


def test_read_memory_fallback_no_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _make_projects_root(tmp_path)
    mem_dir = root / "-home-ubuntu-projects-fake" / "memory"
    mem_dir.mkdir(parents=True)
    content = "# H1\n## S1\nbody\n## S2\n"
    (mem_dir / "MEMORY.md").write_text(content)

    monkeypatch.setenv("HERMES_CLAUDE_PROJECTS_ROOT", str(root))

    result = read_memory("fake")

    assert result["project"] == "fake"
    assert result["text"] == content
    stats = result["stats"]
    assert stats["headings"] == 1
    assert stats["sections"] == 2
    assert "-home-ubuntu-projects-fake" in stats["path"]


def test_read_memory_default_slug(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _make_projects_root(tmp_path)
    mem_dir = root / "-home-ubuntu" / "memory"
    mem_dir.mkdir(parents=True)
    content = "# Global memory\nsome text\n"
    (mem_dir / "MEMORY.md").write_text(content)

    monkeypatch.setenv("HERMES_CLAUDE_PROJECTS_ROOT", str(root))

    result = read_memory("default")

    assert result["project"] == "default"
    assert result["text"] == content
    assert "-home-ubuntu" in result["stats"]["path"]
    assert result["stats"]["bytes"] > 0


def test_read_memory_no_match_returns_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _make_projects_root(tmp_path)
    monkeypatch.setenv("HERMES_CLAUDE_PROJECTS_ROOT", str(root))

    result = read_memory("nonexistent-slug-xyz")

    assert result["project"] == "nonexistent-slug-xyz"
    assert result["text"] == ""
    stats = result["stats"]
    assert stats["bytes"] == 0
    assert stats["lines"] == 0
    assert stats["chars"] == 0
    assert stats["sections"] == 0
    assert stats["headings"] == 0
    assert stats["last_modified"] == 0.0
    assert stats["path"] == ""
