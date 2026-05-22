# src/claude_soma/mcp_servers/voice_tts/server.py
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid
import wave
from pathlib import Path

from mcp.server.fastmcp import FastMCP


PIPER_BIN = os.environ.get("HERMES_PIPER_BIN", "/opt/piper/piper")
VOICES: dict[str, str] = {
    "default": os.environ.get(
        "HERMES_PIPER_DEFAULT_VOICE", "/opt/piper/en_US-ryan-medium.onnx"
    ),
    "ryan": "/opt/piper/en_US-ryan-medium.onnx",
}


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def _ensure_binary() -> str:
    if Path(PIPER_BIN).exists():
        return PIPER_BIN
    found = shutil.which("piper")
    if found is None:
        raise RuntimeError(f"piper binary not found at {PIPER_BIN} or on PATH")
    return found


def synthesize_impl(
    text: str, voice: str = "default", out_dir: str | None = None
) -> dict:
    if not text.strip():
        raise ValueError("text is empty")

    binary = _ensure_binary()
    model = VOICES.get(voice, VOICES["default"])
    if not Path(model).exists():
        raise FileNotFoundError(f"voice model not found: {model}")

    out_root = Path(out_dir) if out_dir else Path(tempfile.gettempdir())
    out_root.mkdir(parents=True, exist_ok=True)
    stem = f"tts_{uuid.uuid4().hex[:8]}"
    wav_path = out_root / f"{stem}.wav"
    opus_path = out_root / f"{stem}.opus"

    try:
        subprocess.run(
            [binary, "--model", model, "--output_file", str(wav_path),
             "--length_scale", "1.0", "--sentence_silence", "0.3"],
            input=text, text=True, capture_output=True, check=True, timeout=60,
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "")[-500:]
        raise RuntimeError(f"piper failed to synthesize {voice!r}: {stderr}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"piper timed out synthesizing {voice!r} (60s)") from e

    # Convert WAV → OGG/Opus for Telegram voice notes.
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(wav_path),
             "-c:a", "libopus", "-b:a", "48k", str(opus_path)],
            capture_output=True, check=True, timeout=60,
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode("utf-8", errors="replace")[-500:]
        raise RuntimeError(f"ffmpeg failed to encode opus: {stderr}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"ffmpeg timed out encoding opus (60s)") from e

    duration = round(_wav_duration(wav_path), 3)
    wav_path.unlink(missing_ok=True)  # keep only the opus file

    return {
        "audio_path": str(opus_path),
        "duration_seconds": duration,
        "voice_used": voice,
    }


mcp = FastMCP("voice_tts")


@mcp.tool()
def synthesize(text: str, voice: str = "default") -> dict:
    """Synthesize text to a Telegram-uploadable Opus voice note."""
    return synthesize_impl(text, voice)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
