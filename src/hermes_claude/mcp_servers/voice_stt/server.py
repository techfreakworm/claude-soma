from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from mcp.server.fastmcp import FastMCP


WHISPER_BIN = os.environ.get(
    "HERMES_WHISPER_BIN", "/opt/whisper.cpp/build/bin/whisper-cli"
)
WHISPER_MODEL = os.environ.get(
    "HERMES_WHISPER_MODEL", "/opt/whisper.cpp/models/ggml-large-v3-turbo.bin"
)


def _wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except wave.Error:
        # Probably not a WAV — let whisper.cpp handle conversion implicitly.
        return 0.0


def _ensure_binary() -> str:
    if Path(WHISPER_BIN).exists():
        return WHISPER_BIN
    found = shutil.which("whisper-cli")
    if found is None:
        raise RuntimeError(
            f"whisper.cpp binary not found at {WHISPER_BIN} or on PATH"
        )
    return found


def _convert_to_wav(src: Path, dst: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-ar", "16000", "-ac", "1",
         "-c:a", "pcm_s16le", str(dst)],
        check=True, capture_output=True,
    )


def transcribe_impl(audio_path: str, language: str = "auto") -> dict:
    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError(f"audio file not found: {audio_path}")

    binary = _ensure_binary()

    with tempfile.TemporaryDirectory(prefix="hermes-stt-") as tmpdir:
        wav_path = Path(tmpdir) / "in.wav"
        # Always normalize via ffmpeg so we accept ogg/opus/m4a/etc.
        _convert_to_wav(path, wav_path)
        out_prefix = Path(tmpdir) / "out"

        lang_flag = "auto" if language in ("auto", "", None) else language
        result = subprocess.run(
            [binary, "-m", WHISPER_MODEL, "-f", str(wav_path),
             "-l", lang_flag, "-otxt", "-of", str(out_prefix), "-nt"],
            capture_output=True, text=True, check=True,
        )

        text_path = out_prefix.with_suffix(".txt")
        text = text_path.read_text().strip() if text_path.exists() else ""

        # whisper.cpp prints "auto-detected language: <code>" to stderr.
        detected = "en"
        m = re.search(r"auto-detected language: (\w+)", result.stderr)
        if m:
            detected = m.group(1)
        elif lang_flag != "auto":
            detected = lang_flag

        return {
            "text": text,
            "language_detected": detected,
            "duration_seconds": round(_wav_duration(wav_path), 3),
            "confidence": None,  # whisper.cpp doesn't expose this in -nt mode
        }


mcp = FastMCP("voice_stt")


@mcp.tool()
def transcribe(audio_path: str, language: str = "auto") -> dict:
    """Transcribe an audio file (any ffmpeg-readable format) to text."""
    return transcribe_impl(audio_path, language)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
