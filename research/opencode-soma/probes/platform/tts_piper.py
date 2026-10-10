#!/usr/bin/env python3
"""TTS probe: piper-tts from PyPI in a throwaway venv, synthesize a sentence with the same voice
claude-soma ships (en_US-ryan-medium), convert to OGG/Opus exactly as
src/claude_soma/mcp_servers/voice_tts/server.py does (ffmpeg -c:a libopus -b:a 48k), validate with
ffprobe, and round-trip the audio through whisper-cli to check it is intelligible.

    python3.12 tts_piper.py

Everything (venv, voice files, audio) lives under $OCSOMA_SCRATCH/tts. Writes
evidence/platform/tts-piper.log.
"""
from __future__ import annotations

import glob
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stt_whisper import find_model, wer  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "evidence" / "platform" / "tts-piper.log"
SCRATCH = Path(os.environ.get("OCSOMA_SCRATCH", Path(tempfile.gettempdir()) / "ocsoma-me"))
WORK = SCRATCH / "tts"
VENV = WORK / "venv"
PIPER_VERSION = os.environ.get("PIPER_VERSION", "1.8.0")
VOICE = "en_US-ryan-medium"
VOICE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/ryan/medium/" + VOICE
TEXT = "Your nightly backup finished. Two projects need your review, and the Lisbon trip is now in April."


def run(cmd: list[str], timeout: int = 600, **kw) -> tuple[float, subprocess.CompletedProcess]:
    t0 = time.perf_counter()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"{cmd[0]} timed out after {timeout}s") from e
    return time.perf_counter() - t0, p


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    def out(s: str = "") -> None:
        lines.append(s)
        print(s, flush=True)

    out(f"date_utc: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    out("command: python3.12 " + " ".join([Path(sys.argv[0]).name] + sys.argv[1:]))
    out(f"host: {platform.system()} {platform.release()} {platform.machine()} (macOS {platform.mac_ver()[0]}), "
        f"python {platform.python_version()}")
    meta = json.load(urllib.request.urlopen(f"https://pypi.org/pypi/piper-tts/{PIPER_VERSION}/json", timeout=30))
    out(f"PyPI piper-tts {PIPER_VERSION} files (platform coverage):")
    for f in meta["urls"]:
        out(f"  {f['filename']}  {f['size'] / 1e6:.1f} MB")
    if not (VENV / "bin" / "python").exists():
        dt, p = run([sys.executable, "-m", "venv", str(VENV)])
        out(f"venv created in {dt:.1f}s")
    dt, p = run([str(VENV / "bin" / "pip"), "install", "-q", "--disable-pip-version-check", f"piper-tts=={PIPER_VERSION}"])
    out(f"pip install piper-tts=={PIPER_VERSION}: rc={p.returncode} {dt:.1f}s {p.stderr.strip()[-300:]}")
    _, p = run([str(VENV / "bin" / "pip"), "list", "--format=freeze", "--disable-pip-version-check"])
    out("venv packages: " + ", ".join(ln for ln in p.stdout.split() if ln.split("==")[0].lower()
                                      in ("piper-tts", "onnxruntime", "numpy", "pathvalidate")))
    site = glob.glob(str(VENV / "lib" / "python*" / "site-packages"))[0]
    _, p = run(["du", "-sk", site])
    out(f"venv site-packages size: {int(p.stdout.split()[0]) / 1024:.0f} MB")

    vdir = WORK / "voices"
    vdir.mkdir(exist_ok=True)
    for ext in (".onnx", ".onnx.json"):
        dest = vdir / (VOICE + ext)
        if not dest.exists():
            t0 = time.perf_counter()
            urllib.request.urlretrieve(VOICE_URL + ext, dest)
            out(f"downloaded {dest.name} ({dest.stat().st_size / 1e6:.1f} MB) in {time.perf_counter() - t0:.1f}s")
        else:
            out(f"voice file present: {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")

    wav, opus = WORK / "reply.wav", WORK / "reply.opus"
    synth = [str(VENV / "bin" / "python"), "-m", "piper", "-m", str(vdir / (VOICE + ".onnx")), "-f", str(wav),
             "--length-scale", "1.0", "--sentence-silence", "0.3"]
    times = []
    for i in range(3):
        dt, p = run(synth, input=TEXT)
        times.append(dt)
        out(f"piper CLI synth run {i + 1} (new process, loads the model each time): rc={p.returncode} {dt:.2f}s "
            f"{p.stderr.strip()[-200:]!r}")
    code = ("import sys, time, wave\nfrom piper import PiperVoice\n"
            "t0=time.perf_counter(); v=PiperVoice.load(sys.argv[1]); t1=time.perf_counter()\n"
            "for i in range(3):\n"
            "    s=time.perf_counter()\n"
            "    with wave.open(sys.argv[2], 'wb') as w: v.synthesize_wav(sys.argv[3], w)\n"
            "    print(f'in-process synth {i + 1}: {time.perf_counter()-s:.3f}s')\n"
            "print(f'model load: {t1-t0:.3f}s')\n")
    dt, p = run([str(VENV / "bin" / "python"), "-c", code, str(vdir / (VOICE + ".onnx")), str(WORK / "inproc.wav"), TEXT])
    out(f"python API (model loaded once): rc={p.returncode} " + " | ".join(p.stdout.strip().splitlines())
        + (f" stderr={p.stderr.strip()[-200:]!r}" if p.returncode else ""))
    dt, p = run(["ffmpeg", "-y", "-i", str(wav), "-c:a", "libopus", "-b:a", "48k", str(opus)])
    out(f"ffmpeg wav -> opus (claude-soma args): rc={p.returncode} {dt:.2f}s")
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                       "format=format_name,duration,bit_rate:stream=codec_name,sample_rate,channels",
                                       "-of", "json", str(opus)], capture_output=True, text=True).stdout)
    out(f"ffprobe reply.opus: {json.dumps(probe)}")
    fmt_ok = probe["format"]["format_name"] == "ogg" and probe["streams"][0]["codec_name"] == "opus"

    model, how = find_model()
    w16 = WORK / "reply16k.wav"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(opus), "-ar", "16000", "-ac", "1", str(w16)])
    dt, p = run(["whisper-cli", "-m", str(model), "-f", str(w16), "-l", "en", "-nt", "-np"])
    hyp = p.stdout.strip()
    err = wer(TEXT, hyp)
    out(f"round trip: opus -> whisper ({model.name}, {how}) {dt:.2f}s WER={err:.3f} text={hyp!r}")
    ok = fmt_ok and err <= 0.2 and all(t > 0 for t in times)
    out(f"VERDICT: {'PASS' if ok else 'FAIL'} — piper-tts {PIPER_VERSION} (PyPI wheel, macOS arm64) synthesized "
        f"{float(probe['format']['duration']):.1f}s of speech in {min(times):.2f}-{max(times):.2f}s per CLI call; "
        f"ffmpeg produced a valid Ogg/Opus voice note (Telegram sendVoice format); whisper round-trip WER {err:.3f}")
    LOG.write_text("\n".join(lines) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
