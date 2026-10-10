#!/usr/bin/env python3
"""STT probe: whisper-cli on (1) a macOS `say` sample converted to 16 kHz mono WAV and (2) an
OGG/Opus voice note shaped like the ones Telegram clients send (Ogg container, Opus, mono, 48 kHz),
decoded the way claude-soma's voice_stt does (ffmpeg -ar 16000 -ac 1, then whisper-cli -l en -nt).

    python3.12 stt_whisper.py            # uses the first real ggml model found locally

Model search order: $WHISPER_MODEL, Homebrew share dirs, ~/models, ~/.cache/**/whisper. If none is
found it downloads ggml-base.en.bin into $OCSOMA_SCRATCH/models only. Writes
evidence/platform/stt-whisper.log.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "evidence" / "platform" / "stt-whisper.log"
SCRATCH = Path(os.environ.get("OCSOMA_SCRATCH", Path(tempfile.gettempdir()) / "ocsoma-me"))
WORK = SCRATCH / "stt"
TEXT = ("Remind me to call Asha about the invoice on Thursday afternoon, "
        "and move the Lisbon trip to April.")
BASE_EN = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin"


def run(cmd: list[str], timeout: int = 300) -> tuple[float, subprocess.CompletedProcess]:
    t0 = time.perf_counter()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"{cmd[0]} timed out after {timeout}s") from e
    return time.perf_counter() - t0, p


def words(s: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).split()


def wer(ref: str, hyp: str) -> float:
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / max(1, len(r))


def find_model() -> tuple[Path, str]:
    cands = [os.environ.get("WHISPER_MODEL", "")]
    for pat in ["/opt/homebrew/share/whisper-cpp/ggml-*.bin", "/opt/homebrew/Cellar/whisper-cpp/*/share/**/ggml-*.bin",
                "/usr/local/share/whisper-cpp/ggml-*.bin", str(Path.home() / "models" / "**" / "ggml-*.bin"),
                str(Path.home() / ".cache" / "**" / "whisper" / "**" / "ggml-*.bin")]:
        cands += sorted(glob.glob(pat, recursive=True))
    real = [c for c in cands if c and Path(c).is_file() and "for-tests" not in c]
    pref = sorted(real, key=lambda c: (".en." not in c, "small" not in c and "base" not in c, c))
    if pref:
        return Path(pref[0]), "found locally"
    dest = SCRATCH / "models" / "ggml-base.en.bin"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        urllib.request.urlretrieve(BASE_EN, dest)
    return dest, "downloaded into scratch"


def transcribe(model: Path, wav: Path) -> tuple[float, str, int]:
    dt, p = run(["whisper-cli", "-m", str(model), "-f", str(wav), "-l", "en", "-nt", "-np"])
    return dt, p.stdout.strip(), p.returncode


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []

    def out(s: str = "") -> None:
        lines.append(s)
        print(s, flush=True)

    out(f"date_utc: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    out("command: python3.12 " + " ".join([Path(sys.argv[0]).name] + sys.argv[1:]))
    brew = subprocess.run(["brew", "list", "--versions", "whisper-cpp"], capture_output=True, text=True).stdout.strip()
    ff = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.splitlines()[0]
    out(f"host: {platform.system()} {platform.release()} {platform.machine()} (macOS {platform.mac_ver()[0]})")
    out(f"whisper-cli: {shutil.which('whisper-cli')} ({brew or 'version unknown'})")
    out(f"ffmpeg: {ff}")
    model, how = find_model()
    sha = hashlib.sha256(model.read_bytes()).hexdigest()[:16]
    out(f"model: {model} ({how}, {model.stat().st_size / 1e6:.1f} MB, sha256 {sha}...)")
    out(f"reference text: {TEXT}")

    aiff, wav, ogg, ogg_wav = WORK / "sample.aiff", WORK / "sample.wav", WORK / "voice.ogg", WORK / "voice16k.wav"
    dt, p = run(["say", "-v", "Samantha", "-o", str(aiff), TEXT])
    out(f"say -> aiff: rc={p.returncode} {dt:.2f}s")
    dt, p = run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)])
    out(f"ffmpeg aiff -> 16k mono wav: rc={p.returncode} {dt:.2f}s")
    dt, p = run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff), "-ac", "1", "-ar", "48000", "-c:a", "libopus",
                 "-b:a", "32k", "-application", "voip", str(ogg)])
    out(f"ffmpeg aiff -> ogg/opus voice note: rc={p.returncode} {dt:.2f}s")
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                       "format=format_name,duration:stream=codec_name,sample_rate,channels",
                                       "-of", "json", str(ogg)], capture_output=True, text=True).stdout)
    out(f"ffprobe voice.ogg: {json.dumps(probe)}")

    results = {}
    for label in ("wav cold", "wav warm 1", "wav warm 2", "wav warm 3"):
        dt, text, rc = transcribe(model, wav)
        results[label] = (dt, text, rc)
        out(f"whisper {label}: rc={rc} {dt:.2f}s WER={wer(TEXT, text):.3f} text={text!r}")
    t0 = time.perf_counter()
    dt1, p = run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(ogg), "-ar", "16000", "-ac", "1", str(ogg_wav)])
    dt2, text, rc = transcribe(model, ogg_wav)
    total = time.perf_counter() - t0
    results["ogg via ffmpeg"] = (total, text, rc)
    out(f"ogg voice note, claude-soma path (ffmpeg {dt1:.2f}s + whisper {dt2:.2f}s = {total:.2f}s): rc={rc} "
        f"WER={wer(TEXT, text):.3f} text={text!r}")
    dt, p = run(["whisper-cli", "-m", str(model), "-f", str(ogg), "-l", "en", "-nt", "-np"])
    out(f"whisper-cli directly on .ogg (no ffmpeg): rc={p.returncode} {dt:.2f}s text={p.stdout.strip()!r} "
        f"stderr_tail={p.stderr.strip()[-160:]!r}")

    warm = [results[k][0] for k in results if k.startswith("wav warm")]
    ok = all(results[k][2] == 0 for k in results) and max(wer(TEXT, results[k][1]) for k in results) <= 0.15
    audio_s = float(probe["format"]["duration"])
    verdict = "PASS" if ok else "FAIL"
    out(f"VERDICT: {verdict} — whisper-cli ({model.name}) transcribed a {audio_s:.1f}s clip in "
        f"{min(warm):.2f}-{max(warm):.2f}s warm (cold {results['wav cold'][0]:.2f}s) and the Telegram-style OGG/Opus "
        f"note in {results['ogg via ffmpeg'][0]:.2f}s end to end; worst WER "
        f"{max(wer(TEXT, results[k][1]) for k in results):.3f}")
    LOG.write_text("\n".join(lines) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
