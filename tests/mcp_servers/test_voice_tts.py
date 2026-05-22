# tests/mcp_servers/test_voice_tts.py
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from claude_soma.mcp_servers.voice_tts.server import synthesize_impl


pytestmark = pytest.mark.skipif(
    not Path("/opt/piper/piper").exists() and not shutil.which("piper"),
    reason="piper not installed; integration test",
)


def test_synthesize_returns_audio_path(tmp_path: Path) -> None:
    out_dir = tmp_path
    result = synthesize_impl("Hello world.", voice="default", out_dir=str(out_dir))
    assert "audio_path" in result
    assert Path(result["audio_path"]).exists()
    assert Path(result["audio_path"]).stat().st_size > 0


def test_synthesize_output_is_opus(tmp_path: Path) -> None:
    result = synthesize_impl("Telegram voice note test.", out_dir=str(tmp_path))
    assert result["audio_path"].endswith(".opus")


def test_synthesize_duration_present(tmp_path: Path) -> None:
    result = synthesize_impl(
        "This is a slightly longer sentence to ensure we get measurable duration.",
        out_dir=str(tmp_path),
    )
    assert isinstance(result.get("duration_seconds"), float)
    assert result["duration_seconds"] > 0.5
