from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _disable_discord_dm_route(monkeypatch):
    """Keep unit tests off the live Discord API.

    src/claude_soma/operator_dm.py is Discord-primary, and on the VPS the real
    DISCORD_BOT_TOKEN is readable from /etc/claude-soma/secrets.env — so without
    this guard a notify unit test would send a real Discord message. Disabling
    the Discord route makes send_operator_dm fall straight through to the
    Telegram fallback, so the existing notify tests stay deterministic. Tests
    that specifically exercise the Discord route delenv this var themselves.

    Note: this only affects the Python helper (SOMA_DISCORD_DM_DISABLED). The
    shell helper uses a different switch (SOMA_NOTIFY_DISCORD_DISABLED), so bash
    script tests under tests/scripts/ are unaffected.
    """
    monkeypatch.setenv("SOMA_DISCORD_DM_DISABLED", "1")


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
