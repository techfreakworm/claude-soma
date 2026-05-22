from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def sample_wav(tmp_path_factory) -> Path:
    """Synthesize a tiny WAV using piper if available, else a silent WAV."""
    out = tmp_path_factory.mktemp("audio") / "sample.wav"
    piper = shutil.which("piper") or "/opt/piper/piper"
    piper_ok = False
    if Path(piper).exists():
        try:
            subprocess.run(
                [piper, "--model", "/opt/piper/en_US-ryan-medium.onnx",
                 "--output_file", str(out)],
                input="This is a test recording.\n",
                text=True, check=True, capture_output=True,
            )
            piper_ok = True
        except subprocess.CalledProcessError:
            piper_ok = False
    if not piper_ok:
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 16000)
    return out
