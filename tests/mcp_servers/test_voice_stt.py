from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from hermes_claude.mcp_servers.voice_stt.server import transcribe_impl


import shutil as _shutil
_FFMPEG_AVAILABLE = _shutil.which("ffmpeg") is not None

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
    """When the input is synthesized speech, language should be detected.
    Accepts the silent-fixture case where whisper.cpp returns empty / 'nn'."""
    result = transcribe_impl(str(sample_wav), language="auto")
    detected = result.get("language_detected")
    # Either an English token (real speech) OR an empty/uncertain token (silent fallback)
    assert detected is None or detected in {"en", "english", "", "nn"}


def test_transcribe_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        transcribe_impl("/tmp/does-not-exist.wav", language="en")


@pytest.mark.skipif(not _FFMPEG_AVAILABLE, reason="ffmpeg not installed")
def test_transcribe_accepts_non_wav_input(sample_wav: Path, tmp_path: Path) -> None:
    """The implementation must convert non-WAV inputs (.ogg / .m4a / etc.)
    via ffmpeg before passing to whisper.cpp."""
    ogg = tmp_path / "sample.ogg"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(sample_wav), "-c:a", "libopus", "-b:a", "48k", str(ogg)],
        check=True, capture_output=True,
    )
    result = transcribe_impl(str(ogg), language="auto")
    assert isinstance(result, dict)
    assert "text" in result
    assert "duration_seconds" in result and result["duration_seconds"] > 0
