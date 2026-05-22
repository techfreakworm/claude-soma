from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from hermes_claude.mcp_servers.voice_stt.server import transcribe_impl


pytestmark = pytest.mark.skipif(
    not Path("/opt/whisper.cpp/build/bin/whisper-cli").exists()
    and not shutil.which("whisper-cli"),
    reason="whisper.cpp not installed; integration test",
)


def test_transcribe_returns_text_for_sample(sample_wav: Path) -> None:
    result = transcribe_impl(str(sample_wav), language="en")
    assert isinstance(result, dict)
    assert "text" in result and isinstance(result["text"], str)
    assert "duration_seconds" in result and result["duration_seconds"] > 0


def test_transcribe_returns_language_detected(sample_wav: Path) -> None:
    result = transcribe_impl(str(sample_wav), language="auto")
    assert result.get("language_detected") in {"en", "english"}


def test_transcribe_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        transcribe_impl("/tmp/does-not-exist.wav", language="en")
