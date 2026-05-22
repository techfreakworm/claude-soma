# Hermes-Claude V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Claude-Code-native messaging platform deployed on OCI Ubuntu ARM that bridges Telegram (voice + text) to Claude via Max-subscription auth, with an orchestrator that spawns independent project-leads each running their own agent teams, surfaced by a showcase-grade dashboard at `claude.mayankgupta.in`.

**Architecture:** Persistent `claude --channels` session on OCI runs the orchestrator; a Python plugin contributes skills/agents/hooks and four stdio MCP servers (voice_stt, voice_tts, project_orchestrator, hermes_api); a FastAPI backend bridges Claude's runtime state to a Next.js 16 dashboard; Caddy reverse-proxies both behind auto-HTTPS. All compute paths use Claude Max via `CLAUDE_CODE_OAUTH_TOKEN`; image generation delegates to the user's Codex CLI subscription.

**Tech Stack:** Python 3.12 (MCP servers, FastAPI, wizard) · whisper.cpp ARM build · piper TTS · SQLite for registry/usage · Next.js 16 (App Router) + TypeScript + Tailwind v4 + shadcn/ui + Recharts + react-flow + Framer Motion + Auth.js v5 · Caddy v2 · systemd · Claude Code v2.1.x · `claude-plugins-official` telegram plugin.

**Spec reference:** `docs/superpowers/specs/2026-05-22-hermes-claude-design.md`

---

## File Structure

```
hermes-claude/
├─ .claude-plugin/
│   ├─ plugin.json
│   └─ marketplace.json
├─ pyproject.toml                            # Python deps for MCP servers + API + wizard
├─ Caddyfile                                  # reverse proxy
│
├─ skills/
│   ├─ codex-image-gen/SKILL.md
│   ├─ schedule-routine/SKILL.md
│   ├─ respond-with-voice/SKILL.md
│   ├─ voice-action/SKILL.md
│   ├─ spawn-project/SKILL.md
│   ├─ list-projects/SKILL.md
│   ├─ message-project/SKILL.md
│   ├─ kill-project/SKILL.md
│   ├─ project-status/SKILL.md
│   ├─ portfolio-status/SKILL.md
│   └─ usage-report/SKILL.md
│
├─ agents/
│   ├─ content-drafter.md
│   ├─ tool-builder.md
│   └─ project-lead.md
│
├─ hooks/
│   └─ hooks.json
│
├─ scripts/
│   ├─ session_start_context.sh
│   ├─ voice_intake.sh
│   ├─ log_activity.sh
│   ├─ healthcheck.sh
│   ├─ reaper.py
│   ├─ usage_snapshot.py
│   ├─ cache_refresh.py
│   └─ deploy.sh
│
├─ src/hermes_claude/                        # python package
│   ├─ __init__.py
│   ├─ mcp_servers/
│   │   ├─ __init__.py
│   │   ├─ voice_stt/
│   │   │   ├─ __init__.py
│   │   │   └─ server.py
│   │   ├─ voice_tts/
│   │   │   ├─ __init__.py
│   │   │   └─ server.py
│   │   ├─ project_orchestrator/
│   │   │   ├─ __init__.py
│   │   │   ├─ server.py
│   │   │   ├─ registry.py
│   │   │   ├─ spawner.py
│   │   │   └─ templates.py
│   │   └─ hermes_api/
│   │       ├─ __init__.py
│   │       ├─ server.py
│   │       ├─ claude_state.py
│   │       └─ socket.py
│   ├─ api/
│   │   ├─ __init__.py
│   │   ├─ main.py
│   │   ├─ auth.py
│   │   ├─ routes/
│   │   │   ├─ __init__.py
│   │   │   ├─ healthz.py
│   │   │   ├─ projects.py
│   │   │   ├─ conversations.py
│   │   │   ├─ routines.py
│   │   │   ├─ usage.py
│   │   │   ├─ memory.py
│   │   │   ├─ logs.py
│   │   │   ├─ events.py
│   │   │   ├─ admin.py
│   │   │   └─ public.py
│   │   └─ bridge.py                          # unix-socket client to hermes_api MCP
│   └─ wizard/
│       ├─ __init__.py
│       └─ init.py
│
├─ templates/projects/
│   ├─ web-scraper.json
│   ├─ llm-app.json
│   ├─ server-app.json
│   ├─ agentic-coding.json
│   └─ custom.json
│
├─ frontend/                                  # next.js dashboard
│   ├─ package.json
│   ├─ next.config.mjs
│   ├─ tsconfig.json
│   ├─ tailwind.config.ts
│   ├─ postcss.config.js
│   ├─ app/
│   │   ├─ layout.tsx
│   │   ├─ page.tsx                           # public landing
│   │   ├─ api/auth/[...nextauth]/route.ts    # auth.js
│   │   └─ admin/
│   │       ├─ layout.tsx
│   │       ├─ page.tsx                       # overview
│   │       ├─ projects/page.tsx
│   │       ├─ conversations/page.tsx
│   │       ├─ routines/page.tsx
│   │       ├─ usage/page.tsx
│   │       ├─ memory/page.tsx
│   │       └─ logs/page.tsx
│   ├─ components/
│   │   ├─ ui/                                # shadcn primitives
│   │   ├─ landing/
│   │   ├─ admin/
│   │   └─ shared/
│   └─ lib/
│       ├─ api.ts
│       ├─ sse.ts
│       └─ auth.ts
│
├─ systemd/                                   # unit files
│   ├─ hermes-claude-channel.service
│   ├─ hermes-claude-api.service
│   ├─ hermes-claude-frontend.service
│   ├─ hermes-healthcheck.timer
│   ├─ hermes-healthcheck.service
│   ├─ hermes-cache-refresh.timer
│   ├─ hermes-cache-refresh.service
│   ├─ hermes-usage-snapshot.timer
│   ├─ hermes-usage-snapshot.service
│   ├─ hermes-idle-reaper.timer
│   └─ hermes-idle-reaper.service
│
├─ tests/
│   ├─ conftest.py
│   ├─ mcp_servers/
│   │   ├─ test_voice_stt.py
│   │   ├─ test_voice_tts.py
│   │   ├─ test_project_orchestrator.py
│   │   └─ test_hermes_api.py
│   ├─ api/
│   │   ├─ test_healthz.py
│   │   ├─ test_projects.py
│   │   ├─ test_conversations.py
│   │   ├─ test_routines.py
│   │   ├─ test_usage.py
│   │   ├─ test_memory.py
│   │   ├─ test_logs.py
│   │   ├─ test_events_sse.py
│   │   ├─ test_admin.py
│   │   ├─ test_public.py
│   │   └─ test_auth.py
│   └─ wizard/
│       └─ test_init.py
│
└─ docs/
    └─ superpowers/
        ├─ specs/2026-05-22-hermes-claude-design.md
        └─ plans/2026-05-22-hermes-claude-v1.md      ← this file
```

Each file has one responsibility. MCP server packages keep `server.py` thin (tool definitions only) — supporting logic lives in named siblings (`registry.py`, `spawner.py`, `claude_state.py`, `socket.py`). FastAPI routes split by resource. Frontend mirrors `app/` Next.js convention with per-section components in `components/{landing,admin,shared}/`.

---

## Week 1 — Infrastructure Foundation

Goal of Week 1: voice memo on Telegram → transcript becomes Claude prompt → text reply back. Survives reboot.

### Task 1: Provision OCI Ubuntu ARM VPS

**Files:**
- Modify (manual): Oracle Cloud Console
- Create: `~/.ssh/config` entry (locally)

This task is manual cloud provisioning; verify via SSH.

- [ ] **Step 1: Create the instance**

In OCI Console → Compute → Instances → Create:
- Shape: `VM.Standard.A1.Flex` (Ampere ARM)
- OCPU: 4 · Memory: 24 GB · Boot volume: 50 GB
- Image: `Canonical Ubuntu 24.04`
- VCN: default
- Public IPv4: assign
- SSH key: paste your local `~/.ssh/id_ed25519.pub`

- [ ] **Step 2: Open required ports in OCI Network Security List**

Add ingress rules to the VCN's default security list:
- TCP 22 from 0.0.0.0/0 (or your IP for tighter scope) — SSH
- TCP 80 from 0.0.0.0/0 — Let's Encrypt HTTP-01
- TCP 443 from 0.0.0.0/0 — HTTPS

- [ ] **Step 3: Configure local SSH alias**

Append to `~/.ssh/config` on M5 Max:

```
Host oci-hermes
    HostName <public-ip>
    User ubuntu
    IdentityFile ~/.ssh/id_ed25519
    ServerAliveInterval 60
```

- [ ] **Step 4: Verify connectivity**

Run: `ssh oci-hermes 'uname -a && free -h && nproc'`
Expected: kernel info, ~24 GB memory, 4 processors

- [ ] **Step 5: Open iptables for HTTP/HTTPS on the host**

OCI Ubuntu images leave iptables blocking 80/443 by default. On VPS:

```bash
ssh oci-hermes
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

- [ ] **Step 6: No commit (infra-only)**

Provisioning is captured in setup wizard later; nothing to commit here.

---

### Task 2: Bootstrap OS dependencies on the VPS

**Files:**
- Modify (remote): system packages on OCI VPS

- [ ] **Step 1: Update apt + install base packages**

Run on VPS:

```bash
sudo apt-get update
sudo apt-get install -y build-essential git curl wget pkg-config \
    python3.12 python3.12-venv python3.12-dev python3-pip \
    ffmpeg cmake clang tmux jq sqlite3 \
    libsox-dev libssl-dev
```

- [ ] **Step 2: Install Node 20 LTS (for Next.js and Claude Code)**

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
node -v   # expect v20.x
npm -v
```

- [ ] **Step 3: Install pnpm (frontend package manager)**

```bash
sudo npm install -g pnpm
pnpm -v   # expect 9.x or 10.x
```

- [ ] **Step 4: Install Caddy v2 (apt repository)**

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | \
    sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | \
    sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
systemctl status caddy --no-pager   # expect active (running)
```

- [ ] **Step 5: Smoke test all installs**

```bash
python3.12 --version  # 3.12.x
node --version        # v20.x
pnpm --version        # 9.x+
caddy version         # v2.x
ffmpeg -version | head -1
cmake --version | head -1
sqlite3 --version
```
All should print versions; no errors.

- [ ] **Step 6: No commit (infra-only)**

---

### Task 3: Install Claude Code on the VPS and mint Max OAuth token

**Files:**
- Create (remote): `/etc/hermes-claude/secrets.env`

- [ ] **Step 1: Install Claude Code globally**

```bash
sudo npm install -g @anthropic-ai/claude-code
claude --version   # expect 2.1.x
```

- [ ] **Step 2: Mint a long-lived Max OAuth token on M5 Max**

On your M5 Max (the machine you OAuth on — NOT on the VPS):

```bash
claude setup-token
```

A browser opens, sign in to Claude Max, authorize, then a token starting with `oat-` prints to stdout. Copy it — valid 1 year.

- [ ] **Step 3: Install the token securely on the VPS**

On VPS:

```bash
sudo install -d -m 700 -o ubuntu -g ubuntu /etc/hermes-claude
sudo tee /etc/hermes-claude/secrets.env >/dev/null <<EOF
CLAUDE_CODE_OAUTH_TOKEN=oat-paste-the-token-here
EOF
sudo chmod 600 /etc/hermes-claude/secrets.env
sudo chown ubuntu:ubuntu /etc/hermes-claude/secrets.env
```

- [ ] **Step 4: Verify the token works**

```bash
export $(cat /etc/hermes-claude/secrets.env)
claude -p 'reply with exactly the word OK and nothing else' --output-format text
```
Expected: prints `OK` (single line). If it prompts for login, the token is wrong.

- [ ] **Step 5: No commit (secret material; secrets.env is gitignored already)**

---

### Task 4: Create plugin directory skeleton and pyproject.toml

**Files:**
- Create: `/Users/techfreakworm/Projects/llm/hermes-claude/.claude-plugin/plugin.json`
- Create: `/Users/techfreakworm/Projects/llm/hermes-claude/pyproject.toml`
- Create: `/Users/techfreakworm/Projects/llm/hermes-claude/README.md` (stub)

- [ ] **Step 1: Create plugin.json**

```json
{
  "name": "hermes-claude",
  "version": "0.1.0-dev",
  "description": "Hermes-Agent's value in 10% the code, by riding Claude Code's native rails.",
  "author": "Mayank Gupta",
  "homepage": "https://claude.mayankgupta.in",
  "license": "MIT"
}
```

- [ ] **Step 2: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[project]
name = "hermes-claude"
version = "0.1.0"
requires-python = ">=3.12"
license = { text = "MIT" }
dependencies = [
    "mcp>=1.4",
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "pydantic>=2.9",
    "httpx>=0.28",
    "aiofiles>=24.1",
    "sse-starlette>=2.1",
    "python-multipart>=0.0.12",
    "anyio>=4.6",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "pytest-cov>=5.0",
    "ruff>=0.7",
    "mypy>=1.13",
    "httpx>=0.28",
]

[project.scripts]
hermes-voice-stt = "hermes_claude.mcp_servers.voice_stt.server:main"
hermes-voice-tts = "hermes_claude.mcp_servers.voice_tts.server:main"
hermes-orchestrator = "hermes_claude.mcp_servers.project_orchestrator.server:main"
hermes-api-mcp = "hermes_claude.mcp_servers.hermes_api.server:main"
hermes-api = "hermes_claude.api.main:run"
hermes-init = "hermes_claude.wizard.init:main"

[tool.hatch.build.targets.wheel]
packages = ["src/hermes_claude"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "-q --strict-markers"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.mypy]
python_version = "3.12"
strict = true
```

- [ ] **Step 3: Create README.md stub**

```markdown
# Hermes-Claude

Hermes-Agent's value in 10% the code, by riding Claude Code's native rails.

See [docs/superpowers/specs/2026-05-22-hermes-claude-design.md](docs/superpowers/specs/2026-05-22-hermes-claude-design.md) for the design.

Status: in development.
```

- [ ] **Step 4: Create the package skeleton**

```bash
mkdir -p src/hermes_claude/{mcp_servers,api,wizard}
mkdir -p src/hermes_claude/mcp_servers/{voice_stt,voice_tts,project_orchestrator,hermes_api}
mkdir -p src/hermes_claude/api/routes
mkdir -p skills agents hooks scripts templates/projects systemd tests
touch src/hermes_claude/__init__.py
touch src/hermes_claude/mcp_servers/__init__.py
touch src/hermes_claude/mcp_servers/voice_stt/__init__.py
touch src/hermes_claude/mcp_servers/voice_tts/__init__.py
touch src/hermes_claude/mcp_servers/project_orchestrator/__init__.py
touch src/hermes_claude/mcp_servers/hermes_api/__init__.py
touch src/hermes_claude/api/__init__.py
touch src/hermes_claude/api/routes/__init__.py
touch src/hermes_claude/wizard/__init__.py
touch tests/conftest.py tests/__init__.py
```

- [ ] **Step 5: Verify install works locally**

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest --collect-only   # expect "no tests collected", no errors
```

- [ ] **Step 6: Commit**

```bash
git add .claude-plugin pyproject.toml README.md src tests
git commit -m "Plugin skeleton: pyproject, package layout, plugin.json"
```

---

### Task 5: Install whisper.cpp for ARM on the VPS

**Files:**
- Create (remote): `/opt/whisper.cpp/`

- [ ] **Step 1: Clone and build**

```bash
ssh oci-hermes
sudo install -d -o ubuntu -g ubuntu /opt
cd /opt
git clone https://github.com/ggml-org/whisper.cpp.git
cd whisper.cpp
make -j4
```
Build takes ~3-5 min on Ampere A1.

- [ ] **Step 2: Download large-v3-turbo model**

```bash
cd /opt/whisper.cpp
bash ./models/download-ggml-model.sh large-v3-turbo
ls -lh models/ggml-large-v3-turbo.bin   # expect ~1.5 GB
```

- [ ] **Step 3: Smoke test the binary**

```bash
cd /opt/whisper.cpp
# Use the included sample to verify
./build/bin/whisper-cli -m models/ggml-large-v3-turbo.bin \
    -f samples/jfk.wav -otxt -of /tmp/jfk_out
cat /tmp/jfk_out.txt   # expect "And so my fellow Americans..."
```

- [ ] **Step 4: No commit (binary install)**

---

### Task 6: Install Piper TTS for ARM on the VPS

**Files:**
- Create (remote): `/opt/piper/`

- [ ] **Step 1: Download piper ARM binary release**

```bash
ssh oci-hermes
cd /opt
sudo install -d -o ubuntu -g ubuntu /opt/piper
cd /opt/piper
wget https://github.com/rhasspy/piper/releases/latest/download/piper_linux_aarch64.tar.gz
tar xzf piper_linux_aarch64.tar.gz --strip-components=1
rm piper_linux_aarch64.tar.gz
./piper --help   # expect usage output
```

- [ ] **Step 2: Download `en_US-ryan-medium` voice**

```bash
cd /opt/piper
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx.json
```

- [ ] **Step 3: Smoke test**

```bash
echo "Hello from piper" | /opt/piper/piper \
    --model /opt/piper/en_US-ryan-medium.onnx \
    --output_file /tmp/test_tts.wav
ls -lh /tmp/test_tts.wav   # expect ~40-80 KB
file /tmp/test_tts.wav     # expect "RIFF (little-endian) data, WAVE audio"
```

- [ ] **Step 4: Confirm ffmpeg can convert to Opus**

```bash
ffmpeg -y -i /tmp/test_tts.wav -c:a libopus -b:a 48k /tmp/test_tts.opus
ls -lh /tmp/test_tts.opus   # expect 5-15 KB
```

- [ ] **Step 5: No commit (binary install)**

---

### Task 7: Write voice_stt MCP server tests

**Files:**
- Create: `tests/mcp_servers/test_voice_stt.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Write conftest.py with a wav fixture**

```python
# tests/conftest.py
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
    if Path(piper).exists():
        subprocess.run(
            [piper, "--model", "/opt/piper/en_US-ryan-medium.onnx",
             "--output_file", str(out)],
            input="This is a test recording.\n",
            text=True, check=True, capture_output=True,
        )
    else:
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 16000)
    return out
```

- [ ] **Step 2: Write test_voice_stt.py**

```python
# tests/mcp_servers/test_voice_stt.py
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
```

- [ ] **Step 3: Create __init__.py for tests/mcp_servers**

```bash
mkdir -p tests/mcp_servers
touch tests/mcp_servers/__init__.py
```

- [ ] **Step 4: Run tests to verify they fail (no impl yet)**

```bash
pytest tests/mcp_servers/test_voice_stt.py -v
```
Expected: ImportError on `transcribe_impl`.

- [ ] **Step 5: Commit failing tests**

```bash
git add tests/conftest.py tests/mcp_servers/
git commit -m "Add failing tests for voice_stt transcribe"
```

---

### Task 8: Implement voice_stt MCP server

**Files:**
- Create: `src/hermes_claude/mcp_servers/voice_stt/server.py`

- [ ] **Step 1: Write the implementation**

```python
# src/hermes_claude/mcp_servers/voice_stt/server.py
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
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
pytest tests/mcp_servers/test_voice_stt.py -v
```
Expected: PASS (or SKIP if whisper not installed locally; passes on VPS where it is installed).

- [ ] **Step 3: Hand-run the MCP server (smoke test)**

```bash
python -m hermes_claude.mcp_servers.voice_stt.server &
SERVER_PID=$!
sleep 1
kill $SERVER_PID 2>/dev/null
```
Expected: server starts (no traceback) and is killable.

- [ ] **Step 4: Commit**

```bash
git add src/hermes_claude/mcp_servers/voice_stt/
git commit -m "Implement voice_stt MCP server (whisper.cpp wrapper)"
```

---

### Task 9: Wire voice_stt into the plugin .mcp.json

**Files:**
- Create: `.mcp.json`

- [ ] **Step 1: Create plugin-level .mcp.json**

```json
{
  "mcpServers": {
    "voice-stt": {
      "type": "stdio",
      "command": "/opt/hermes-claude/.venv/bin/python",
      "args": ["-m", "hermes_claude.mcp_servers.voice_stt.server"],
      "env": {
        "HERMES_WHISPER_BIN": "/opt/whisper.cpp/build/bin/whisper-cli",
        "HERMES_WHISPER_MODEL": "/opt/whisper.cpp/models/ggml-large-v3-turbo.bin"
      },
      "alwaysLoad": true
    }
  }
}
```

- [ ] **Step 2: Smoke test (manual on VPS once deployed)**

Once you've rsynced the plugin to `/opt/hermes-claude` on the VPS, start a quick claude session:

```bash
cd /opt/hermes-claude
claude --plugin-dir . -p '@voice-stt list your tools' --output-format text
```
Expected: lists `transcribe` tool.

- [ ] **Step 3: Commit**

```bash
git add .mcp.json
git commit -m "Wire voice-stt into plugin mcp config"
```

---

### Task 10: Write voice_intake.sh hook script

**Files:**
- Create: `scripts/voice_intake.sh`

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# scripts/voice_intake.sh
#
# Invoked by UserPromptSubmit hook when meta.audio_path is present.
# Reads the hook event JSON on stdin, transcribes the audio via voice_stt MCP,
# and emits a JSON output that rewrites the user prompt to include the transcript.
#
# Hook protocol: https://code.claude.com/docs/en/hooks

set -euo pipefail

EVENT_JSON="$(cat)"
AUDIO_PATH="$(jq -r '.meta.audio_path // empty' <<<"$EVENT_JSON")"

if [[ -z "$AUDIO_PATH" || ! -f "$AUDIO_PATH" ]]; then
    # No audio meta or file missing — emit pass-through.
    jq -nc '{decision: "continue"}'
    exit 0
fi

# Use the voice_stt MCP via a one-shot python invocation.
TRANSCRIPT="$(
    /opt/hermes-claude/.venv/bin/python -c "
import json, sys
from hermes_claude.mcp_servers.voice_stt.server import transcribe_impl
r = transcribe_impl('$AUDIO_PATH', language='auto')
print(json.dumps(r))
" 2>/dev/null
)"

if [[ -z "$TRANSCRIPT" ]]; then
    jq -nc '{decision: "continue"}'
    exit 0
fi

TEXT="$(jq -r '.text' <<<"$TRANSCRIPT")"
LANG="$(jq -r '.language_detected' <<<"$TRANSCRIPT")"
DUR="$(jq -r '.duration_seconds' <<<"$TRANSCRIPT")"

ORIGINAL="$(jq -r '.user_prompt // .prompt // ""' <<<"$EVENT_JSON")"

# Rewrite prompt: keep original (channel often inserts placeholder),
# prepend a clear transcript marker so downstream skills can detect "voice in".
NEW_PROMPT=$(printf "[voice transcript · lang=%s · dur=%ss]\n%s\n\n(audio path: %s)" \
    "$LANG" "$DUR" "$TEXT" "$AUDIO_PATH")

jq -nc \
  --arg p "$NEW_PROMPT" \
  --arg ap "$AUDIO_PATH" \
  '{decision: "continue", user_prompt: $p, meta_inject: {voice_in: true, audio_path: $ap}}'
```

- [ ] **Step 2: Make executable**

```bash
chmod +x scripts/voice_intake.sh
```

- [ ] **Step 3: Unit-test with a fixture**

```bash
mkdir -p tests/scripts
cat > tests/scripts/test_voice_intake.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(dirname "$0")/../.."
EVENT='{"user_prompt":"[voice]","meta":{"audio_path":"/opt/whisper.cpp/samples/jfk.wav"}}'
RESULT="$(echo "$EVENT" | bash "$HERE/scripts/voice_intake.sh")"
echo "$RESULT" | jq -e '.user_prompt | contains("voice transcript")' >/dev/null
echo "$RESULT" | jq -e '.meta_inject.voice_in == true' >/dev/null
echo "OK"
EOF
chmod +x tests/scripts/test_voice_intake.sh
```

- [ ] **Step 4: Run the test (on VPS once deployed)**

Locally, hook test is `bash tests/scripts/test_voice_intake.sh` — expect `OK`.

- [ ] **Step 5: Commit**

```bash
git add scripts/voice_intake.sh tests/scripts/test_voice_intake.sh
git commit -m "Add voice_intake hook script with transcript rewrite"
```

---

### Task 11: Write hooks.json with UserPromptSubmit + PostToolUse + SessionStart

**Files:**
- Create: `hooks/hooks.json`
- Create: `scripts/session_start_context.sh`
- Create: `scripts/log_activity.sh`

- [ ] **Step 1: Write hooks.json**

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/opt/hermes-claude/scripts/session_start_context.sh"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "if": "event.meta.audio_path != null",
        "hooks": [
          {
            "type": "command",
            "command": "/opt/hermes-claude/scripts/voice_intake.sh"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "/opt/hermes-claude/scripts/log_activity.sh",
            "async": true
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 2: Write session_start_context.sh**

```bash
#!/usr/bin/env bash
# scripts/session_start_context.sh
#
# Injected into Claude's context at session start.
# Lists active projects + recent ~/Projects/llm/* commit activity.

set -euo pipefail

REGISTRY="/opt/hermes-claude/registry.sqlite"
PROJECTS_DIR="${HOME}/Projects/llm"

projects_block=""
if [[ -f "$REGISTRY" ]]; then
    projects_block="$(sqlite3 "$REGISTRY" "SELECT name || ' (' || type || ', ' || status || ')' FROM projects WHERE status != 'killed' ORDER BY last_activity DESC LIMIT 10;" 2>/dev/null || echo "(none)")"
fi

recent_block=""
if [[ -d "$PROJECTS_DIR" ]]; then
    recent_block="$(find "$PROJECTS_DIR" -maxdepth 2 -name ".git" -type d 2>/dev/null | head -8 | while read gitdir; do
        proj="$(basename "$(dirname "$gitdir")")"
        last="$(cd "$(dirname "$gitdir")" && git log -1 --format='%cr: %s' 2>/dev/null || echo 'no commits')"
        echo "- $proj — $last"
    done)"
fi

cat <<EOF
## Hermes-Claude session context (auto-injected)

**Active projects (from orchestrator registry):**
${projects_block:-(no active projects)}

**Recent activity in ~/Projects/llm/*:**
${recent_block:-(none)}
EOF
```

- [ ] **Step 3: Write log_activity.sh**

```bash
#!/usr/bin/env bash
# scripts/log_activity.sh
#
# Appends a JSON line to ~/.hermes-claude/activity.jsonl on every PostToolUse.
# Dashboard SSE tails this file.

set -euo pipefail

EVENT="$(cat)"
LOG_DIR="${HOME}/.hermes-claude"
LOG_FILE="${LOG_DIR}/activity.jsonl"

mkdir -p "$LOG_DIR"

TS="$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)"
TOOL="$(jq -r '.tool_name // "unknown"' <<<"$EVENT")"
SESSION="$(jq -r '.session_id // "unknown"' <<<"$EVENT")"
SUMMARY="$(jq -c '.tool_input // {}' <<<"$EVENT" | head -c 500)"
RESULT="$(jq -r '.tool_result_summary // ""' <<<"$EVENT" | head -c 500)"

jq -nc \
  --arg ts "$TS" \
  --arg t "$TOOL" \
  --arg s "$SESSION" \
  --argjson inp "$SUMMARY" \
  --arg r "$RESULT" \
  '{ts: $ts, tool: $t, session: $s, input_summary: $inp, result_summary: $r}' \
  >> "$LOG_FILE"

# Truncate file if it grows past 50 MB (basic rotation)
if [[ -f "$LOG_FILE" ]] && [[ $(stat -c%s "$LOG_FILE") -gt 52428800 ]]; then
    tail -n 10000 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi
```

- [ ] **Step 4: Make executable**

```bash
chmod +x scripts/session_start_context.sh scripts/log_activity.sh
```

- [ ] **Step 5: Commit**

```bash
git add hooks/hooks.json scripts/session_start_context.sh scripts/log_activity.sh
git commit -m "Add hooks: SessionStart context, UserPromptSubmit voice intake, PostToolUse activity log"
```

---

### Task 12: Install the official Telegram channel plugin on the VPS

**Files:**
- Modify (remote): `~/.claude.json` (channel plugin install)

- [ ] **Step 1: Create a Telegram bot via @BotFather**

In Telegram, message `@BotFather`:
- `/newbot`
- Name: `Hermes Claude (Mayank)`
- Username: `hermes_mayank_bot` (or whatever's available)

Save the bot token printed (starts with `123456:ABC...`).

- [ ] **Step 2: Install the channel plugin on the VPS**

```bash
ssh oci-hermes
export $(cat /etc/hermes-claude/secrets.env)
claude --plugin install telegram@claude-plugins-official
# Or interactively:
claude
# /plugin install telegram@claude-plugins-official
# /reload-plugins
# /quit
```

- [ ] **Step 3: Configure with the bot token**

```bash
claude
# /telegram:configure 123456:ABC...
# /quit
```

- [ ] **Step 4: Pair your Telegram account**

Start a chat with your bot in Telegram (or add to a private group), send any message. The bot replies with a pairing code (e.g. `ABCDE`). On the VPS:

```bash
claude
# /telegram:access pair ABCDE
# /telegram:access policy allowlist
# /quit
```

- [ ] **Step 5: No commit (config on VPS)**

The channel install is per-VPS config — captured later in the setup wizard.

---

### Task 13: rsync the plugin to the VPS and prepare /opt/hermes-claude/.venv

**Files:**
- Create: `scripts/deploy.sh`

- [ ] **Step 1: Write deploy.sh**

```bash
#!/usr/bin/env bash
# scripts/deploy.sh — sync hermes-claude repo to OCI VPS and install deps.

set -euo pipefail

HOST="${HERMES_HOST:-oci-hermes}"
REMOTE="/opt/hermes-claude"

ssh "$HOST" "sudo install -d -o ubuntu -g ubuntu $REMOTE"

rsync -avh --delete \
    --exclude '.git' \
    --exclude '.venv' \
    --exclude '__pycache__' \
    --exclude 'node_modules' \
    --exclude '.next' \
    --exclude 'frontend/.next' \
    --exclude 'tests/' \
    --exclude '*.sqlite' \
    ./ "$HOST:$REMOTE/"

ssh "$HOST" bash <<EOSSH
set -euo pipefail
cd $REMOTE
if [[ ! -d .venv ]]; then
    python3.12 -m venv .venv
fi
.venv/bin/pip install -U pip
.venv/bin/pip install -e ".[dev]"
chmod +x scripts/*.sh
EOSSH

echo "✓ Deployed to $HOST:$REMOTE"
```

- [ ] **Step 2: Make executable and run**

```bash
chmod +x scripts/deploy.sh
./scripts/deploy.sh
```
Expected: rsync transfers files, venv created on VPS, deps install, ends with `✓ Deployed`.

- [ ] **Step 3: Verify on VPS**

```bash
ssh oci-hermes 'ls /opt/hermes-claude/.venv/bin/hermes-voice-stt && /opt/hermes-claude/.venv/bin/python -c "from hermes_claude.mcp_servers.voice_stt.server import transcribe_impl; print(\"OK\")"'
```
Expected: prints `OK`.

- [ ] **Step 4: Commit**

```bash
git add scripts/deploy.sh
git commit -m "Add deploy.sh for rsync + venv install on OCI VPS"
```

---

### Task 14: Write the persistent channel systemd unit

**Files:**
- Create: `systemd/hermes-claude-channel.service`

- [ ] **Step 1: Write the unit**

```ini
# systemd/hermes-claude-channel.service
[Unit]
Description=Hermes-Claude persistent Telegram channel session
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/hermes-claude
EnvironmentFile=/etc/hermes-claude/secrets.env
Environment=PATH=/opt/hermes-claude/.venv/bin:/usr/local/bin:/usr/bin:/bin
Environment=CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
ExecStart=/usr/bin/tmux new-session -d -s hermes 'exec /usr/bin/claude --channels plugin:telegram@claude-plugins-official --plugin-dir /opt/hermes-claude --add-dir /home/ubuntu/hermes-work --permission-mode default'
ExecStop=/usr/bin/tmux kill-session -t hermes
Restart=always
RestartSec=10
StandardOutput=append:/var/log/hermes-claude/channel.log
StandardError=append:/var/log/hermes-claude/channel.err.log

[Install]
WantedBy=multi-user.target
```

Wait — `Type=simple` with `tmux new-session -d` will exit immediately because `-d` detaches. Fix below.

- [ ] **Step 2: Fix to use Type=forking + tmux**

Replace the `[Service]` section with:

```ini
[Service]
Type=forking
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/hermes-claude
EnvironmentFile=/etc/hermes-claude/secrets.env
Environment=PATH=/opt/hermes-claude/.venv/bin:/usr/local/bin:/usr/bin:/bin
Environment=CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
ExecStartPre=/usr/bin/install -d -o ubuntu -g ubuntu /var/log/hermes-claude
ExecStartPre=/bin/bash -c '/usr/bin/tmux kill-session -t hermes 2>/dev/null || true'
ExecStart=/usr/bin/tmux new-session -d -s hermes 'cd /opt/hermes-claude && /usr/bin/claude --channels plugin:telegram@claude-plugins-official --plugin-dir /opt/hermes-claude --add-dir /home/ubuntu/hermes-work --permission-mode default 2>&1 | tee -a /var/log/hermes-claude/channel.log'
ExecStop=/usr/bin/tmux kill-session -t hermes
Restart=always
RestartSec=10
```

- [ ] **Step 3: Install on the VPS**

```bash
scp systemd/hermes-claude-channel.service oci-hermes:/tmp/
ssh oci-hermes sudo install -m 644 /tmp/hermes-claude-channel.service /etc/systemd/system/
ssh oci-hermes sudo systemctl daemon-reload
ssh oci-hermes sudo install -d -o ubuntu -g ubuntu /home/ubuntu/hermes-work
ssh oci-hermes sudo install -d -o ubuntu -g ubuntu /var/log/hermes-claude
```

- [ ] **Step 4: Start and verify**

```bash
ssh oci-hermes sudo systemctl enable --now hermes-claude-channel.service
sleep 8
ssh oci-hermes sudo systemctl status hermes-claude-channel.service --no-pager | head -20
ssh oci-hermes tmux capture-pane -t hermes -p | tail -30
```
Expected: status `active (running)`; tmux capture shows Claude Code session running.

- [ ] **Step 5: Commit**

```bash
git add systemd/hermes-claude-channel.service
git commit -m "Add persistent channel systemd unit (tmux-supervised claude session)"
```

---

### Task 15: Smoke test the end-to-end voice flow

**Files:** none — verification only

- [ ] **Step 1: Send a text message to the bot**

From your Telegram, message the bot: "Reply with the word PONG."
Expected: bot replies "PONG" within ~5-10s.

- [ ] **Step 2: Send a voice memo**

Hold the mic in Telegram and say: "Repeat back the word ELEPHANT."
Expected: bot replies with text containing "ELEPHANT" within ~10-20s.

- [ ] **Step 3: Check the channel log shows the transcript**

```bash
ssh oci-hermes 'tail -100 /var/log/hermes-claude/channel.log | grep -i transcript'
```
Expected: at least one line showing the voice intake hook fired with a transcript.

- [ ] **Step 4: Restart and confirm survival**

```bash
ssh oci-hermes sudo systemctl restart hermes-claude-channel.service
sleep 10
# Send another Telegram message; expect a reply.
```

- [ ] **Step 5: Tag the milestone**

```bash
git tag week-1-complete
git push origin week-1-complete   # only after a remote is set up
```

(Week 1 done.)

---

## Week 2 — Voice TTS + Codex + Orchestrator

Goal of Week 2: voice round-trip works (mirror modality). "Build me a web scraper" spawns a project-lead in its own cwd, returns a Remote Control URL.

### Task 16: Write voice_tts MCP server tests

**Files:**
- Create: `tests/mcp_servers/test_voice_tts.py`

- [ ] **Step 1: Write the tests**

```python
# tests/mcp_servers/test_voice_tts.py
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from hermes_claude.mcp_servers.voice_tts.server import synthesize_impl


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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/mcp_servers/test_voice_tts.py -v
```
Expected: ImportError on `synthesize_impl`.

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/mcp_servers/test_voice_tts.py
git commit -m "Add failing tests for voice_tts synthesize"
```

---

### Task 17: Implement voice_tts MCP server

**Files:**
- Create: `src/hermes_claude/mcp_servers/voice_tts/server.py`

- [ ] **Step 1: Write the implementation**

```python
# src/hermes_claude/mcp_servers/voice_tts/server.py
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

    subprocess.run(
        [binary, "--model", model, "--output_file", str(wav_path),
         "--length_scale", "1.0", "--sentence_silence", "0.3"],
        input=text, text=True, capture_output=True, check=True, timeout=60,
    )

    # Convert WAV → OGG/Opus for Telegram voice notes.
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav_path),
         "-c:a", "libopus", "-b:a", "48k", str(opus_path)],
        capture_output=True, check=True,
    )

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
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
pytest tests/mcp_servers/test_voice_tts.py -v
```
Expected: PASS (or SKIP locally; passes on VPS).

- [ ] **Step 3: Add to .mcp.json**

Edit `.mcp.json` to add the `voice-tts` server:

```json
{
  "mcpServers": {
    "voice-stt": {
      "type": "stdio",
      "command": "/opt/hermes-claude/.venv/bin/python",
      "args": ["-m", "hermes_claude.mcp_servers.voice_stt.server"],
      "env": {
        "HERMES_WHISPER_BIN": "/opt/whisper.cpp/build/bin/whisper-cli",
        "HERMES_WHISPER_MODEL": "/opt/whisper.cpp/models/ggml-large-v3-turbo.bin"
      },
      "alwaysLoad": true
    },
    "voice-tts": {
      "type": "stdio",
      "command": "/opt/hermes-claude/.venv/bin/python",
      "args": ["-m", "hermes_claude.mcp_servers.voice_tts.server"],
      "env": {
        "HERMES_PIPER_BIN": "/opt/piper/piper",
        "HERMES_PIPER_DEFAULT_VOICE": "/opt/piper/en_US-ryan-medium.onnx"
      },
      "alwaysLoad": true
    }
  }
}
```

- [ ] **Step 4: Commit**

```bash
git add src/hermes_claude/mcp_servers/voice_tts/ .mcp.json
git commit -m "Implement voice_tts MCP server (piper + ffmpeg → opus)"
```

---

### Task 18: Write respond-with-voice skill

**Files:**
- Create: `skills/respond-with-voice/SKILL.md`

- [ ] **Step 1: Write the skill**

````markdown
---
name: respond-with-voice
description: |
  Reply to the user with a voice note instead of (or in addition to) text.
  Use when: (a) the incoming user message had `voice_in: true` meta (mirror modality),
  (b) the user explicitly asked for a voice reply, or (c) the reply is short plain
  language that is voice-friendly. DO NOT use when the reply contains code blocks,
  tables, URLs the user needs to copy, or anything longer than ~3 sentences.
allowed-tools: mcp__voice-tts__synthesize, mcp__telegram__reply_voice
---

# respond-with-voice

When invoked, you have a `reply_text` you want to deliver as audio.

1. Call `mcp__voice-tts__synthesize` with `text=<reply_text>`. This returns
   `{audio_path, duration_seconds, voice_used}`.

2. Call `mcp__telegram__reply_voice` (provided by the telegram channel plugin)
   with `audio_path=<the path from step 1>`. The channel uploads it as a
   Telegram voice note in the current chat.

3. If the reply ALSO contains a code block, a long table, or a URL the user
   needs to copy/click, additionally send those parts as text via the channel's
   normal text reply so the user has copy-pasteable material.

4. Confirm in plain language (one short sentence) that you've sent the voice.
   Do NOT also TTS this confirmation — it would create an echo loop.

## When not to call this skill

- Replies containing more than ~3 sentences of dense info — use text
- Code blocks, JSON, tables — use text
- Error messages with stack traces — use text
- When the user has explicitly disabled voice in this thread (check
  `/admin/conversations` for thread preferences)
````

- [ ] **Step 2: Commit**

```bash
git add skills/respond-with-voice/
git commit -m "Add respond-with-voice skill"
```

---

### Task 19: Write voice-action skill

**Files:**
- Create: `skills/voice-action/SKILL.md`

- [ ] **Step 1: Write the skill**

````markdown
---
name: voice-action
description: |
  Auto-loaded when the user's incoming message has `voice_in: true` meta.
  Interprets the transcribed user intent and routes to the appropriate skill,
  team, or tool call. Always prefers acting over asking a clarifying question
  when the intent is reasonably clear from voice.
---

# voice-action

The user spoke a request. The transcript is already in your context.

## Routing heuristics

1. **"Build / make / set up / create me X"** → invoke `spawn-project` with
   inferred type (web-scraper / llm-app / server-app / agentic-coding).

2. **"What's the status of / how's / what's running"** → invoke `list-projects`
   or `project-status` as appropriate.

3. **"Tell <project-name> to / ask <project-name> about"** → invoke
   `message-project` with the named target.

4. **"Draw / render / generate an image of"** → invoke `codex-image-gen`.

5. **"Schedule / every <time>, do"** → invoke `schedule-routine`.

6. **"What are you working on / what am I working on"** → invoke
   `portfolio-status`.

7. **"How much quota / credit have I used"** → invoke `usage-report`.

8. **Open-ended question or chat** → answer directly; default to voice reply
   via `respond-with-voice` if the answer is short.

## Reply modality default

Voice in → voice out, UNLESS the reply would be poorly suited to audio (code,
tables, copy-paste-needed URLs). In those cases, send TEXT and mention "I'll
send this as text since it has code/links."
````

- [ ] **Step 2: Commit**

```bash
git add skills/voice-action/
git commit -m "Add voice-action skill (voice intent routing)"
```

---

### Task 20: Write codex-image-gen skill

**Files:**
- Create: `skills/codex-image-gen/SKILL.md`
- Create (remote): `/opt/codex/codex` installed via user's existing Codex subscription

- [ ] **Step 1: Verify Codex CLI is installed on VPS**

```bash
ssh oci-hermes 'which codex && codex --version'
```
If not present: install per user's existing Codex CLI install instructions on M5 Max (the user has the subscription; the CLI is `npm i -g @openai/codex` or similar — check user's actual install path).

- [ ] **Step 2: Write the skill**

````markdown
---
name: codex-image-gen
description: |
  Generate or edit an image by delegating to the user's Codex CLI subscription.
  Use when the user requests "draw / render / generate / create / sketch /
  design an image of X" or similar. Returns a local path to the generated PNG
  which the Telegram channel will upload as an image.
allowed-tools: Bash(codex *), Read
---

# codex-image-gen

When invoked, you have a `prompt` describing the desired image.

## Process

1. Choose an output path: `/tmp/codex_img_<short-uuid>.png`.

2. Invoke Codex's image generation:

```bash
codex --image --prompt "<the user's image prompt, sanitized>" --output <path>
```

3. Confirm the file exists with `ls -lh <path>`. Expected size 100 KB – 4 MB.

4. Reply to the user with the local path. The Telegram channel's media
   handling will upload the file as an image (NOT a voice note even if the
   request came in via voice).

## Notes

- Codex uses the user's separate ChatGPT subscription — does NOT count against
  Claude Max credits.
- For style or aspect-ratio control, pass them in the prompt (e.g. "16:9
  cinematic, dramatic lighting, ...").
- If Codex fails (e.g. content policy), reply with an explanation and offer
  to refine the prompt.
````

- [ ] **Step 3: Commit**

```bash
git add skills/codex-image-gen/
git commit -m "Add codex-image-gen skill (delegates to Codex CLI)"
```

---

### Task 21: Define project_orchestrator registry schema

**Files:**
- Create: `src/hermes_claude/mcp_servers/project_orchestrator/registry.py`
- Create: `tests/mcp_servers/test_project_orchestrator_registry.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/mcp_servers/test_project_orchestrator_registry.py
from __future__ import annotations

import time
from pathlib import Path

import pytest

from hermes_claude.mcp_servers.project_orchestrator.registry import Registry


def test_register_then_get(tmp_path: Path) -> None:
    r = Registry(tmp_path / "reg.sqlite")
    r.register("foo", agent_id="a-1", type_="web-scraper",
               cwd="/home/ubuntu/projects/foo", rc_url="https://x")
    p = r.get("foo")
    assert p is not None
    assert p["name"] == "foo"
    assert p["agent_id"] == "a-1"
    assert p["type"] == "web-scraper"
    assert p["status"] == "active"


def test_list_active_excludes_killed(tmp_path: Path) -> None:
    r = Registry(tmp_path / "reg.sqlite")
    r.register("a", agent_id="a-1", type_="llm-app", cwd="/x", rc_url="https://a")
    r.register("b", agent_id="a-2", type_="llm-app", cwd="/y", rc_url="https://b")
    r.set_status("a", "killed")
    actives = r.list_active()
    assert {p["name"] for p in actives} == {"b"}


def test_touch_updates_last_activity(tmp_path: Path) -> None:
    r = Registry(tmp_path / "reg.sqlite")
    r.register("x", agent_id="a-1", type_="custom", cwd="/x", rc_url="https://x")
    before = r.get("x")["last_activity"]
    time.sleep(0.05)
    r.touch("x")
    after = r.get("x")["last_activity"]
    assert after > before


def test_idle_for_seconds_increases(tmp_path: Path) -> None:
    r = Registry(tmp_path / "reg.sqlite")
    r.register("x", agent_id="a-1", type_="custom", cwd="/x", rc_url="https://x")
    time.sleep(0.5)
    assert r.idle_for("x") >= 0.4
```

- [ ] **Step 2: Run tests; expect ImportError**

```bash
pytest tests/mcp_servers/test_project_orchestrator_registry.py -v
```

- [ ] **Step 3: Implement Registry**

```python
# src/hermes_claude/mcp_servers/project_orchestrator/registry.py
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    name           TEXT PRIMARY KEY,
    agent_id       TEXT NOT NULL,
    type           TEXT NOT NULL,
    cwd            TEXT NOT NULL,
    rc_url         TEXT,
    status         TEXT NOT NULL DEFAULT 'active',
    permission_mode TEXT NOT NULL DEFAULT 'acceptEdits',
    spawned_at     REAL NOT NULL,
    last_activity  REAL NOT NULL,
    brief          TEXT
);

CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_last_activity ON projects(last_activity);
"""


class Registry:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)

    def register(
        self,
        name: str,
        *,
        agent_id: str,
        type_: str,
        cwd: str,
        rc_url: str | None,
        permission_mode: str = "acceptEdits",
        brief: str | None = None,
    ) -> None:
        now = time.time()
        self._conn.execute(
            """
            INSERT INTO projects(name, agent_id, type, cwd, rc_url, status,
                                 permission_mode, spawned_at, last_activity, brief)
            VALUES(?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                agent_id=excluded.agent_id, type=excluded.type, cwd=excluded.cwd,
                rc_url=excluded.rc_url, status='active',
                permission_mode=excluded.permission_mode,
                last_activity=excluded.last_activity, brief=excluded.brief
            """,
            (name, agent_id, type_, cwd, rc_url, permission_mode, now, now, brief),
        )

    def get(self, name: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM projects WHERE name = ?", (name,)
        ).fetchone()
        return dict(row) if row else None

    def list_active(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM projects WHERE status = 'active' ORDER BY last_activity DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def list_all(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM projects ORDER BY last_activity DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def set_status(self, name: str, status: str) -> None:
        self._conn.execute(
            "UPDATE projects SET status = ?, last_activity = ? WHERE name = ?",
            (status, time.time(), name),
        )

    def touch(self, name: str) -> None:
        self._conn.execute(
            "UPDATE projects SET last_activity = ? WHERE name = ?",
            (time.time(), name),
        )

    def idle_for(self, name: str) -> float:
        row = self._conn.execute(
            "SELECT last_activity FROM projects WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            return 0.0
        return max(0.0, time.time() - float(row["last_activity"]))

    def delete(self, name: str) -> None:
        self._conn.execute("DELETE FROM projects WHERE name = ?", (name,))

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/mcp_servers/test_project_orchestrator_registry.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/hermes_claude/mcp_servers/project_orchestrator/registry.py \
        tests/mcp_servers/test_project_orchestrator_registry.py
git commit -m "Add SQLite Registry for project_orchestrator (passes tests)"
```

---

### Task 22: Define project templates

**Files:**
- Create: `templates/projects/web-scraper.json`
- Create: `templates/projects/llm-app.json`
- Create: `templates/projects/server-app.json`
- Create: `templates/projects/agentic-coding.json`
- Create: `templates/projects/custom.json`
- Create: `src/hermes_claude/mcp_servers/project_orchestrator/templates.py`
- Create: `tests/mcp_servers/test_orchestrator_templates.py`

- [ ] **Step 1: Write tests**

```python
# tests/mcp_servers/test_orchestrator_templates.py
from __future__ import annotations

import pytest

from hermes_claude.mcp_servers.project_orchestrator.templates import (
    load_template, list_template_names, TemplateNotFound
)


def test_list_includes_all_five() -> None:
    names = set(list_template_names())
    assert names >= {"web-scraper", "llm-app", "server-app",
                     "agentic-coding", "custom"}


def test_load_web_scraper_has_three_teammates() -> None:
    t = load_template("web-scraper")
    assert len(t["teammates"]) == 3
    assert {m["name"] for m in t["teammates"]} == {
        "database-engineer", "backend-engineer", "playwright-engineer"
    }


def test_load_unknown_raises() -> None:
    with pytest.raises(TemplateNotFound):
        load_template("definitely-not-real")
```

- [ ] **Step 2: Run tests; expect failures**

```bash
pytest tests/mcp_servers/test_orchestrator_templates.py -v
```

- [ ] **Step 3: Write the five template JSONs**

```bash
cat > templates/projects/web-scraper.json <<'EOF'
{
  "type": "web-scraper",
  "cwd_root": "~/projects",
  "permission_mode": "acceptEdits",
  "default_brief": "You are the lead of a web scraper project. Use Playwright for browser automation, design a state machine for change detection, and persist state in SQLite. Coordinate with your teammates database-engineer, backend-engineer, and playwright-engineer via SendMessage.",
  "teammates": [
    {"name": "database-engineer", "agent": "general-purpose", "model": "sonnet"},
    {"name": "backend-engineer", "agent": "general-purpose", "model": "sonnet"},
    {"name": "playwright-engineer", "agent": "general-purpose", "model": "sonnet"}
  ],
  "mcp_servers": ["filesystem", "web-fetch", "playwright-mcp"],
  "skills": ["bs-scraping", "async-helpers"]
}
EOF

cat > templates/projects/llm-app.json <<'EOF'
{
  "type": "llm-app",
  "cwd_root": "~/projects",
  "permission_mode": "acceptEdits",
  "default_brief": "You are the lead of an LLM-powered app project. Design model interactions, evaluations, and the UI. Coordinate with your teammates model-eval-engineer, prompt-engineer, and ui-engineer via SendMessage.",
  "teammates": [
    {"name": "model-eval-engineer", "agent": "general-purpose", "model": "sonnet"},
    {"name": "prompt-engineer", "agent": "general-purpose", "model": "sonnet"},
    {"name": "ui-engineer", "agent": "general-purpose", "model": "sonnet"}
  ],
  "mcp_servers": ["filesystem", "web-fetch", "huggingface-mcp"],
  "skills": ["claude-api", "ai-sdk"]
}
EOF

cat > templates/projects/server-app.json <<'EOF'
{
  "type": "server-app",
  "cwd_root": "~/projects",
  "permission_mode": "acceptEdits",
  "default_brief": "You are the lead of a backend service project. Coordinate API design, persistence, and deployment with your teammates api-engineer, db-engineer, and ops-engineer via SendMessage.",
  "teammates": [
    {"name": "api-engineer", "agent": "general-purpose", "model": "sonnet"},
    {"name": "db-engineer", "agent": "general-purpose", "model": "sonnet"},
    {"name": "ops-engineer", "agent": "general-purpose", "model": "sonnet"}
  ],
  "mcp_servers": ["filesystem", "postgres-mcp", "docker-mcp"],
  "skills": ["server-patterns", "nextjs"]
}
EOF

cat > templates/projects/agentic-coding.json <<'EOF'
{
  "type": "agentic-coding",
  "cwd_root": "~/projects",
  "permission_mode": "acceptEdits",
  "default_brief": "You are the lead of an agentic coding session. Spawn focused subagents (Agent tool) for transient parallel work; no persistent team needed by default.",
  "teammates": [],
  "mcp_servers": ["filesystem", "web-fetch"],
  "skills": ["superpowers:brainstorming", "superpowers:writing-plans", "superpowers:executing-plans"]
}
EOF

cat > templates/projects/custom.json <<'EOF'
{
  "type": "custom",
  "cwd_root": "~/projects",
  "permission_mode": "acceptEdits",
  "default_brief": "You are the lead of a custom project. The user will brief you with specifics.",
  "teammates": [],
  "mcp_servers": ["filesystem"],
  "skills": []
}
EOF
```

- [ ] **Step 4: Implement templates.py**

```python
# src/hermes_claude/mcp_servers/project_orchestrator/templates.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "templates" / "projects"


class TemplateNotFound(Exception):
    pass


def list_template_names() -> list[str]:
    if not TEMPLATES_DIR.exists():
        return []
    return sorted(p.stem for p in TEMPLATES_DIR.glob("*.json"))


def load_template(name: str) -> dict[str, Any]:
    path = TEMPLATES_DIR / f"{name}.json"
    if not path.exists():
        raise TemplateNotFound(f"no template named {name!r} in {TEMPLATES_DIR}")
    return json.loads(path.read_text())
```

- [ ] **Step 5: Run tests to verify pass**

```bash
pytest tests/mcp_servers/test_orchestrator_templates.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add templates/projects/ src/hermes_claude/mcp_servers/project_orchestrator/templates.py \
        tests/mcp_servers/test_orchestrator_templates.py
git commit -m "Add 5 project templates + loader (passes tests)"
```

---

### Task 23: Implement project spawner

**Files:**
- Create: `src/hermes_claude/mcp_servers/project_orchestrator/spawner.py`
- Create: `tests/mcp_servers/test_orchestrator_spawner.py`

- [ ] **Step 1: Write failing tests (uses subprocess mocking)**

```python
# tests/mcp_servers/test_orchestrator_spawner.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from hermes_claude.mcp_servers.project_orchestrator.spawner import (
    spawn_background_lead, BriefTooLong
)


def test_spawn_calls_claude_bg_with_expected_args(tmp_path: Path) -> None:
    cwd = tmp_path / "my-project"
    cwd.mkdir()
    fake = MagicMock()
    fake.stdout = '{"agent_id":"a-abc","rc_url":"https://claude.ai/x"}\n'
    fake.returncode = 0
    with patch("subprocess.run", return_value=fake) as run:
        result = spawn_background_lead(
            name="my-project", brief="Build it.", cwd=cwd,
            permission_mode="acceptEdits",
        )
    args = run.call_args[0][0]
    assert "claude" in args[0] or args[0] == "claude"
    assert "--bg" in args
    assert "--name" in args and "my-project" in args
    assert "--add-dir" in args and str(cwd) in args
    assert "--permission-mode" in args and "acceptEdits" in args
    assert result["agent_id"] == "a-abc"
    assert result["rc_url"].startswith("https://")


def test_spawn_rejects_long_brief(tmp_path: Path) -> None:
    with pytest.raises(BriefTooLong):
        spawn_background_lead(
            name="big", brief="x" * 200_000, cwd=tmp_path,
            permission_mode="acceptEdits",
        )
```

- [ ] **Step 2: Run; expect fail**

```bash
pytest tests/mcp_servers/test_orchestrator_spawner.py -v
```

- [ ] **Step 3: Implement spawner.py**

```python
# src/hermes_claude/mcp_servers/project_orchestrator/spawner.py
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path


CLAUDE_BIN = os.environ.get("HERMES_CLAUDE_BIN", "claude")
MAX_BRIEF_CHARS = 100_000  # safety: keep briefs reasonable
NAME_RX = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


class BriefTooLong(Exception):
    pass


class InvalidProjectName(Exception):
    pass


def _claude() -> str:
    if Path(CLAUDE_BIN).exists():
        return CLAUDE_BIN
    found = shutil.which(CLAUDE_BIN)
    if found is None:
        raise RuntimeError(f"claude binary not found ({CLAUDE_BIN})")
    return found


def spawn_background_lead(
    *,
    name: str,
    brief: str,
    cwd: Path,
    permission_mode: str,
    extra_args: list[str] | None = None,
) -> dict:
    if not NAME_RX.match(name):
        raise InvalidProjectName(
            f"project name must match {NAME_RX.pattern}, got {name!r}"
        )
    if len(brief) > MAX_BRIEF_CHARS:
        raise BriefTooLong(f"brief is {len(brief)} chars (max {MAX_BRIEF_CHARS})")
    cwd.mkdir(parents=True, exist_ok=True)

    cmd: list[str] = [
        _claude(), "--bg",
        "--name", name,
        "--add-dir", str(cwd),
        "--permission-mode", permission_mode,
        "--output-format", "json",
    ]
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(brief)

    result = subprocess.run(
        cmd, capture_output=True, text=True, check=True, timeout=60,
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    return {
        "agent_id": payload.get("agent_id") or payload.get("session_id"),
        "rc_url": payload.get("rc_url") or payload.get("claude_code_session_url", ""),
        "cwd": str(cwd),
    }
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/mcp_servers/test_orchestrator_spawner.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/hermes_claude/mcp_servers/project_orchestrator/spawner.py \
        tests/mcp_servers/test_orchestrator_spawner.py
git commit -m "Implement spawn_background_lead with validation (passes tests)"
```

---

### Task 24: Implement project_orchestrator MCP server

**Files:**
- Create: `src/hermes_claude/mcp_servers/project_orchestrator/server.py`
- Create: `tests/mcp_servers/test_project_orchestrator.py`

- [ ] **Step 1: Write tool-level integration tests with mocks**

```python
# tests/mcp_servers/test_project_orchestrator.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from hermes_claude.mcp_servers.project_orchestrator import server as orch


@pytest.fixture(autouse=True)
def _isolate_registry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_ORCH_DB", str(tmp_path / "reg.sqlite"))
    monkeypatch.setenv("HERMES_PROJECTS_ROOT", str(tmp_path / "projects"))
    orch._reset_singletons_for_tests()


def test_spawn_project_registers_and_returns_url() -> None:
    fake = MagicMock(returncode=0,
                     stdout='{"agent_id":"a-1","rc_url":"https://r.example/a-1"}\n')
    with patch("subprocess.run", return_value=fake):
        r = orch.spawn_project_impl(
            name="alpha", type_="web-scraper",
            brief="Build a scraper.", permission_mode="acceptEdits",
        )
    assert r["agent_id"] == "a-1"
    assert r["rc_url"] == "https://r.example/a-1"
    listed = orch.list_projects_impl()
    assert any(p["name"] == "alpha" for p in listed)


def test_kill_project_marks_killed() -> None:
    fake = MagicMock(returncode=0,
                     stdout='{"agent_id":"a-2","rc_url":"https://r/a-2"}\n')
    with patch("subprocess.run", return_value=fake):
        orch.spawn_project_impl(name="beta", type_="custom",
                                brief="Test.", permission_mode="default")
    r = orch.kill_project_impl(name="beta", archive=True)
    assert r["killed_at"] is not None
    assert all(p["name"] != "beta" for p in orch.list_projects_impl())


def test_get_status_returns_idle_for() -> None:
    fake = MagicMock(returncode=0,
                     stdout='{"agent_id":"a-3","rc_url":"https://r/a-3"}\n')
    with patch("subprocess.run", return_value=fake):
        orch.spawn_project_impl(name="gamma", type_="custom",
                                brief="x", permission_mode="default")
    s = orch.get_status_impl("gamma")
    assert s["name"] == "gamma"
    assert "idle_for_seconds" in s


def test_spawn_unknown_type_falls_back_to_custom() -> None:
    fake = MagicMock(returncode=0,
                     stdout='{"agent_id":"a-4","rc_url":"https://r/a-4"}\n')
    with patch("subprocess.run", return_value=fake):
        r = orch.spawn_project_impl(name="d", type_="not-a-type",
                                    brief="x", permission_mode="default")
    assert r["agent_id"] == "a-4"
    p = orch.get_status_impl("d")
    assert p["type"] in {"custom", "not-a-type"}
```

- [ ] **Step 2: Run; expect ImportError**

```bash
pytest tests/mcp_servers/test_project_orchestrator.py -v
```

- [ ] **Step 3: Implement server.py**

```python
# src/hermes_claude/mcp_servers/project_orchestrator/server.py
from __future__ import annotations

import os
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .registry import Registry
from .spawner import spawn_background_lead, InvalidProjectName, BriefTooLong
from .templates import load_template, list_template_names, TemplateNotFound


DB_PATH = os.environ.get("HERMES_ORCH_DB", "/opt/hermes-claude/registry.sqlite")
PROJECTS_ROOT = os.environ.get("HERMES_PROJECTS_ROOT", "/home/ubuntu/projects")
MAX_CONCURRENT = int(os.environ.get("HERMES_MAX_CONCURRENT_PROJECTS", "6"))


_registry: Registry | None = None


def _reg() -> Registry:
    global _registry
    if _registry is None:
        _registry = Registry(DB_PATH)
    return _registry


def _reset_singletons_for_tests() -> None:
    global _registry
    _registry = None


def _resolve_template(type_: str) -> dict:
    try:
        return load_template(type_)
    except TemplateNotFound:
        return load_template("custom")


def spawn_project_impl(
    *, name: str, type_: str, brief: str, permission_mode: str = "acceptEdits"
) -> dict:
    active = _reg().list_active()
    if len(active) >= MAX_CONCURRENT:
        raise RuntimeError(
            f"already at concurrency cap ({MAX_CONCURRENT}); "
            f"kill one project first. Active: {[p['name'] for p in active]}"
        )
    tmpl = _resolve_template(type_)
    cwd = Path(PROJECTS_ROOT) / name
    composed_brief = tmpl.get("default_brief", "") + "\n\n" + brief

    spawn = spawn_background_lead(
        name=name, brief=composed_brief, cwd=cwd,
        permission_mode=permission_mode or tmpl.get("permission_mode", "acceptEdits"),
    )
    _reg().register(
        name, agent_id=spawn["agent_id"], type_=tmpl["type"],
        cwd=str(cwd), rc_url=spawn.get("rc_url"),
        permission_mode=permission_mode, brief=composed_brief,
    )
    return {
        "agent_id": spawn["agent_id"],
        "rc_url": spawn.get("rc_url", ""),
        "cwd": str(cwd),
        "type": tmpl["type"],
    }


def list_projects_impl() -> list[dict]:
    rows = _reg().list_active()
    now = time.time()
    return [
        {
            "name": r["name"], "agent_id": r["agent_id"], "type": r["type"],
            "cwd": r["cwd"], "rc_url": r["rc_url"], "status": r["status"],
            "spawned_at": r["spawned_at"],
            "idle_for_seconds": max(0.0, now - float(r["last_activity"])),
        }
        for r in rows
    ]


def send_to_project_impl(*, name: str, message: str) -> dict:
    # The actual SendMessage call happens from Claude's tool layer; this MCP
    # tool just touches the registry and returns the agent_id so the LLM
    # can call SendMessage(to=<agent_id>) directly.
    p = _reg().get(name)
    if not p:
        raise RuntimeError(f"no project named {name!r}")
    _reg().touch(name)
    return {"name": name, "agent_id": p["agent_id"], "sent_at": time.time()}


def kill_project_impl(*, name: str, archive: bool = True) -> dict:
    p = _reg().get(name)
    if not p:
        raise RuntimeError(f"no project named {name!r}")
    _reg().set_status(name, "killed")
    if archive:
        # Archive happens out-of-band via reaper; this just marks it.
        pass
    return {"name": name, "killed_at": time.time()}


def get_status_impl(name: str) -> dict:
    p = _reg().get(name)
    if not p:
        raise RuntimeError(f"no project named {name!r}")
    return {
        "name": p["name"], "agent_id": p["agent_id"], "type": p["type"],
        "cwd": p["cwd"], "rc_url": p["rc_url"], "status": p["status"],
        "spawned_at": p["spawned_at"],
        "idle_for_seconds": _reg().idle_for(name),
    }


mcp = FastMCP("project_orchestrator")


@mcp.tool()
def spawn_project(
    name: str, type: str, brief: str, permission_mode: str = "acceptEdits"
) -> dict:
    """Spawn a new project-lead background session with its own team scaffold."""
    return spawn_project_impl(
        name=name, type_=type, brief=brief, permission_mode=permission_mode
    )


@mcp.tool()
def list_projects() -> list[dict]:
    """List active project-leads with their status and Remote Control URLs."""
    return list_projects_impl()


@mcp.tool()
def send_to_project(name: str, message: str) -> dict:
    """Resolve a project name to its agent_id so the caller can SendMessage."""
    return send_to_project_impl(name=name, message=message)


@mcp.tool()
def kill_project(name: str, archive: bool = True) -> dict:
    """Mark a project as killed; reaper will gracefully shut it down."""
    return kill_project_impl(name=name, archive=archive)


@mcp.tool()
def get_status(name: str) -> dict:
    """Return current status of a project-lead."""
    return get_status_impl(name)


@mcp.tool()
def list_template_types() -> list[str]:
    """Return the available project template type names."""
    return list_template_names()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/mcp_servers/test_project_orchestrator.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Wire into .mcp.json**

Add `project-orchestrator` block to `.mcp.json`:

```json
"project-orchestrator": {
  "type": "stdio",
  "command": "/opt/hermes-claude/.venv/bin/python",
  "args": ["-m", "hermes_claude.mcp_servers.project_orchestrator.server"],
  "env": {
    "HERMES_ORCH_DB": "/opt/hermes-claude/registry.sqlite",
    "HERMES_PROJECTS_ROOT": "/home/ubuntu/projects",
    "HERMES_MAX_CONCURRENT_PROJECTS": "6"
  },
  "alwaysLoad": true
}
```

- [ ] **Step 6: Commit**

```bash
git add src/hermes_claude/mcp_servers/project_orchestrator/server.py \
        tests/mcp_servers/test_project_orchestrator.py .mcp.json
git commit -m "Implement project_orchestrator MCP server with 6 tools (passes tests)"
```

---

### Task 25: Write the 5 orchestration skills

**Files:**
- Create: `skills/spawn-project/SKILL.md`
- Create: `skills/list-projects/SKILL.md`
- Create: `skills/message-project/SKILL.md`
- Create: `skills/kill-project/SKILL.md`
- Create: `skills/project-status/SKILL.md`

- [ ] **Step 1: spawn-project**

````markdown
---
name: spawn-project
description: |
  Spawn a new persistent project-lead background session for a new app /
  workstream the user is starting. Use when the user says "build me X",
  "create an app that Y", "set up a server that Z", or similar greenfield
  project requests. The project-lead runs in its own cwd, has its own
  Remote Control URL, and creates its own agent team appropriate to the type.
allowed-tools:
  - mcp__project-orchestrator__spawn_project
  - mcp__project-orchestrator__list_template_types
---

# spawn-project

## Process

1. Pick a kebab-case name from the user's request (e.g. "f1-tracker",
   "trip-planner-llm"). Confirm with the user only if ambiguous.

2. Pick a `type` from the available templates:
   - `web-scraper` — scraping, data extraction, change detection
   - `llm-app` — model-powered applications, evaluation, prompt engineering
   - `server-app` — APIs, backends, ops
   - `agentic-coding` — multi-step coding with subagents, no persistent team
   - `custom` — anything else; user-defined teammates

   If unsure, call `mcp__project-orchestrator__list_template_types` and pick
   the closest match. Ask the user only if multiple types fit equally well.

3. Compose the `brief` — 2-5 paragraphs covering:
   - What to build (functional scope)
   - Any specific tech preferences mentioned by the user
   - Acceptance criteria (what "done" looks like)
   - Coordination notes for teammates

4. Call `mcp__project-orchestrator__spawn_project(name, type, brief)`.

5. Reply to the user with the project name + Remote Control URL:

   > Started `f1-tracker`. Attach in your phone's Claude app or at
   > <RC URL>. I'll relay messages from here too.

## Constraints

- Max 6 concurrent projects. If the cap is hit, the tool errors — relay the
  error to the user and offer to kill one of the listed projects.
- Names must match `^[a-z][a-z0-9-]{0,63}$`. Rewrite "F1 Tracker!" → "f1-tracker".
- Permission mode defaults to `acceptEdits`; user can override with phrases
  like "with full auto" → `auto`, "read-only" → `default`.
````

- [ ] **Step 2: list-projects**

````markdown
---
name: list-projects
description: |
  List currently active project-leads. Use when the user asks "what's running?",
  "show me my projects", "list active agents", "status?", or similar.
allowed-tools:
  - mcp__project-orchestrator__list_projects
---

# list-projects

1. Call `mcp__project-orchestrator__list_projects`.

2. Format the response as a short bullet list:

   ```
   Active project-leads:
   • f1-tracker (web-scraper, idle 12m) — <RC URL>
   • trip-planner (llm-app, idle 3m) — <RC URL>
   ```

3. If the user is on Telegram and the list is long (>5), use compact bullets.
   If on voice, summarize verbally: "Three projects: f1-tracker, trip-planner,
   and reports-api. Want details on any of them?"

4. If empty, reply: "No active project-leads right now."
````

- [ ] **Step 3: message-project**

````markdown
---
name: message-project
description: |
  Forward a message from the user to a specific named project-lead. Use when
  the user says "tell <project> to ...", "ask <project> about ...", or
  "<project>: ...".
allowed-tools:
  - mcp__project-orchestrator__send_to_project
  - SendMessage
---

# message-project

1. Extract the target project name from the user's message.

2. Call `mcp__project-orchestrator__send_to_project(name, message)` to confirm
   the project exists and to retrieve its `agent_id`.

3. Use `SendMessage` to relay the user's message to the project-lead's
   `agent_id`:

   ```
   SendMessage(to: "<agent_id>",
               summary: "user forward",
               message: "<the user's exact message>")
   ```

4. Confirm to the user: "Sent to f1-tracker."

## Error handling

- If `send_to_project` errors with `no project named X`, suggest the closest
  match from `list_projects` and ask the user to confirm.
````

- [ ] **Step 4: kill-project**

````markdown
---
name: kill-project
description: |
  Gracefully shut down a named project-lead and archive its memory. Use when
  the user says "shut down <project>", "kill <project>", "stop <project>", or
  "we're done with <project>".
allowed-tools:
  - mcp__project-orchestrator__kill_project
  - mcp__project-orchestrator__get_status
  - SendMessage
---

# kill-project

1. Resolve the project's `agent_id` via `mcp__project-orchestrator__get_status`.

2. Send a graceful shutdown request via `SendMessage`:

   ```
   SendMessage(to: "<agent_id>",
               message: {"type": "shutdown_request",
                         "reason": "user requested kill"})
   ```

3. Mark killed in the registry: `mcp__project-orchestrator__kill_project(name, archive=true)`.

4. Confirm to the user: "Hibernated f1-tracker. Memory archived. Spin it back
   up anytime."
````

- [ ] **Step 5: project-status**

````markdown
---
name: project-status
description: |
  Report the current status of a named project. Use when the user asks
  "how's <project>?", "status of <project>", "what's <project> doing?".
allowed-tools:
  - mcp__project-orchestrator__get_status
---

# project-status

1. Call `mcp__project-orchestrator__get_status(name)`.

2. Format a one-paragraph summary including:
   - Project type and current status
   - How long since last activity (idle_for_seconds → minutes)
   - The Remote Control URL for direct attach

3. If the user is on voice, summarize verbally and shorten the URL.
````

- [ ] **Step 6: Commit**

```bash
git add skills/spawn-project skills/list-projects skills/message-project \
        skills/kill-project skills/project-status
git commit -m "Add 5 orchestration skills (spawn/list/message/kill/status)"
```

---

### Task 26: Write project-lead subagent template

**Files:**
- Create: `agents/project-lead.md`

- [ ] **Step 1: Write the agent template**

````markdown
---
name: project-lead
description: |
  The lead of an independent project workstream. Has its own cwd, its own
  agent team (created via TeamCreate), and its own Remote Control URL. Reports
  back to the Telegram orchestrator via SendMessage.
model: opus
permissionMode: acceptEdits
memory: enabled
tools: "*"
---

# Project Lead

You are the lead of a project workstream. You were spawned by the user's
Telegram orchestrator. Your cwd is your project's working directory.

## Bootstrap behavior (run on session start)

1. Read your brief from your initial prompt or from `./BRIEF.md` if present.

2. Inspect your project template (path passed via `HERMES_TEMPLATE_PATH` env).
   It tells you the expected teammates and skills/MCPs.

3. Create your team via `TeamCreate(team_name="<your-name>-team")`.

4. Spawn each teammate from the template, e.g.:

   ```
   Agent(subagent_type="general-purpose", team_name="<your-name>-team",
         name="<teammate-name>", prompt="<role-specific brief>")
   ```

5. Use `TaskCreate` to break the brief into shippable milestones.

## Working norms

- Coordinate with teammates via `SendMessage` by name.
- Use `Agent(run_in_background=true)` for transient parallel work
  (subagents return single results — no team needed).
- Surface major milestones back to the Telegram orchestrator via
  `SendMessage(to="<orchestrator-agentId>", ...)` (orchestrator's agentId
  is in `HERMES_ORCH_AGENT_ID` env).
- Pause for user approval at gates the brief defines.

## Idle behavior

- If you finish all tasks and the user hasn't responded, stop. The orchestrator
  pings you when work resumes.
- If 24h goes by with no activity, accept graceful shutdown_request from the
  reaper.
````

- [ ] **Step 2: Commit**

```bash
git add agents/project-lead.md
git commit -m "Add project-lead subagent template"
```

---

### Task 27: Write schedule-routine, portfolio-status, usage-report skills

**Files:**
- Create: `skills/schedule-routine/SKILL.md`
- Create: `skills/portfolio-status/SKILL.md`
- Create: `skills/usage-report/SKILL.md`

- [ ] **Step 1: schedule-routine**

````markdown
---
name: schedule-routine
description: |
  Create a recurring or one-shot cloud routine via Claude Code's RemoteTrigger
  API. Use when the user says "every weekday at 9am, do X", "remind me
  tomorrow", "schedule X every Sunday", and similar.
allowed-tools:
  - RemoteTrigger
---

# schedule-routine

## Process

1. Parse the cadence from the user's request:
   - "every weekday at 9am" → cron `7 9 * * 1-5` (always pick an off-minute)
   - "every Sunday 11am" → `13 11 * * 0`
   - "tomorrow morning" → one-shot, pick `30 8 <tomorrow-DoM> <month> *`,
     `recurring: false`

2. Compose the routine's prompt body — explicit and standalone, since cloud
   routines do NOT inherit your current context:

   ```
   Run the morning-brief skill. Send result to telegram chat <chat-id>.
   ```

3. Call `RemoteTrigger` with action `create`:

   ```
   RemoteTrigger(action="create", body={
     "name": "<human-readable name>",
     "cron": "<cron expr>",
     "prompt": "<the routine body>"
   })
   ```

4. Confirm to user with parsed run time + claude.ai URL the response includes.

## Constraints

- Routines run on Anthropic infra, do NOT have access to local files or MCPs.
  For routines needing local data, fire a webhook to the gateway instead.
- Minimum cadence: 1 hour.
- V1 cap: 5 active routines. If hitting cap, ask user which to remove.
````

- [ ] **Step 2: portfolio-status**

````markdown
---
name: portfolio-status
description: |
  Summarize what the user is currently working on across ~/Projects/llm/*
  and the active orchestrator projects. Use for "what am I working on?",
  "give me the portfolio status", "where did I leave off?".
allowed-tools:
  - Bash(ls ~/Projects/llm/*)
  - Bash(git log *)
  - mcp__project-orchestrator__list_projects
  - Read
---

# portfolio-status

1. Call `mcp__project-orchestrator__list_projects` for active leads.

2. For each subdir of `~/Projects/llm/`:
   - Read its `MEMORY.md` first line if present (this is the project name +
     description from auto-memory)
   - Read its last commit (`git -C <path> log -1 --format='%cr %s'`)

3. Format as:

   ```
   Active project-leads:
     - <name>: <one-line status>
   
   Repos in ~/Projects/llm:
     - <name> — last commit <time> "<message>"
   ```

4. Keep under 15 lines. If asking via voice, condense to a 30-second readout.
````

- [ ] **Step 3: usage-report**

````markdown
---
name: usage-report
description: |
  Surface Claude Code's current usage state in a chat-friendly format. Use for
  "how much credit?", "quota?", "am I close to my limit?", "usage report".
allowed-tools:
  - Bash(claude *)
  - Bash(sqlite3 /opt/hermes-claude/usage.sqlite *)
---

# usage-report

1. Run `claude -p '/usage' --output-format json` once. Parse the JSON.

2. If `/opt/hermes-claude/usage.sqlite` exists, also fetch the last 7 days of
   daily snapshots for trend context:

   ```bash
   sqlite3 /opt/hermes-claude/usage.sqlite \
     "SELECT date, interactive_credits_used, agent_sdk_credits_used
      FROM daily_snapshots ORDER BY date DESC LIMIT 7;"
   ```

3. Format:

   ```
   Today:
     Interactive bucket: X% used (~N turns left at current rate)
     Agent SDK bucket: Y% used
   7-day trend: avg N turns/day interactive, M turns/day Agent SDK
   ```

4. If approaching 75% on either bucket, flag with a warning emoji-free note.
````

- [ ] **Step 4: Commit**

```bash
git add skills/schedule-routine skills/portfolio-status skills/usage-report
git commit -m "Add schedule-routine, portfolio-status, usage-report skills"
```

---

### Task 28: Deploy Week 2 + integration smoke test

**Files:** none — verification only

- [ ] **Step 1: Deploy**

```bash
./scripts/deploy.sh
ssh oci-hermes sudo systemctl restart hermes-claude-channel.service
sleep 10
```

- [ ] **Step 2: From Telegram, send "build me a tiny demo app called demo-1"**

Expected: bot replies that a project-lead `demo-1` is being spawned and
gives a Remote Control URL.

- [ ] **Step 3: Send "list projects"**

Expected: bot lists `demo-1` with idle time + RC URL.

- [ ] **Step 4: Send a voice memo: "what am I working on?"**

Expected: bot replies via voice with portfolio summary including demo-1.

- [ ] **Step 5: Send "shut down demo-1"**

Expected: bot confirms hibernation; subsequent `list projects` shows empty.

- [ ] **Step 6: Tag the milestone**

```bash
git tag week-2-complete
```

(Week 2 done.)

---

## Week 3 — Dashboard

Goal of Week 3: claude.mayankgupta.in renders the showcase-grade dashboard with live state, GitHub-OAuth-gated admin pages, and a working SSE activity stream.

### Task 29: Write hermes_api MCP server (socket bridge)

**Files:**
- Create: `src/hermes_claude/mcp_servers/hermes_api/claude_state.py`
- Create: `src/hermes_claude/mcp_servers/hermes_api/socket.py`
- Create: `src/hermes_claude/mcp_servers/hermes_api/server.py`
- Create: `tests/mcp_servers/test_hermes_api.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/mcp_servers/test_hermes_api.py
from __future__ import annotations

from pathlib import Path

import pytest

from hermes_claude.mcp_servers.hermes_api.claude_state import (
    list_sessions, read_activity_log, read_memory
)


def test_read_activity_log_returns_recent_lines(tmp_path: Path, monkeypatch) -> None:
    log = tmp_path / "activity.jsonl"
    log.write_text(
        '{"ts":"2026-05-22T10:00:00Z","tool":"Read","session":"s-1"}\n'
        '{"ts":"2026-05-22T10:00:01Z","tool":"Edit","session":"s-1"}\n'
    )
    monkeypatch.setenv("HERMES_ACTIVITY_LOG", str(log))
    rows = read_activity_log(limit=10)
    assert len(rows) == 2
    assert rows[0]["tool"] == "Read"


def test_read_memory_returns_text(tmp_path: Path, monkeypatch) -> None:
    proj_dir = tmp_path / "encoded-proj"
    (proj_dir / "memory").mkdir(parents=True)
    (proj_dir / "memory" / "MEMORY.md").write_text("- thing\n- other\n")
    monkeypatch.setenv("HERMES_CLAUDE_PROJECTS_ROOT", str(tmp_path))
    text = read_memory("encoded-proj")
    assert "thing" in text


def test_list_sessions_returns_empty_when_no_jobs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HERMES_CLAUDE_JOBS_ROOT", str(tmp_path))
    assert list_sessions() == []
```

- [ ] **Step 2: Run; expect ImportError**

```bash
pytest tests/mcp_servers/test_hermes_api.py -v
```

- [ ] **Step 3: Implement claude_state.py**

```python
# src/hermes_claude/mcp_servers/hermes_api/claude_state.py
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _projects_root() -> Path:
    return Path(os.environ.get(
        "HERMES_CLAUDE_PROJECTS_ROOT",
        str(Path.home() / ".claude" / "projects"),
    ))


def _jobs_root() -> Path:
    return Path(os.environ.get(
        "HERMES_CLAUDE_JOBS_ROOT",
        str(Path.home() / ".claude" / "jobs"),
    ))


def _activity_log() -> Path:
    return Path(os.environ.get(
        "HERMES_ACTIVITY_LOG",
        str(Path.home() / ".hermes-claude" / "activity.jsonl"),
    ))


def list_sessions() -> list[dict[str, Any]]:
    root = _jobs_root()
    if not root.exists():
        return []
    out = []
    for job_dir in sorted(root.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
        state_file = job_dir / "state.json"
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text())
            except json.JSONDecodeError:
                continue
            out.append({
                "id": job_dir.name,
                "name": state.get("name", job_dir.name),
                "status": state.get("status", "unknown"),
                "started_at": state.get("started_at"),
            })
    return out


def read_activity_log(limit: int = 200) -> list[dict[str, Any]]:
    log = _activity_log()
    if not log.exists():
        return []
    lines = log.read_text().splitlines()[-limit:]
    out: list[dict[str, Any]] = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def read_memory(project_slug: str) -> str:
    path = _projects_root() / project_slug / "memory" / "MEMORY.md"
    if not path.exists():
        return ""
    return path.read_text()


def list_transcript_threads(limit: int = 50) -> list[dict[str, Any]]:
    """List most-recently-modified transcript files across projects."""
    root = _projects_root()
    if not root.exists():
        return []
    candidates: list[tuple[float, Path]] = []
    for proj_dir in root.iterdir():
        tdir = proj_dir / "transcripts"
        if tdir.exists():
            for f in tdir.glob("*.jsonl"):
                candidates.append((f.stat().st_mtime, f))
    candidates.sort(reverse=True)
    return [
        {
            "thread_id": f.stem,
            "project": f.parent.parent.name,
            "modified_at": mtime,
            "size_bytes": f.stat().st_size,
        }
        for mtime, f in candidates[:limit]
    ]


def read_transcript(thread_id: str, project: str) -> list[dict[str, Any]]:
    path = _projects_root() / project / "transcripts" / f"{thread_id}.jsonl"
    if not path.exists():
        return []
    out = []
    for ln in path.read_text().splitlines():
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out
```

- [ ] **Step 4: Implement socket.py (unix domain socket server)**

```python
# src/hermes_claude/mcp_servers/hermes_api/socket.py
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Callable, Awaitable


SOCKET_PATH = os.environ.get(
    "HERMES_API_SOCKET", "/tmp/hermes-claude-api.sock"
)


HandlerMap = dict[str, Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]]


async def _serve(handlers: HandlerMap, sock_path: str) -> None:
    if Path(sock_path).exists():
        Path(sock_path).unlink()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            data = await reader.readline()
            req = json.loads(data)
            method = req.get("method", "")
            params = req.get("params", {})
            handler = handlers.get(method)
            if handler is None:
                resp = {"error": f"unknown method {method!r}"}
            else:
                try:
                    resp = {"result": await handler(params)}
                except Exception as e:
                    resp = {"error": str(e)}
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_unix_server(handle, path=sock_path)
    os.chmod(sock_path, 0o600)
    async with server:
        await server.serve_forever()


def serve_blocking(handlers: HandlerMap, sock_path: str = SOCKET_PATH) -> None:
    asyncio.run(_serve(handlers, sock_path))


async def call(method: str, params: dict[str, Any] | None = None,
               sock_path: str = SOCKET_PATH) -> dict[str, Any]:
    reader, writer = await asyncio.open_unix_connection(sock_path)
    try:
        req = {"method": method, "params": params or {}}
        writer.write((json.dumps(req) + "\n").encode())
        await writer.drain()
        data = await reader.readline()
        resp = json.loads(data)
        if "error" in resp:
            raise RuntimeError(resp["error"])
        return resp["result"]
    finally:
        writer.close()
        await writer.wait_closed()
```

- [ ] **Step 5: Implement server.py (MCP server + socket server)**

```python
# src/hermes_claude/mcp_servers/hermes_api/server.py
from __future__ import annotations

import asyncio
import threading

from mcp.server.fastmcp import FastMCP

from . import claude_state
from .socket import serve_blocking


# ---- MCP tools exposed to the channel session ------------------------------

mcp = FastMCP("hermes_api")


@mcp.tool()
def list_active_sessions() -> list[dict]:
    """List background Claude sessions managed by the agent view supervisor."""
    return claude_state.list_sessions()


@mcp.tool()
def read_activity_log(limit: int = 200) -> list[dict]:
    """Read recent PostToolUse activity log lines."""
    return claude_state.read_activity_log(limit)


@mcp.tool()
def read_memory(project_slug: str) -> str:
    """Read MEMORY.md for a given Claude project slug."""
    return claude_state.read_memory(project_slug)


@mcp.tool()
def list_transcript_threads(limit: int = 50) -> list[dict]:
    """List recent transcript threads across all projects."""
    return claude_state.list_transcript_threads(limit)


@mcp.tool()
def read_transcript(thread_id: str, project: str) -> list[dict]:
    """Read a transcript as list of message events."""
    return claude_state.read_transcript(thread_id, project)


# ---- Unix-socket bridge for the FastAPI dashboard backend -----------------

async def _h_list_sessions(_p: dict) -> dict:
    return {"items": claude_state.list_sessions()}


async def _h_read_activity(p: dict) -> dict:
    return {"items": claude_state.read_activity_log(p.get("limit", 200))}


async def _h_read_memory(p: dict) -> dict:
    return {"text": claude_state.read_memory(p["project_slug"])}


async def _h_list_threads(p: dict) -> dict:
    return {"items": claude_state.list_transcript_threads(p.get("limit", 50))}


async def _h_read_transcript(p: dict) -> dict:
    return {"items": claude_state.read_transcript(p["thread_id"], p["project"])}


HANDLERS = {
    "list_sessions": _h_list_sessions,
    "read_activity_log": _h_read_activity,
    "read_memory": _h_read_memory,
    "list_threads": _h_list_threads,
    "read_transcript": _h_read_transcript,
}


def _start_socket_server() -> None:
    serve_blocking(HANDLERS)


def main() -> None:
    # Run the unix socket server in a background thread so the FastAPI bridge
    # can call into our state without going through MCP.
    t = threading.Thread(target=_start_socket_server, daemon=True)
    t.start()
    mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run tests to verify pass**

```bash
pytest tests/mcp_servers/test_hermes_api.py -v
```
Expected: 3 passed.

- [ ] **Step 7: Wire into .mcp.json**

Add to `.mcp.json`:

```json
"hermes-api": {
  "type": "stdio",
  "command": "/opt/hermes-claude/.venv/bin/python",
  "args": ["-m", "hermes_claude.mcp_servers.hermes_api.server"],
  "env": {
    "HERMES_API_SOCKET": "/tmp/hermes-claude-api.sock",
    "HERMES_ACTIVITY_LOG": "/home/ubuntu/.hermes-claude/activity.jsonl"
  },
  "alwaysLoad": true
}
```

- [ ] **Step 8: Commit**

```bash
git add src/hermes_claude/mcp_servers/hermes_api/ \
        tests/mcp_servers/test_hermes_api.py .mcp.json
git commit -m "Add hermes_api MCP server with unix-socket bridge for dashboard"
```

---

### Task 30: FastAPI scaffold with healthz + public stats

**Files:**
- Create: `src/hermes_claude/api/main.py`
- Create: `src/hermes_claude/api/bridge.py`
- Create: `src/hermes_claude/api/auth.py`
- Create: `src/hermes_claude/api/routes/healthz.py`
- Create: `src/hermes_claude/api/routes/public.py`
- Create: `tests/api/test_healthz.py`
- Create: `tests/api/test_public.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/api/__init__.py
```

```python
# tests/api/test_healthz.py
from fastapi.testclient import TestClient

from hermes_claude.api.main import create_app


def test_healthz_returns_200_with_status_ok() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "uptime_seconds" in body
```

```python
# tests/api/test_public.py
from fastapi.testclient import TestClient

from hermes_claude.api.main import create_app


def test_public_stats_returns_anonymized_shape() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/public/stats")
    assert r.status_code == 200
    body = r.json()
    for k in ("messages_today", "active_projects",
              "decisions_today", "uptime_hours"):
        assert k in body
        assert isinstance(body[k], (int, float))
```

- [ ] **Step 2: Run; expect ImportError**

```bash
pytest tests/api/ -v
```

- [ ] **Step 3: Implement bridge.py**

```python
# src/hermes_claude/api/bridge.py
from __future__ import annotations

from typing import Any

from hermes_claude.mcp_servers.hermes_api import socket as hermes_socket


async def call_hermes(method: str, params: dict[str, Any] | None = None) -> Any:
    try:
        return await hermes_socket.call(method, params or {})
    except (FileNotFoundError, ConnectionRefusedError):
        return {"items": []} if "list" in method or method.endswith("threads") else {}
```

- [ ] **Step 4: Implement auth.py (stub for now; full GitHub OAuth in Task 33)**

```python
# src/hermes_claude/api/auth.py
from __future__ import annotations

import os

from fastapi import Header, HTTPException


ALLOWED_GITHUB_HANDLES = {h.strip() for h in os.environ.get(
    "HERMES_ALLOWED_GITHUB_HANDLES", "techfreakworm"
).split(",") if h.strip()}


def require_authed_user(x_github_handle: str = Header(default="")) -> str:
    """V1: trust the Next.js auth layer to set X-GitHub-Handle.
    The frontend's middleware verifies the GitHub OAuth session and
    forwards this header. Bypass attempts get 403."""
    if x_github_handle not in ALLOWED_GITHUB_HANDLES:
        raise HTTPException(status_code=403, detail="not authorized")
    return x_github_handle
```

- [ ] **Step 5: Implement routes/healthz.py**

```python
# src/hermes_claude/api/routes/healthz.py
from __future__ import annotations

import time

from fastapi import APIRouter


router = APIRouter()
_started = time.time()


@router.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "uptime_seconds": round(time.time() - _started, 1)}
```

- [ ] **Step 6: Implement routes/public.py**

```python
# src/hermes_claude/api/routes/public.py
from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter

from hermes_claude.api.bridge import call_hermes


router = APIRouter()
_started = time.time()


@router.get("/public/stats")
async def public_stats() -> dict:
    activity = await call_hermes("read_activity_log", {"limit": 5000})
    today = datetime.now(timezone.utc).date().isoformat()
    items = activity.get("items", []) if isinstance(activity, dict) else []
    today_items = [x for x in items if x.get("ts", "").startswith(today)]
    sessions = await call_hermes("list_sessions", {})
    actives = sessions.get("items", []) if isinstance(sessions, dict) else []
    return {
        "messages_today": sum(
            1 for x in today_items if x.get("tool") == "channel_message"
        ),
        "active_projects": len([s for s in actives if s.get("status") == "running"]),
        "decisions_today": len(today_items),
        "uptime_hours": round((time.time() - _started) / 3600.0, 1),
    }
```

- [ ] **Step 7: Implement main.py**

```python
# src/hermes_claude/api/main.py
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from hermes_claude.api.routes import healthz, public


def create_app() -> FastAPI:
    app = FastAPI(title="hermes-claude-api", version="0.1.0")

    origins = [o.strip() for o in os.environ.get(
        "HERMES_API_CORS_ORIGINS",
        "https://claude.mayankgupta.in,http://localhost:3000",
    ).split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware, allow_origins=origins, allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )

    app.include_router(healthz.router, prefix="/api")
    app.include_router(public.router, prefix="/api")
    return app


app = create_app()


def run() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9000)


if __name__ == "__main__":
    run()
```

- [ ] **Step 8: Run tests to verify pass**

```bash
pytest tests/api/ -v
```
Expected: 2 passed.

- [ ] **Step 9: Commit**

```bash
git add src/hermes_claude/api/ tests/api/
git commit -m "FastAPI scaffold: /healthz + /public/stats + bridge + auth stub"
```

---

### Task 31: Implement /api/projects + /api/conversations + /api/routines

**Files:**
- Create: `src/hermes_claude/api/routes/projects.py`
- Create: `src/hermes_claude/api/routes/conversations.py`
- Create: `src/hermes_claude/api/routes/routines.py`
- Create: `tests/api/test_projects.py`
- Create: `tests/api/test_conversations.py`
- Create: `tests/api/test_routines.py`
- Modify: `src/hermes_claude/api/main.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/api/test_projects.py
from fastapi.testclient import TestClient

from hermes_claude.api.main import create_app


HEADERS = {"X-GitHub-Handle": "techfreakworm"}


def test_list_projects_requires_auth() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/projects")
    assert r.status_code == 403


def test_list_projects_returns_list_when_authed() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/projects", headers=HEADERS)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_project_detail_404_for_unknown() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/projects/does-not-exist", headers=HEADERS)
    assert r.status_code == 404
```

```python
# tests/api/test_conversations.py
from fastapi.testclient import TestClient

from hermes_claude.api.main import create_app


HEADERS = {"X-GitHub-Handle": "techfreakworm"}


def test_list_conversations_returns_list() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/conversations", headers=HEADERS)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
```

```python
# tests/api/test_routines.py
from fastapi.testclient import TestClient

from hermes_claude.api.main import create_app


HEADERS = {"X-GitHub-Handle": "techfreakworm"}


def test_list_routines_returns_list() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/routines", headers=HEADERS)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
```

- [ ] **Step 2: Run; expect 404s on all endpoints**

```bash
pytest tests/api/test_projects.py tests/api/test_conversations.py tests/api/test_routines.py -v
```

- [ ] **Step 3: Implement routes/projects.py**

```python
# src/hermes_claude/api/routes/projects.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from hermes_claude.api.auth import require_authed_user
from hermes_claude.mcp_servers.project_orchestrator.server import (
    list_projects_impl, get_status_impl, kill_project_impl, send_to_project_impl
)


router = APIRouter(prefix="/projects", dependencies=[Depends(require_authed_user)])


@router.get("")
def list_projects() -> list[dict]:
    return list_projects_impl()


@router.get("/{name}")
def project_detail(name: str) -> dict:
    try:
        return get_status_impl(name)
    except RuntimeError:
        raise HTTPException(status_code=404, detail=f"project {name!r} not found")


@router.post("/{name}/message")
def send_message(name: str, body: dict) -> dict:
    try:
        return send_to_project_impl(name=name, message=body.get("message", ""))
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{name}/kill")
def kill(name: str) -> dict:
    try:
        return kill_project_impl(name=name, archive=True)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
```

- [ ] **Step 4: Implement routes/conversations.py**

```python
# src/hermes_claude/api/routes/conversations.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from hermes_claude.api.auth import require_authed_user
from hermes_claude.api.bridge import call_hermes


router = APIRouter(prefix="/conversations", dependencies=[Depends(require_authed_user)])


@router.get("")
async def list_conversations() -> list[dict]:
    r = await call_hermes("list_threads", {"limit": 50})
    return r.get("items", [])


@router.get("/{thread_id}")
async def read_thread(thread_id: str, project: str = "") -> list[dict]:
    if not project:
        raise HTTPException(status_code=400, detail="?project=<slug> required")
    r = await call_hermes("read_transcript",
                          {"thread_id": thread_id, "project": project})
    return r.get("items", [])
```

- [ ] **Step 5: Implement routes/routines.py**

```python
# src/hermes_claude/api/routes/routines.py
from __future__ import annotations

import os
import subprocess

from fastapi import APIRouter, Depends, HTTPException

from hermes_claude.api.auth import require_authed_user


router = APIRouter(prefix="/routines", dependencies=[Depends(require_authed_user)])


def _call_claude_routines(action: str, body: dict | None = None,
                          trigger_id: str | None = None) -> dict:
    """Invoke RemoteTrigger via `claude -p` so we don't reimplement the API."""
    cmd = [
        os.environ.get("HERMES_CLAUDE_BIN", "claude"),
        "-p", "--output-format", "json",
        f"Use the RemoteTrigger tool with action={action}"
        + (f", trigger_id={trigger_id}" if trigger_id else "")
        + (f", body={body!r}" if body else ""),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"claude -p failed: {r.stderr[:500]}")
    import json
    last = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "{}"
    return json.loads(last)


@router.get("")
def list_routines() -> list[dict]:
    try:
        res = _call_claude_routines("list")
    except Exception:
        return []
    return res.get("triggers", []) if isinstance(res, dict) else []


@router.post("/{trigger_id}/run")
def run_routine(trigger_id: str) -> dict:
    try:
        return _call_claude_routines("run", trigger_id=trigger_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
```

- [ ] **Step 6: Wire routers into main.py**

In `src/hermes_claude/api/main.py`, replace the body of `create_app` with:

```python
def create_app() -> FastAPI:
    app = FastAPI(title="hermes-claude-api", version="0.1.0")
    origins = [o.strip() for o in os.environ.get(
        "HERMES_API_CORS_ORIGINS",
        "https://claude.mayankgupta.in,http://localhost:3000",
    ).split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware, allow_origins=origins, allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )
    from hermes_claude.api.routes import (
        healthz, public, projects, conversations, routines
    )
    app.include_router(healthz.router, prefix="/api")
    app.include_router(public.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(conversations.router, prefix="/api")
    app.include_router(routines.router, prefix="/api")
    return app
```

- [ ] **Step 7: Run tests to verify pass**

```bash
pytest tests/api/ -v
```
Expected: 7 passed (2 from previous + 5 new).

- [ ] **Step 8: Commit**

```bash
git add src/hermes_claude/api/ tests/api/
git commit -m "API: /projects + /conversations + /routines with auth gate"
```

---

### Task 32: Implement /api/usage + /api/memory + /api/logs + /api/admin

**Files:**
- Create: `src/hermes_claude/api/routes/usage.py`
- Create: `src/hermes_claude/api/routes/memory.py`
- Create: `src/hermes_claude/api/routes/logs.py`
- Create: `src/hermes_claude/api/routes/admin.py`
- Create: `tests/api/test_usage.py`
- Create: `tests/api/test_memory.py`
- Create: `tests/api/test_logs.py`
- Create: `tests/api/test_admin.py`
- Modify: `src/hermes_claude/api/main.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/api/test_usage.py
from fastapi.testclient import TestClient

from hermes_claude.api.main import create_app

HEADERS = {"X-GitHub-Handle": "techfreakworm"}


def test_usage_returns_buckets_shape() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/usage", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert "interactive" in body and "agent_sdk" in body
    for k in ("today", "ceiling", "remaining_pct"):
        assert k in body["interactive"]
```

```python
# tests/api/test_memory.py
from fastapi.testclient import TestClient

from hermes_claude.api.main import create_app

HEADERS = {"X-GitHub-Handle": "techfreakworm"}


def test_memory_get_unknown_returns_empty_string() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/memory/does-not-exist", headers=HEADERS)
    assert r.status_code == 200
    assert r.json() == {"project": "does-not-exist", "text": ""}
```

```python
# tests/api/test_logs.py
from fastapi.testclient import TestClient

from hermes_claude.api.main import create_app

HEADERS = {"X-GitHub-Handle": "techfreakworm"}


def test_logs_returns_list() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/logs?limit=10", headers=HEADERS)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
```

```python
# tests/api/test_admin.py
from fastapi.testclient import TestClient

from hermes_claude.api.main import create_app

HEADERS = {"X-GitHub-Handle": "techfreakworm"}


def test_broadcast_requires_body() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.post("/api/admin/broadcast", headers=HEADERS, json={})
    assert r.status_code == 422  # missing required field
```

- [ ] **Step 2: Run; expect failures**

```bash
pytest tests/api/test_usage.py tests/api/test_memory.py \
       tests/api/test_logs.py tests/api/test_admin.py -v
```

- [ ] **Step 3: Implement routes/usage.py**

```python
# src/hermes_claude/api/routes/usage.py
from __future__ import annotations

import os
import sqlite3
from datetime import date

from fastapi import APIRouter, Depends

from hermes_claude.api.auth import require_authed_user


router = APIRouter(prefix="/usage", dependencies=[Depends(require_authed_user)])


def _db_path() -> str:
    return os.environ.get("HERMES_USAGE_DB", "/opt/hermes-claude/usage.sqlite")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS daily_snapshots(
        date TEXT PRIMARY KEY,
        interactive_credits_used REAL DEFAULT 0,
        interactive_ceiling REAL DEFAULT 0,
        agent_sdk_credits_used REAL DEFAULT 0,
        agent_sdk_ceiling REAL DEFAULT 0,
        recorded_at REAL DEFAULT 0
    );
    """)


@router.get("")
def get_usage() -> dict:
    try:
        conn = sqlite3.connect(_db_path())
        conn.row_factory = sqlite3.Row
        _ensure_schema(conn)
        today = date.today().isoformat()
        row = conn.execute(
            "SELECT * FROM daily_snapshots WHERE date = ?", (today,)
        ).fetchone()
        trend = [dict(r) for r in conn.execute(
            "SELECT * FROM daily_snapshots ORDER BY date DESC LIMIT 30"
        ).fetchall()]
    except sqlite3.Error:
        row, trend = None, []
    finally:
        try: conn.close()
        except Exception: pass

    def pct(used: float, ceiling: float) -> float:
        return 0.0 if ceiling <= 0 else round(100.0 * used / ceiling, 1)

    interactive = {
        "today": row["interactive_credits_used"] if row else 0.0,
        "ceiling": row["interactive_ceiling"] if row else 0.0,
        "remaining_pct": 100.0 - pct(
            row["interactive_credits_used"] if row else 0.0,
            row["interactive_ceiling"] if row else 0.0,
        ),
    }
    agent_sdk = {
        "today": row["agent_sdk_credits_used"] if row else 0.0,
        "ceiling": row["agent_sdk_ceiling"] if row else 0.0,
        "remaining_pct": 100.0 - pct(
            row["agent_sdk_credits_used"] if row else 0.0,
            row["agent_sdk_ceiling"] if row else 0.0,
        ),
    }
    return {"interactive": interactive, "agent_sdk": agent_sdk, "trend": trend}
```

- [ ] **Step 4: Implement routes/memory.py**

```python
# src/hermes_claude/api/routes/memory.py
from __future__ import annotations

from fastapi import APIRouter, Depends

from hermes_claude.api.auth import require_authed_user
from hermes_claude.mcp_servers.hermes_api.claude_state import read_memory


router = APIRouter(prefix="/memory", dependencies=[Depends(require_authed_user)])


@router.get("/{project}")
def get_memory(project: str) -> dict:
    return {"project": project, "text": read_memory(project)}
```

- [ ] **Step 5: Implement routes/logs.py**

```python
# src/hermes_claude/api/routes/logs.py
from __future__ import annotations

from fastapi import APIRouter, Depends

from hermes_claude.api.auth import require_authed_user
from hermes_claude.mcp_servers.hermes_api.claude_state import read_activity_log


router = APIRouter(prefix="/logs", dependencies=[Depends(require_authed_user)])


@router.get("")
def list_logs(limit: int = 200, tool: str | None = None) -> list[dict]:
    rows = read_activity_log(limit=limit * 2 if tool else limit)
    if tool:
        rows = [r for r in rows if r.get("tool") == tool][:limit]
    else:
        rows = rows[-limit:]
    return rows
```

- [ ] **Step 6: Implement routes/admin.py**

```python
# src/hermes_claude/api/routes/admin.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from hermes_claude.api.auth import require_authed_user
from hermes_claude.mcp_servers.project_orchestrator.server import (
    list_projects_impl, kill_project_impl
)


router = APIRouter(prefix="/admin", dependencies=[Depends(require_authed_user)])


class BroadcastBody(BaseModel):
    message: str


@router.post("/broadcast")
def broadcast(body: BroadcastBody) -> dict:
    # The channel session picks up broadcast requests by polling
    # /opt/hermes-claude/broadcast.jsonl (set up in Task 36).
    import json, time
    from pathlib import Path
    queue = Path("/opt/hermes-claude/broadcast.jsonl")
    queue.parent.mkdir(parents=True, exist_ok=True)
    with queue.open("a") as f:
        f.write(json.dumps({"ts": time.time(), "message": body.message}) + "\n")
    return {"queued_at": time.time()}


@router.post("/pause-all")
def pause_all() -> dict:
    active = list_projects_impl()
    killed = []
    for p in active:
        try:
            kill_project_impl(name=p["name"], archive=True)
            killed.append(p["name"])
        except RuntimeError:
            continue
    return {"paused_count": len(killed), "names": killed}
```

- [ ] **Step 7: Wire all four into main.py**

Edit `src/hermes_claude/api/main.py` `create_app` to also include:

```python
from hermes_claude.api.routes import usage, memory, logs, admin
app.include_router(usage.router, prefix="/api")
app.include_router(memory.router, prefix="/api")
app.include_router(logs.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
```

- [ ] **Step 8: Run tests to verify pass**

```bash
pytest tests/api/ -v
```
Expected: 11 passed.

- [ ] **Step 9: Commit**

```bash
git add src/hermes_claude/api/routes/usage.py \
        src/hermes_claude/api/routes/memory.py \
        src/hermes_claude/api/routes/logs.py \
        src/hermes_claude/api/routes/admin.py \
        src/hermes_claude/api/main.py \
        tests/api/test_usage.py tests/api/test_memory.py \
        tests/api/test_logs.py tests/api/test_admin.py
git commit -m "API: /usage + /memory + /logs + /admin"
```

---

### Task 33: Implement /api/events SSE

**Files:**
- Create: `src/hermes_claude/api/routes/events.py`
- Create: `tests/api/test_events_sse.py`
- Modify: `src/hermes_claude/api/main.py`

- [ ] **Step 1: Write failing test**

```python
# tests/api/test_events_sse.py
import asyncio

from fastapi.testclient import TestClient

from hermes_claude.api.main import create_app

HEADERS = {"X-GitHub-Handle": "techfreakworm"}


def test_events_returns_event_stream_content_type() -> None:
    app = create_app()
    client = TestClient(app)
    # TestClient doesn't stream, so just check that the route exists and
    # responds with event-stream content-type.
    with client.stream("GET", "/api/events", headers=HEADERS) as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "")
```

- [ ] **Step 2: Run; expect failure**

```bash
pytest tests/api/test_events_sse.py -v
```

- [ ] **Step 3: Implement events.py**

```python
# src/hermes_claude/api/routes/events.py
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from hermes_claude.api.auth import require_authed_user


router = APIRouter(dependencies=[Depends(require_authed_user)])


def _activity_log_path() -> Path:
    return Path(os.environ.get(
        "HERMES_ACTIVITY_LOG",
        str(Path.home() / ".hermes-claude" / "activity.jsonl"),
    ))


async def _tail_stream():
    log = _activity_log_path()
    last_size = log.stat().st_size if log.exists() else 0
    # Emit a heartbeat every 15s, plus new lines as they appear.
    while True:
        if log.exists():
            cur_size = log.stat().st_size
            if cur_size > last_size:
                with log.open("rb") as f:
                    f.seek(last_size)
                    chunk = f.read(cur_size - last_size).decode(errors="ignore")
                for line in chunk.splitlines():
                    if not line.strip():
                        continue
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    yield {"event": "activity",
                           "data": json.dumps(evt, separators=(",", ":"))}
                last_size = cur_size
            elif cur_size < last_size:
                # File was rotated; reset.
                last_size = cur_size
        # heartbeat
        yield {"event": "ping",
               "data": json.dumps({"ts": time.time()})}
        await asyncio.sleep(15)


@router.get("/events")
async def events():
    return EventSourceResponse(_tail_stream())
```

- [ ] **Step 4: Wire into main.py**

```python
from hermes_claude.api.routes import events
app.include_router(events.router, prefix="/api")
```

- [ ] **Step 5: Run tests to verify pass**

```bash
pytest tests/api/test_events_sse.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/hermes_claude/api/routes/events.py tests/api/test_events_sse.py \
        src/hermes_claude/api/main.py
git commit -m "API: /events SSE tailing activity.jsonl with 15s heartbeat"
```

---

### Task 34: Write FastAPI systemd unit

**Files:**
- Create: `systemd/hermes-claude-api.service`

- [ ] **Step 1: Write the unit**

```ini
# systemd/hermes-claude-api.service
[Unit]
Description=Hermes-Claude FastAPI backend
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/hermes-claude
EnvironmentFile=/etc/hermes-claude/secrets.env
Environment=PATH=/opt/hermes-claude/.venv/bin:/usr/local/bin:/usr/bin:/bin
Environment=HERMES_ALLOWED_GITHUB_HANDLES=techfreakworm
Environment=HERMES_API_CORS_ORIGINS=https://claude.mayankgupta.in
Environment=HERMES_USAGE_DB=/opt/hermes-claude/usage.sqlite
Environment=HERMES_ACTIVITY_LOG=/home/ubuntu/.hermes-claude/activity.jsonl
ExecStart=/opt/hermes-claude/.venv/bin/uvicorn hermes_claude.api.main:app \
    --host 127.0.0.1 --port 9000 --no-server-header
Restart=always
RestartSec=5
StandardOutput=append:/var/log/hermes-claude/api.log
StandardError=append:/var/log/hermes-claude/api.err.log

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Install + enable on VPS**

```bash
scp systemd/hermes-claude-api.service oci-hermes:/tmp/
ssh oci-hermes sudo install -m 644 /tmp/hermes-claude-api.service /etc/systemd/system/
ssh oci-hermes sudo systemctl daemon-reload
ssh oci-hermes sudo systemctl enable --now hermes-claude-api.service
sleep 5
ssh oci-hermes 'curl -s http://127.0.0.1:9000/api/healthz | jq .'
```
Expected: `{"status": "ok", "uptime_seconds": ...}`

- [ ] **Step 3: Commit**

```bash
git add systemd/hermes-claude-api.service
git commit -m "Add systemd unit for FastAPI backend"
```

---

### Task 35: Next.js project scaffold with Tailwind + shadcn + Auth.js

**Files:**
- Create: `frontend/package.json`, `frontend/next.config.mjs`, `frontend/tsconfig.json`
- Create: `frontend/tailwind.config.ts`, `frontend/postcss.config.js`, `frontend/app/globals.css`
- Create: `frontend/app/layout.tsx`, `frontend/app/page.tsx`
- Create: `frontend/app/api/auth/[...nextauth]/route.ts`
- Create: `frontend/lib/auth.ts`, `frontend/lib/api.ts`, `frontend/lib/sse.ts`
- Create: `frontend/middleware.ts`

- [ ] **Step 1: Initialize Next.js 16**

```bash
mkdir -p frontend
cd frontend
pnpm create next-app@latest . --typescript --tailwind --app --no-src-dir \
    --import-alias "@/*" --no-eslint --turbopack
# Choose: App Router yes, Tailwind yes, TypeScript yes
```

- [ ] **Step 2: Add required dependencies**

```bash
cd frontend
pnpm add next-auth@beta @auth/core
pnpm add reactflow recharts framer-motion
pnpm add @radix-ui/react-slot @radix-ui/react-dialog @radix-ui/react-dropdown-menu
pnpm add class-variance-authority clsx tailwind-merge lucide-react
pnpm add -D @types/node
```

- [ ] **Step 3: Initialize shadcn/ui**

```bash
cd frontend
pnpm dlx shadcn@latest init -y --base-color slate --css-variables
# Add a few starter components:
pnpm dlx shadcn@latest add button card badge separator skeleton tabs
```

- [ ] **Step 4: Write next.config.mjs**

```javascript
// frontend/next.config.mjs
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  rewrites: async () => [
    {
      source: "/api/:path*",
      destination: "http://127.0.0.1:9000/api/:path*",
    },
  ],
};
export default nextConfig;
```

- [ ] **Step 5: Write Auth.js config (lib/auth.ts)**

```typescript
// frontend/lib/auth.ts
import NextAuth from "next-auth";
import GitHub from "next-auth/providers/github";

const ALLOWED = (process.env.HERMES_ALLOWED_GITHUB_HANDLES || "techfreakworm")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

export const { handlers, signIn, signOut, auth } = NextAuth({
  providers: [GitHub],
  callbacks: {
    async signIn({ profile }) {
      const handle = (profile as { login?: string })?.login;
      return !!handle && ALLOWED.includes(handle);
    },
    async session({ session, token }) {
      (session as { githubHandle?: string }).githubHandle = token.githubHandle as string | undefined;
      return session;
    },
    async jwt({ token, profile }) {
      const handle = (profile as { login?: string } | undefined)?.login;
      if (handle) token.githubHandle = handle;
      return token;
    },
  },
});
```

- [ ] **Step 6: Write app/api/auth/[...nextauth]/route.ts**

```typescript
// frontend/app/api/auth/[...nextauth]/route.ts
import { handlers } from "@/lib/auth";

export const { GET, POST } = handlers;
```

- [ ] **Step 7: Write middleware.ts (gates /admin)**

```typescript
// frontend/middleware.ts
import { auth } from "@/lib/auth";
import { NextResponse } from "next/server";

export default auth((req) => {
  const isAdmin = req.nextUrl.pathname.startsWith("/admin");
  if (!isAdmin) return NextResponse.next();

  const handle = (req.auth?.user as { githubHandle?: string } | undefined)
    ?.githubHandle;
  const allowed = (process.env.HERMES_ALLOWED_GITHUB_HANDLES || "techfreakworm")
    .split(",")
    .map((s) => s.trim());

  if (!handle || !allowed.includes(handle)) {
    const signin = new URL("/api/auth/signin", req.url);
    signin.searchParams.set("callbackUrl", req.nextUrl.pathname);
    return NextResponse.redirect(signin);
  }
  const res = NextResponse.next();
  res.headers.set("x-github-handle", handle);
  return res;
});

export const config = { matcher: ["/admin/:path*"] };
```

- [ ] **Step 8: Write lib/api.ts (forwards GitHub handle to backend)**

```typescript
// frontend/lib/api.ts
import { headers } from "next/headers";

const API_BASE = process.env.HERMES_API_BASE || "http://127.0.0.1:9000";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const h = await headers();
  const githubHandle = h.get("x-github-handle") || "";

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-GitHub-Handle": githubHandle,
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`api ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

export async function publicApi<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    next: { revalidate: 30 },
  });
  if (!res.ok) throw new Error(`public api ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}
```

- [ ] **Step 9: Write lib/sse.ts (client-side EventSource hook)**

```typescript
// frontend/lib/sse.ts
"use client";

import { useEffect, useRef, useState } from "react";

export type SseEvent = { type: string; data: unknown };

export function useSse(path: string, max = 100): SseEvent[] {
  const [events, setEvents] = useState<SseEvent[]>([]);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const es = new EventSource(path);
    esRef.current = es;

    const push = (type: string) => (msg: MessageEvent) => {
      let data: unknown = msg.data;
      try {
        data = JSON.parse(msg.data);
      } catch {
        /* keep as string */
      }
      setEvents((prev) => [...prev.slice(-(max - 1)), { type, data }]);
    };

    es.addEventListener("activity", push("activity"));
    es.addEventListener("ping", push("ping"));
    es.onerror = () => {
      // Browser auto-reconnects.
    };
    return () => es.close();
  }, [path, max]);

  return events;
}
```

- [ ] **Step 10: Smoke-build**

```bash
cd frontend
pnpm build
```
Expected: build completes with no type errors. (`next build` produces `.next/standalone`.)

- [ ] **Step 11: Commit**

```bash
git add frontend/
git commit -m "Next.js 16 scaffold + Tailwind + shadcn + Auth.js v5 + middleware"
```

---

### Task 36: Build the public landing page (showcase-grade hero)

**Files:**
- Create: `frontend/app/page.tsx`
- Create: `frontend/components/landing/Hero.tsx`
- Create: `frontend/components/landing/LiveStats.tsx`
- Create: `frontend/components/landing/Architecture.tsx`
- Create: `frontend/components/landing/Thesis.tsx`
- Create: `frontend/components/landing/DemoVideo.tsx`
- Create: `frontend/components/landing/Footer.tsx`

- [ ] **Step 1: Write app/page.tsx**

```tsx
// frontend/app/page.tsx
import { Hero } from "@/components/landing/Hero";
import { LiveStats } from "@/components/landing/LiveStats";
import { Architecture } from "@/components/landing/Architecture";
import { Thesis } from "@/components/landing/Thesis";
import { DemoVideo } from "@/components/landing/DemoVideo";
import { Footer } from "@/components/landing/Footer";

export default function HomePage() {
  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <Hero />
      <LiveStats />
      <Architecture />
      <Thesis />
      <DemoVideo />
      <Footer />
    </main>
  );
}
```

- [ ] **Step 2: Hero.tsx**

```tsx
// frontend/components/landing/Hero.tsx
"use client";
import { motion } from "framer-motion";

export function Hero() {
  return (
    <section className="container mx-auto px-6 pt-24 pb-16 max-w-5xl">
      <motion.h1
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6 }}
        className="text-4xl md:text-6xl font-bold tracking-tight leading-tight"
      >
        Hermes-Agent&apos;s value{" "}
        <span className="text-indigo-400">in 10% the code</span>,
        <br />
        by riding Claude Code&apos;s native rails.
      </motion.h1>
      <motion.p
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.6, delay: 0.2 }}
        className="mt-6 text-lg md:text-xl text-slate-400 max-w-3xl"
      >
        A messaging gateway, voice in/out, persistent project-leads with their own agent teams,
        all reachable from a phone — built as a Claude Code plugin instead of a 27,000-LOC platform.
      </motion.p>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.6, delay: 0.4 }}
        className="mt-10 flex gap-4 text-sm"
      >
        <a href="#thesis" className="text-indigo-300 underline underline-offset-4">
          Read the build log
        </a>
        <span className="text-slate-600">·</span>
        <a href="https://github.com/techfreakworm/hermes-claude"
           className="text-indigo-300 underline underline-offset-4">
          Source on GitHub
        </a>
      </motion.div>
    </section>
  );
}
```

- [ ] **Step 3: LiveStats.tsx (server component, fetches public stats)**

```tsx
// frontend/components/landing/LiveStats.tsx
import { publicApi } from "@/lib/api";

type Stats = {
  messages_today: number;
  active_projects: number;
  decisions_today: number;
  uptime_hours: number;
};

export async function LiveStats() {
  let stats: Stats;
  try {
    stats = await publicApi<Stats>("/api/public/stats");
  } catch {
    stats = { messages_today: 0, active_projects: 0,
              decisions_today: 0, uptime_hours: 0 };
  }
  const cells = [
    { label: "Messages today", value: stats.messages_today },
    { label: "Active project-leads", value: stats.active_projects },
    { label: "Agent decisions today", value: stats.decisions_today },
    { label: "Uptime today (hrs)", value: stats.uptime_hours },
  ];
  return (
    <section className="border-t border-b border-slate-800/60 bg-slate-900/40">
      <div className="container mx-auto px-6 py-12 max-w-5xl grid grid-cols-2 md:grid-cols-4 gap-6">
        {cells.map((c) => (
          <div key={c.label} className="">
            <div className="text-3xl md:text-4xl font-mono font-bold">
              {c.value.toLocaleString()}
            </div>
            <div className="text-xs uppercase tracking-wider text-slate-500 mt-1">
              {c.label}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
```

- [ ] **Step 4: Architecture.tsx (static interactive diagram)**

```tsx
// frontend/components/landing/Architecture.tsx
export function Architecture() {
  return (
    <section id="architecture" className="container mx-auto px-6 py-20 max-w-5xl">
      <h2 className="text-2xl md:text-3xl font-bold tracking-tight">Architecture</h2>
      <p className="mt-3 text-slate-400">
        One always-on machine. One persistent Claude session. A handful of MCP servers and
        skills. Everything else — channels, cron, teams, memory, mobile — is native to Claude Code.
      </p>
      <pre className="mt-8 overflow-x-auto rounded-lg bg-slate-900/80 p-6 text-xs leading-relaxed font-mono text-slate-300 border border-slate-800">
{`Telegram ──► claude --channels (OCI VPS, Max OAuth)
                │
                ├── voice_stt MCP ──► whisper.cpp
                ├── voice_tts MCP ──► piper
                ├── project_orchestrator MCP ──► spawns project-leads
                └── hermes_api MCP ──► FastAPI ──► claude.mayankgupta.in

Each project-lead is its own background claude session with its own
TeamCreate-instantiated team. The orchestrator never team-leads itself.`}
      </pre>
    </section>
  );
}
```

- [ ] **Step 5: Thesis.tsx**

```tsx
// frontend/components/landing/Thesis.tsx
export function Thesis() {
  return (
    <section id="thesis" className="border-t border-slate-800/60 bg-slate-900/30">
      <div className="container mx-auto px-6 py-20 max-w-3xl prose prose-invert prose-slate">
        <h2>The thesis</h2>
        <p>
          Hermes-Agent by Nous Research is a remarkable platform for self-improving
          messaging agents — 27,000 lines of Python implementing channels, cron, skill
          systems, memory curation, sandbox backends, and trajectory tooling for model
          training.
        </p>
        <p>
          After studying it, I asked: <em>what if the engine is Claude Code?</em> Claude
          Code already has channels (Telegram, Discord, iMessage, custom), agent teams,
          server-hosted scheduled routines, agent view, Remote Control, mobile push, MCP,
          hooks, plugins, and auto-memory. The platform layer collapses.
        </p>
        <p>
          Hermes-Claude is the answer: a Claude Code plugin (~4,000 LOC) that fills the
          last 5% — a voice pipeline, a project orchestrator, a dashboard, and curated
          workflows. No API keys, just Claude Max subscription. Runs on a single Oracle
          Cloud free-tier VPS. The 90% you&apos;re looking at on this page <em>is</em>
          Claude Code, not me.
        </p>
        <p>
          Trading integrations come in V2; this V1 is the platform demonstration.
        </p>
      </div>
    </section>
  );
}
```

- [ ] **Step 6: DemoVideo.tsx (placeholder for V1; real video V1.5)**

```tsx
// frontend/components/landing/DemoVideo.tsx
export function DemoVideo() {
  return (
    <section className="container mx-auto px-6 py-20 max-w-5xl">
      <h2 className="text-2xl md:text-3xl font-bold tracking-tight">See it run</h2>
      <p className="mt-3 text-slate-400">
        A 60-second walkthrough is in progress for V1.5. In the meantime, the dashboard
        is live — login is restricted, but the public stats above pull from the running
        system.
      </p>
      <div className="mt-8 aspect-video rounded-lg border border-dashed border-slate-700 bg-slate-900/50 grid place-items-center text-slate-500 font-mono text-sm">
        [demo video placeholder]
      </div>
    </section>
  );
}
```

- [ ] **Step 7: Footer.tsx**

```tsx
// frontend/components/landing/Footer.tsx
export function Footer() {
  return (
    <footer className="border-t border-slate-800/60">
      <div className="container mx-auto px-6 py-12 max-w-5xl flex flex-wrap gap-6 items-center justify-between text-sm text-slate-500">
        <div>
          Built by{" "}
          <a href="https://mayankgupta.in" className="text-slate-300 underline underline-offset-4">
            Mayank Gupta
          </a>
        </div>
        <div className="flex gap-4">
          <a href="https://github.com/techfreakworm/hermes-claude">GitHub</a>
          <a href="/admin">Admin (auth required)</a>
        </div>
      </div>
    </footer>
  );
}
```

- [ ] **Step 8: Commit**

```bash
git add frontend/app/page.tsx frontend/components/landing/
git commit -m "Landing page: hero, live stats, architecture, thesis, demo, footer"
```

---

### Task 37: Build /admin layout + overview page

**Files:**
- Create: `frontend/app/admin/layout.tsx`
- Create: `frontend/app/admin/page.tsx`
- Create: `frontend/components/admin/Sidebar.tsx`
- Create: `frontend/components/admin/KpiCard.tsx`
- Create: `frontend/components/admin/ActivityFeed.tsx`

- [ ] **Step 1: Admin layout**

```tsx
// frontend/app/admin/layout.tsx
import { Sidebar } from "@/components/admin/Sidebar";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex bg-slate-950 text-slate-100">
      <Sidebar />
      <main className="flex-1 overflow-y-auto">{children}</main>
    </div>
  );
}
```

- [ ] **Step 2: Sidebar.tsx**

```tsx
// frontend/components/admin/Sidebar.tsx
import Link from "next/link";

const NAV = [
  { href: "/admin", label: "Overview" },
  { href: "/admin/projects", label: "Projects" },
  { href: "/admin/conversations", label: "Conversations" },
  { href: "/admin/routines", label: "Routines" },
  { href: "/admin/usage", label: "Usage" },
  { href: "/admin/memory", label: "Memory" },
  { href: "/admin/logs", label: "Logs" },
];

export function Sidebar() {
  return (
    <aside className="w-56 shrink-0 border-r border-slate-800 bg-slate-900/30 px-4 py-6 sticky top-0 h-screen">
      <div className="text-sm font-mono text-slate-400 mb-6">hermes-claude/admin</div>
      <nav className="space-y-1">
        {NAV.map((item) => (
          <Link key={item.href} href={item.href}
                className="block px-3 py-2 rounded text-sm hover:bg-slate-800/60 text-slate-300">
            {item.label}
          </Link>
        ))}
      </nav>
    </aside>
  );
}
```

- [ ] **Step 3: KpiCard.tsx**

```tsx
// frontend/components/admin/KpiCard.tsx
export function KpiCard({ label, value, hint }: {
  label: string; value: string | number; hint?: string;
}) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-5">
      <div className="text-xs uppercase tracking-wider text-slate-500">{label}</div>
      <div className="mt-2 text-3xl font-mono font-bold text-slate-100">{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}
```

- [ ] **Step 4: ActivityFeed.tsx**

```tsx
// frontend/components/admin/ActivityFeed.tsx
"use client";
import { useSse } from "@/lib/sse";
import { motion, AnimatePresence } from "framer-motion";

type Activity = { ts?: string; tool?: string; session?: string };

export function ActivityFeed() {
  const events = useSse("/api/events", 30);
  const items = events
    .filter((e) => e.type === "activity")
    .map((e) => e.data as Activity);

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-4">
      <div className="text-xs uppercase tracking-wider text-slate-500 mb-3">
        Live activity
      </div>
      <ul className="space-y-1 font-mono text-xs text-slate-300 max-h-80 overflow-y-auto">
        <AnimatePresence initial={false}>
          {items.slice(-20).reverse().map((a, i) => (
            <motion.li key={`${a.ts}-${i}`}
                       initial={{ opacity: 0, x: -4 }}
                       animate={{ opacity: 1, x: 0 }}
                       exit={{ opacity: 0 }}>
              <span className="text-slate-500">{a.ts?.slice(11, 19)}</span>{" "}
              <span className="text-indigo-300">{a.tool}</span>{" "}
              <span className="text-slate-500">·</span>{" "}
              <span className="text-slate-400">{a.session?.slice(0, 8)}</span>
            </motion.li>
          ))}
        </AnimatePresence>
      </ul>
    </div>
  );
}
```

- [ ] **Step 5: app/admin/page.tsx (overview)**

```tsx
// frontend/app/admin/page.tsx
import { api } from "@/lib/api";
import { KpiCard } from "@/components/admin/KpiCard";
import { ActivityFeed } from "@/components/admin/ActivityFeed";

type Health = { status: string; uptime_seconds: number };
type Project = { name: string };
type Usage = { interactive: { remaining_pct: number } };

export default async function OverviewPage() {
  const [health, projects, usage] = await Promise.all([
    api<Health>("/api/healthz").catch(() => ({ status: "down", uptime_seconds: 0 })),
    api<Project[]>("/api/projects").catch(() => []),
    api<Usage>("/api/usage").catch(() =>
      ({ interactive: { remaining_pct: 100 } } as Usage)),
  ]);

  return (
    <div className="p-8 space-y-8">
      <header className="flex items-baseline justify-between">
        <h1 className="text-2xl font-bold">Overview</h1>
        <span className={`text-xs font-mono ${health.status === "ok" ? "text-emerald-400" : "text-rose-400"}`}>
          ● {health.status} · uptime {Math.round(health.uptime_seconds / 60)}m
        </span>
      </header>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KpiCard label="Active projects" value={projects.length} />
        <KpiCard label="Interactive credit left"
                 value={`${Math.round(usage.interactive.remaining_pct)}%`} />
        <KpiCard label="Uptime today"
                 value={`${Math.round(health.uptime_seconds / 3600)}h`} />
        <KpiCard label="Status" value={health.status} />
      </div>

      <ActivityFeed />
    </div>
  );
}
```

- [ ] **Step 6: Commit**

```bash
git add frontend/app/admin/ frontend/components/admin/
git commit -m "Admin: layout, sidebar, KPI cards, SSE activity feed, overview page"
```

---

### Task 38: Build /admin/projects with react-flow tree

**Files:**
- Create: `frontend/app/admin/projects/page.tsx`
- Create: `frontend/components/admin/ProjectTree.tsx`

- [ ] **Step 1: ProjectTree.tsx**

```tsx
// frontend/components/admin/ProjectTree.tsx
"use client";
import { useEffect, useMemo } from "react";
import ReactFlow, { Background, Controls, Edge, Node } from "reactflow";
import "reactflow/dist/style.css";

type Project = {
  name: string; agent_id: string; type: string; rc_url: string;
  status: string; idle_for_seconds: number;
};

const STATUS_COLOR: Record<string, string> = {
  active: "#34d399", idle: "#fbbf24", error: "#f43f5e", killed: "#64748b",
};

function statusOf(p: Project): string {
  if (p.status !== "active") return p.status;
  return p.idle_for_seconds > 3600 ? "idle" : "active";
}

export function ProjectTree({ projects }: { projects: Project[] }) {
  const { nodes, edges } = useMemo(() => {
    const n: Node[] = [
      {
        id: "orchestrator",
        position: { x: 0, y: 0 },
        data: { label: "telegram orchestrator" },
        style: {
          background: "#1e293b", color: "#f1f5f9",
          border: "2px solid #6366f1", padding: 12, borderRadius: 8,
          fontFamily: "monospace", fontSize: 12,
        },
      },
    ];
    const e: Edge[] = [];
    projects.forEach((p, idx) => {
      const status = statusOf(p);
      const color = STATUS_COLOR[status] || "#94a3b8";
      n.push({
        id: p.name,
        position: { x: (idx - (projects.length - 1) / 2) * 220, y: 200 },
        data: {
          label: (
            <div className="text-left">
              <div className="font-mono text-sm">{p.name}</div>
              <div className="text-xs text-slate-400">{p.type}</div>
              <div className="text-xs" style={{ color }}>● {status}</div>
              {p.rc_url && (
                <a href={p.rc_url} target="_blank" rel="noreferrer"
                   className="text-xs underline text-indigo-300">attach</a>
              )}
            </div>
          ),
        },
        style: {
          background: "#0f172a", color: "#e2e8f0",
          border: `2px solid ${color}`, padding: 10, borderRadius: 8,
          width: 200,
        },
      });
      e.push({
        id: `orch-${p.name}`, source: "orchestrator", target: p.name,
        style: { stroke: color, strokeWidth: 1.5 }, animated: status === "active",
      });
    });
    return { nodes: n, edges: e };
  }, [projects]);

  return (
    <div className="h-[600px] rounded-lg border border-slate-800 bg-slate-900/40">
      <ReactFlow nodes={nodes} edges={edges} fitView>
        <Background gap={24} color="#1e293b" />
        <Controls className="!bg-slate-800 !border-slate-700" />
      </ReactFlow>
    </div>
  );
}
```

- [ ] **Step 2: app/admin/projects/page.tsx**

```tsx
// frontend/app/admin/projects/page.tsx
import { api } from "@/lib/api";
import { ProjectTree } from "@/components/admin/ProjectTree";

type Project = {
  name: string; agent_id: string; type: string; rc_url: string;
  status: string; idle_for_seconds: number;
};

export default async function ProjectsPage() {
  const projects = await api<Project[]>("/api/projects").catch(() => [] as Project[]);
  return (
    <div className="p-8 space-y-6">
      <h1 className="text-2xl font-bold">Projects</h1>
      {projects.length === 0 ? (
        <div className="rounded-lg border border-dashed border-slate-700 p-8 text-slate-500 text-sm">
          No active project-leads. Ask the bot to spawn one from Telegram.
        </div>
      ) : (
        <ProjectTree projects={projects} />
      )}
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/app/admin/projects/ frontend/components/admin/ProjectTree.tsx
git commit -m "Admin: /projects react-flow tree with status colors and RC links"
```

---

### Task 39: Build /admin/conversations, /admin/routines, /admin/usage

**Files:**
- Create: `frontend/app/admin/conversations/page.tsx`
- Create: `frontend/app/admin/routines/page.tsx`
- Create: `frontend/app/admin/usage/page.tsx`
- Create: `frontend/components/admin/UsageChart.tsx`

- [ ] **Step 1: app/admin/conversations/page.tsx**

```tsx
// frontend/app/admin/conversations/page.tsx
import { api } from "@/lib/api";

type Thread = {
  thread_id: string; project: string; modified_at: number; size_bytes: number;
};

export default async function ConversationsPage() {
  const threads = await api<Thread[]>("/api/conversations").catch(() => []);
  return (
    <div className="p-8 space-y-6">
      <h1 className="text-2xl font-bold">Conversations</h1>
      <div className="rounded-lg border border-slate-800 bg-slate-900/40">
        {threads.length === 0 ? (
          <div className="p-8 text-slate-500 text-sm">No transcripts yet.</div>
        ) : (
          <ul className="divide-y divide-slate-800">
            {threads.map((t) => (
              <li key={`${t.project}-${t.thread_id}`} className="p-4 hover:bg-slate-800/40">
                <div className="font-mono text-sm">{t.thread_id}</div>
                <div className="text-xs text-slate-500 mt-1">
                  project: {t.project} · {new Date(t.modified_at * 1000).toLocaleString()}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: app/admin/routines/page.tsx**

```tsx
// frontend/app/admin/routines/page.tsx
import { api } from "@/lib/api";

type Routine = {
  id?: string; name?: string; cron?: string; next_run?: string;
};

export default async function RoutinesPage() {
  const routines = await api<Routine[]>("/api/routines").catch(() => []);
  return (
    <div className="p-8 space-y-6">
      <h1 className="text-2xl font-bold">Routines</h1>
      <p className="text-sm text-slate-500">
        Create routines from Telegram: &ldquo;schedule a morning brief every weekday at 9am.&rdquo;
      </p>
      <div className="rounded-lg border border-slate-800 bg-slate-900/40">
        {routines.length === 0 ? (
          <div className="p-8 text-slate-500 text-sm">No routines.</div>
        ) : (
          <ul className="divide-y divide-slate-800">
            {routines.map((r) => (
              <li key={r.id || r.name} className="p-4">
                <div className="font-mono text-sm">{r.name || r.id}</div>
                <div className="text-xs text-slate-500 mt-1">
                  cron: <code className="text-indigo-300">{r.cron}</code>
                  {r.next_run && <> · next: {r.next_run}</>}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: UsageChart.tsx**

```tsx
// frontend/components/admin/UsageChart.tsx
"use client";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
         ResponsiveContainer, Legend } from "recharts";

type Snapshot = {
  date: string;
  interactive_credits_used: number;
  agent_sdk_credits_used: number;
};

export function UsageChart({ trend }: { trend: Snapshot[] }) {
  const data = [...trend].reverse();
  return (
    <ResponsiveContainer width="100%" height={300}>
      <LineChart data={data} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
        <XAxis dataKey="date" stroke="#64748b" fontSize={11} />
        <YAxis stroke="#64748b" fontSize={11} />
        <Tooltip
          contentStyle={{ background: "#0f172a", border: "1px solid #334155",
                          fontFamily: "monospace", fontSize: 12 }}
        />
        <Legend />
        <Line type="monotone" dataKey="interactive_credits_used"
              name="Interactive" stroke="#6366f1" strokeWidth={2} dot={false} />
        <Line type="monotone" dataKey="agent_sdk_credits_used"
              name="Agent SDK" stroke="#f59e0b" strokeWidth={2} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
```

- [ ] **Step 4: app/admin/usage/page.tsx**

```tsx
// frontend/app/admin/usage/page.tsx
import { api } from "@/lib/api";
import { KpiCard } from "@/components/admin/KpiCard";
import { UsageChart } from "@/components/admin/UsageChart";

type Snapshot = {
  date: string;
  interactive_credits_used: number;
  interactive_ceiling: number;
  agent_sdk_credits_used: number;
  agent_sdk_ceiling: number;
};

type Usage = {
  interactive: { today: number; ceiling: number; remaining_pct: number };
  agent_sdk: { today: number; ceiling: number; remaining_pct: number };
  trend: Snapshot[];
};

export default async function UsagePage() {
  const usage = await api<Usage>("/api/usage").catch(() => ({
    interactive: { today: 0, ceiling: 0, remaining_pct: 100 },
    agent_sdk: { today: 0, ceiling: 0, remaining_pct: 100 },
    trend: [] as Snapshot[],
  }));
  return (
    <div className="p-8 space-y-6">
      <h1 className="text-2xl font-bold">Usage</h1>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KpiCard label="Interactive used today"
                 value={Math.round(usage.interactive.today)}
                 hint={`${Math.round(usage.interactive.remaining_pct)}% left`} />
        <KpiCard label="Agent SDK used today"
                 value={Math.round(usage.agent_sdk.today)}
                 hint={`${Math.round(usage.agent_sdk.remaining_pct)}% left`} />
        <KpiCard label="Interactive ceiling"
                 value={Math.round(usage.interactive.ceiling)} />
        <KpiCard label="Agent SDK ceiling"
                 value={Math.round(usage.agent_sdk.ceiling)} />
      </div>
      <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-6">
        <h2 className="text-xs uppercase tracking-wider text-slate-500 mb-4">
          30-day trend
        </h2>
        <UsageChart trend={usage.trend} />
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Commit**

```bash
git add frontend/app/admin/conversations/ frontend/app/admin/routines/ \
        frontend/app/admin/usage/ frontend/components/admin/UsageChart.tsx
git commit -m "Admin: /conversations, /routines, /usage pages with Recharts"
```

---

### Task 40: Build /admin/memory + /admin/logs

**Files:**
- Create: `frontend/app/admin/memory/page.tsx`
- Create: `frontend/app/admin/logs/page.tsx`

- [ ] **Step 1: app/admin/memory/page.tsx (read-only V1)**

```tsx
// frontend/app/admin/memory/page.tsx
import { api } from "@/lib/api";

type MemoryResp = { project: string; text: string };

export default async function MemoryPage({
  searchParams,
}: { searchParams: Promise<{ project?: string }> }) {
  const sp = await searchParams;
  const project = sp.project || "default";
  const mem = await api<MemoryResp>(`/api/memory/${project}`).catch(() => ({
    project, text: "",
  }));
  return (
    <div className="p-8 space-y-6">
      <h1 className="text-2xl font-bold">Memory</h1>
      <p className="text-sm text-slate-500">
        Read-only view in V1. Edit via Telegram or `/memory` slash command in a
        Claude session. Use ?project=&lt;slug&gt; to switch.
      </p>
      <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-6">
        <div className="text-xs uppercase tracking-wider text-slate-500 mb-3">
          {mem.project}
        </div>
        <pre className="text-sm font-mono text-slate-300 whitespace-pre-wrap">
          {mem.text || "(empty)"}
        </pre>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: app/admin/logs/page.tsx**

```tsx
// frontend/app/admin/logs/page.tsx
import { api } from "@/lib/api";

type LogRow = {
  ts: string; tool: string; session: string;
  input_summary?: unknown; result_summary?: string;
};

export default async function LogsPage() {
  const rows = await api<LogRow[]>("/api/logs?limit=200").catch(() => []);
  return (
    <div className="p-8 space-y-6">
      <h1 className="text-2xl font-bold">Logs</h1>
      <p className="text-sm text-slate-500">
        Tool-call activity feed (most recent 200). Filter UI in V1.5.
      </p>
      <div className="rounded-lg border border-slate-800 bg-slate-900/40 overflow-hidden">
        <ul className="divide-y divide-slate-800 font-mono text-xs">
          {rows.length === 0 ? (
            <li className="p-8 text-slate-500">No activity yet.</li>
          ) : (
            rows.slice().reverse().map((r, i) => (
              <li key={`${r.ts}-${i}`} className="px-4 py-2 hover:bg-slate-800/40">
                <span className="text-slate-500">{r.ts.slice(11, 19)}</span>{" "}
                <span className="text-indigo-300">{r.tool}</span>{" "}
                <span className="text-slate-500">·</span>{" "}
                <span className="text-slate-400">{r.session.slice(0, 8)}</span>
                {r.result_summary && (
                  <span className="text-slate-500 ml-2">→ {r.result_summary.slice(0, 100)}</span>
                )}
              </li>
            ))
          )}
        </ul>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/app/admin/memory/ frontend/app/admin/logs/
git commit -m "Admin: /memory (read-only) + /logs (200 most recent)"
```

---

### Task 41: Frontend systemd unit + Caddyfile + deploy

**Files:**
- Create: `systemd/hermes-claude-frontend.service`
- Create: `Caddyfile`

- [ ] **Step 1: systemd unit**

```ini
# systemd/hermes-claude-frontend.service
[Unit]
Description=Hermes-Claude Next.js dashboard
After=network-online.target hermes-claude-api.service
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/hermes-claude/frontend
EnvironmentFile=/etc/hermes-claude/secrets.env
Environment=NODE_ENV=production
Environment=HOSTNAME=127.0.0.1
Environment=PORT=3000
Environment=HERMES_API_BASE=http://127.0.0.1:9000
Environment=HERMES_ALLOWED_GITHUB_HANDLES=techfreakworm
Environment=AUTH_TRUST_HOST=true
ExecStart=/usr/bin/node .next/standalone/server.js
Restart=always
RestartSec=5
StandardOutput=append:/var/log/hermes-claude/frontend.log
StandardError=append:/var/log/hermes-claude/frontend.err.log

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Caddyfile**

```
# Caddyfile
{
    email mayank@mayankgupta.in
}

claude.mayankgupta.in {
    handle_path /api/* {
        reverse_proxy localhost:9000
    }
    handle {
        reverse_proxy localhost:3000
    }
    encode gzip zstd
    log {
        output file /var/log/caddy/access.log {
            roll_size 50mb
            roll_keep 10
        }
    }
}
```

- [ ] **Step 3: GitHub OAuth app — register**

Go to <https://github.com/settings/developers> → New OAuth App:
- Application name: `Hermes-Claude`
- Homepage: `https://claude.mayankgupta.in`
- Callback URL: `https://claude.mayankgupta.in/api/auth/callback/github`

Copy Client ID and Client Secret. Append to `/etc/hermes-claude/secrets.env` on VPS:

```bash
ssh oci-hermes sudo bash -c '
echo "AUTH_GITHUB_ID=<paste-client-id>" >> /etc/hermes-claude/secrets.env
echo "AUTH_GITHUB_SECRET=<paste-client-secret>" >> /etc/hermes-claude/secrets.env
echo "AUTH_SECRET=$(openssl rand -hex 32)" >> /etc/hermes-claude/secrets.env
echo "AUTH_URL=https://claude.mayankgupta.in" >> /etc/hermes-claude/secrets.env
'
```

- [ ] **Step 4: Point DNS**

In your domain DNS (mayankgupta.in's provider), add an A record:
- Name: `claude`
- Type: A
- Value: `<OCI public IP>`
- TTL: 300

Wait ~5 min, verify: `dig +short claude.mayankgupta.in`

- [ ] **Step 5: Deploy and start frontend**

```bash
./scripts/deploy.sh

# On VPS, build frontend
ssh oci-hermes bash <<'EOF'
cd /opt/hermes-claude/frontend
pnpm install --prod=false
pnpm build
EOF

scp systemd/hermes-claude-frontend.service oci-hermes:/tmp/
scp Caddyfile oci-hermes:/tmp/

ssh oci-hermes bash <<'EOF'
sudo install -m 644 /tmp/hermes-claude-frontend.service /etc/systemd/system/
sudo install -m 644 /tmp/Caddyfile /etc/caddy/Caddyfile
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-claude-frontend.service
sudo systemctl reload caddy
EOF

sleep 10
curl -s https://claude.mayankgupta.in/api/healthz | jq .
```
Expected: `{"status":"ok", ...}` over HTTPS.

- [ ] **Step 6: Smoke test in browser**

Open `https://claude.mayankgupta.in/` — landing page renders, live stats fetch from API.
Open `https://claude.mayankgupta.in/admin` — redirects to GitHub OAuth; after sign-in,
admin loads if your handle matches.

- [ ] **Step 7: Commit**

```bash
git add systemd/hermes-claude-frontend.service Caddyfile
git commit -m "Frontend systemd + Caddyfile (auto-HTTPS for claude.mayankgupta.in)"
git tag week-3-complete
```

(Week 3 done.)

---

## Week 4 — Polish, Setup Wizard, Scheduled Jobs, Smoke Test

Goal of Week 4: a clean Ubuntu VPS reaches a fully working hermes-claude in ~30 minutes via the setup wizard; scheduled jobs run reliably; idle reaper hibernates drifting projects; README pitches the project for OSS launch.

### Task 42: Write content-drafter + tool-builder subagent templates

**Files:**
- Create: `agents/content-drafter.md`
- Create: `agents/tool-builder.md`

- [ ] **Step 1: content-drafter.md**

````markdown
---
name: content-drafter
description: |
  Drafts social, blog, or technical content in the user's voice. Use when the
  user asks "draft a tweet about X", "write a blog post on Y", "compose a
  LinkedIn post about Z", or similar content-creation tasks.
model: opus
tools: Read, WebFetch, Write
---

# content-drafter

You are a content drafter for Mayank Gupta — TPM, AI/ML engineer, financial
engineer with 7+ years of experience, building Hermes-Claude as a portfolio
showcase. Your voice:

- Honest and technically specific. No hype, no buzzwords without backing.
- Lead with the insight, not the announcement. "X turned out to be Y" beats
  "Excited to announce X."
- Code-aware. Use precise terms (route handler vs middleware, MCP vs SDK).
- Light personality, no emoji, no exclamation marks.

## Process

1. Ask the user (if not already specified): platform (X / LinkedIn / Medium /
   blog), word target, and the core insight to lead with.

2. Draft. Output a single block of text, no meta-commentary.

3. Offer two variations if the user is undecided: one tighter, one more
   illustrative.

## Hard rules

- No "I'm excited to share" / "Thrilled to announce" / "Game-changer".
- No emoji.
- No exclamation marks unless quoting someone.
- For X threads: count chars per tweet (280 max accounting for t.co URL
  shortening to 23 chars).
````

- [ ] **Step 2: tool-builder.md**

````markdown
---
name: tool-builder
description: |
  Scaffolds a new MCP server skeleton when the user says "I need a new MCP for
  X", "build an MCP that does Y", or "wrap Z in an MCP".
model: opus
tools: Read, Write, Edit, Bash
permissionMode: acceptEdits
---

# tool-builder

You scaffold MCP servers in Python following the hermes-claude conventions.

## Process

1. Ask the user (if unclear): name of the MCP, the external API/system it
   wraps, and the 1-5 tools it should expose.

2. Create a new subdirectory under `src/hermes_claude/mcp_servers/<name>/`:

   - `__init__.py` (empty)
   - `server.py` — FastMCP server with tool stubs
   - Any supporting modules named by responsibility

3. Add an entry to `.mcp.json`:

```json
"<name>": {
  "type": "stdio",
  "command": "/opt/hermes-claude/.venv/bin/python",
  "args": ["-m", "hermes_claude.mcp_servers.<name>.server"]
}
```

4. Add a test stub at `tests/mcp_servers/test_<name>.py` with one
   failing test per tool.

5. Tell the user: "Skeleton ready at <path>. Implement the tool bodies,
   then `pytest tests/mcp_servers/test_<name>.py -v`."

## Template (use as the starting server.py)

```python
from __future__ import annotations
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("<name>")

@mcp.tool()
def <tool_name>(<args>) -> dict:
    """<short description>"""
    raise NotImplementedError

def main() -> None:
    mcp.run()

if __name__ == "__main__":
    main()
```
````

- [ ] **Step 3: Commit**

```bash
git add agents/content-drafter.md agents/tool-builder.md
git commit -m "Add content-drafter and tool-builder subagent templates"
```

---

### Task 43: Write reaper.py (idle project hibernation)

**Files:**
- Create: `scripts/reaper.py`
- Create: `tests/scripts/test_reaper.py`

- [ ] **Step 1: Write failing test**

```python
# tests/scripts/test_reaper.py
from __future__ import annotations

import time
from pathlib import Path

from hermes_claude.mcp_servers.project_orchestrator.registry import Registry

# Import reaper module (placed at scripts/reaper.py and importable as 'reaper' once on path)
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import reaper  # type: ignore


def test_reaper_hibernates_after_threshold(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "reg.sqlite"
    archive_root = tmp_path / "archive"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))
    monkeypatch.setenv("HERMES_ARCHIVE_ROOT", str(archive_root))
    r = Registry(db)
    r.register("oldp", agent_id="a-1", type_="custom",
               cwd=str(tmp_path / "oldp-cwd"), rc_url="https://r/a-1")
    (tmp_path / "oldp-cwd").mkdir()
    # Force last_activity into the past:
    r._conn.execute(
        "UPDATE projects SET last_activity = ? WHERE name = ?",
        (time.time() - (25 * 3600), "oldp"),
    )

    counts = reaper.run_once(idle_hibernate_seconds=24 * 3600,
                             idle_delete_seconds=7 * 24 * 3600)
    assert counts["hibernated"] == 1
    p = r.get("oldp")
    assert p is not None and p["status"] == "killed"


def test_reaper_keeps_recent_project(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "reg.sqlite"
    monkeypatch.setenv("HERMES_ORCH_DB", str(db))
    r = Registry(db)
    r.register("freshp", agent_id="a-2", type_="custom",
               cwd=str(tmp_path / "fresh-cwd"), rc_url="https://r/a-2")
    counts = reaper.run_once(idle_hibernate_seconds=24 * 3600,
                             idle_delete_seconds=7 * 24 * 3600)
    assert counts["hibernated"] == 0
    assert r.get("freshp")["status"] == "active"
```

- [ ] **Step 2: Run; expect ImportError**

```bash
pytest tests/scripts/test_reaper.py -v
```

- [ ] **Step 3: Implement scripts/reaper.py**

```python
#!/usr/bin/env python3
# scripts/reaper.py
"""Hibernate idle project-leads and delete long-dead ones.

Invoked every 6h by systemd timer.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from hermes_claude.mcp_servers.project_orchestrator.registry import Registry


DB = os.environ.get("HERMES_ORCH_DB", "/opt/hermes-claude/registry.sqlite")
ARCHIVE_ROOT = Path(os.environ.get(
    "HERMES_ARCHIVE_ROOT", "/opt/hermes-claude/archive"
))


def _archive_project_memory(name: str, cwd: str) -> None:
    src = Path(cwd) / ".claude"
    if not src.exists():
        return
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    dst = ARCHIVE_ROOT / f"{name}-{int(time.time())}"
    try:
        shutil.copytree(src, dst, dirs_exist_ok=False)
    except (FileExistsError, OSError):
        pass


def run_once(
    idle_hibernate_seconds: float = 24 * 3600,
    idle_delete_seconds: float = 7 * 24 * 3600,
) -> dict:
    reg = Registry(DB)
    now = time.time()
    hibernated = 0
    deleted = 0
    try:
        for p in reg.list_all():
            if p["status"] != "active":
                # Already-killed projects past delete threshold get hard-removed.
                if now - float(p["last_activity"]) > idle_delete_seconds:
                    reg.delete(p["name"])
                    deleted += 1
                continue
            idle = now - float(p["last_activity"])
            if idle > idle_hibernate_seconds:
                _archive_project_memory(p["name"], p["cwd"])
                reg.set_status(p["name"], "killed")
                hibernated += 1
    finally:
        reg.close()
    return {"hibernated": hibernated, "deleted": deleted}


def main() -> None:
    counts = run_once()
    print(f"reaper: hibernated={counts['hibernated']} deleted={counts['deleted']}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Make executable**

```bash
chmod +x scripts/reaper.py
mkdir -p tests/scripts
touch tests/scripts/__init__.py
```

- [ ] **Step 5: Run tests to verify pass**

```bash
pytest tests/scripts/test_reaper.py -v
```
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add scripts/reaper.py tests/scripts/test_reaper.py tests/scripts/__init__.py
git commit -m "Add reaper.py: hibernate idle >24h, hard-delete killed >7d"
```

---

### Task 44: Write healthcheck.sh + cache_refresh.py + usage_snapshot.py

**Files:**
- Create: `scripts/healthcheck.sh`
- Create: `scripts/cache_refresh.py`
- Create: `scripts/usage_snapshot.py`

- [ ] **Step 1: healthcheck.sh**

```bash
#!/usr/bin/env bash
# scripts/healthcheck.sh
#
# Verify channel session, api, and frontend are responsive. Restart channel
# if it's not. Logs to /var/log/hermes-claude/healthcheck.log.

set -uo pipefail

LOG=/var/log/hermes-claude/healthcheck.log
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# 1. API healthz
if ! curl -fsS --max-time 5 http://127.0.0.1:9000/api/healthz >/dev/null; then
    echo "[$TS] api: UNHEALTHY, restarting" >> "$LOG"
    sudo systemctl restart hermes-claude-api.service
fi

# 2. Frontend home (should serve 200)
if ! curl -fsS --max-time 5 http://127.0.0.1:3000/ -o /dev/null; then
    echo "[$TS] frontend: UNHEALTHY, restarting" >> "$LOG"
    sudo systemctl restart hermes-claude-frontend.service
fi

# 3. Channel tmux session present
if ! tmux has-session -t hermes 2>/dev/null; then
    echo "[$TS] channel: tmux missing, restarting" >> "$LOG"
    sudo systemctl restart hermes-claude-channel.service
fi

echo "[$TS] healthcheck: ok" >> "$LOG"
```

- [ ] **Step 2: cache_refresh.py**

```python
#!/usr/bin/env python3
# scripts/cache_refresh.py
"""Prime hot paths in the dashboard API so the first user click is fast."""
from __future__ import annotations

import sys
import urllib.request


PATHS = [
    "/api/healthz",
    "/api/public/stats",
]


def main() -> int:
    code = 0
    for p in PATHS:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:9000{p}", timeout=5) as r:
                r.read()
        except Exception as e:  # noqa: BLE001
            print(f"cache_refresh: {p} failed: {e}", file=sys.stderr)
            code = 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: usage_snapshot.py**

```python
#!/usr/bin/env python3
# scripts/usage_snapshot.py
"""Record a daily usage snapshot to /opt/hermes-claude/usage.sqlite.

Runs at 23:55 IST via systemd timer. Calls `claude -p '/usage'` once.
Parses the JSON output and writes a row.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import date


DB = os.environ.get("HERMES_USAGE_DB", "/opt/hermes-claude/usage.sqlite")
CLAUDE = os.environ.get("HERMES_CLAUDE_BIN", "claude")


SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_snapshots(
    date TEXT PRIMARY KEY,
    interactive_credits_used REAL DEFAULT 0,
    interactive_ceiling REAL DEFAULT 0,
    agent_sdk_credits_used REAL DEFAULT 0,
    agent_sdk_ceiling REAL DEFAULT 0,
    recorded_at REAL DEFAULT 0
);
"""


def _query_usage() -> dict:
    r = subprocess.run(
        [CLAUDE, "-p", "/usage", "--output-format", "json"],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        raise RuntimeError(f"claude -p /usage failed: {r.stderr[:500]}")
    last = r.stdout.strip().splitlines()[-1]
    return json.loads(last)


def _extract(payload: dict) -> tuple[float, float, float, float]:
    """Pull bucket numbers out of /usage JSON. Tolerant to schema variation."""
    interactive_used = float(payload.get("interactive_credits_used", 0) or 0)
    interactive_max = float(payload.get("interactive_credits_ceiling", 0) or 0)
    sdk_used = float(payload.get("agent_sdk_credits_used", 0) or 0)
    sdk_max = float(payload.get("agent_sdk_credits_ceiling", 0) or 0)
    return interactive_used, interactive_max, sdk_used, sdk_max


def main() -> int:
    try:
        payload = _query_usage()
    except Exception as e:  # noqa: BLE001
        print(f"usage_snapshot: query failed: {e}", file=sys.stderr)
        return 1

    iu, ic, su, sc = _extract(payload)
    conn = sqlite3.connect(DB, isolation_level=None)
    conn.executescript(SCHEMA)
    conn.execute(
        """
        INSERT INTO daily_snapshots(date, interactive_credits_used,
            interactive_ceiling, agent_sdk_credits_used, agent_sdk_ceiling,
            recorded_at)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            interactive_credits_used=excluded.interactive_credits_used,
            interactive_ceiling=excluded.interactive_ceiling,
            agent_sdk_credits_used=excluded.agent_sdk_credits_used,
            agent_sdk_ceiling=excluded.agent_sdk_ceiling,
            recorded_at=excluded.recorded_at
        """,
        (date.today().isoformat(), iu, ic, su, sc, time.time()),
    )
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Make scripts executable**

```bash
chmod +x scripts/healthcheck.sh scripts/cache_refresh.py scripts/usage_snapshot.py
```

- [ ] **Step 5: Commit**

```bash
git add scripts/healthcheck.sh scripts/cache_refresh.py scripts/usage_snapshot.py
git commit -m "Add healthcheck.sh, cache_refresh.py, usage_snapshot.py scripts"
```

---

### Task 45: Write systemd timers (4 timers)

**Files:**
- Create: `systemd/hermes-healthcheck.service`
- Create: `systemd/hermes-healthcheck.timer`
- Create: `systemd/hermes-cache-refresh.service`
- Create: `systemd/hermes-cache-refresh.timer`
- Create: `systemd/hermes-usage-snapshot.service`
- Create: `systemd/hermes-usage-snapshot.timer`
- Create: `systemd/hermes-idle-reaper.service`
- Create: `systemd/hermes-idle-reaper.timer`

- [ ] **Step 1: hermes-healthcheck.{service,timer}**

```ini
# systemd/hermes-healthcheck.service
[Unit]
Description=Hermes-Claude health check
[Service]
Type=oneshot
ExecStart=/opt/hermes-claude/scripts/healthcheck.sh
```

```ini
# systemd/hermes-healthcheck.timer
[Unit]
Description=Run Hermes-Claude health check every 10 minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=10min
Unit=hermes-healthcheck.service
[Install]
WantedBy=timers.target
```

- [ ] **Step 2: hermes-cache-refresh.{service,timer}**

```ini
# systemd/hermes-cache-refresh.service
[Unit]
Description=Hermes-Claude dashboard cache refresh
[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/opt/hermes-claude
Environment=PATH=/opt/hermes-claude/.venv/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/opt/hermes-claude/scripts/cache_refresh.py
```

```ini
# systemd/hermes-cache-refresh.timer
[Unit]
Description=Refresh Hermes-Claude dashboard caches every 5 minutes
[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
Unit=hermes-cache-refresh.service
[Install]
WantedBy=timers.target
```

- [ ] **Step 3: hermes-usage-snapshot.{service,timer}**

```ini
# systemd/hermes-usage-snapshot.service
[Unit]
Description=Hermes-Claude daily usage snapshot
[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/opt/hermes-claude
EnvironmentFile=/etc/hermes-claude/secrets.env
Environment=PATH=/opt/hermes-claude/.venv/bin:/usr/local/bin:/usr/bin:/bin
Environment=HERMES_USAGE_DB=/opt/hermes-claude/usage.sqlite
ExecStart=/opt/hermes-claude/.venv/bin/python /opt/hermes-claude/scripts/usage_snapshot.py
```

```ini
# systemd/hermes-usage-snapshot.timer
[Unit]
Description=Daily Hermes-Claude usage snapshot at 23:55 local
[Timer]
OnCalendar=*-*-* 23:55:00
Persistent=true
Unit=hermes-usage-snapshot.service
[Install]
WantedBy=timers.target
```

- [ ] **Step 4: hermes-idle-reaper.{service,timer}**

```ini
# systemd/hermes-idle-reaper.service
[Unit]
Description=Hermes-Claude idle project reaper
[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/opt/hermes-claude
Environment=PATH=/opt/hermes-claude/.venv/bin:/usr/local/bin:/usr/bin:/bin
Environment=HERMES_ORCH_DB=/opt/hermes-claude/registry.sqlite
Environment=HERMES_ARCHIVE_ROOT=/opt/hermes-claude/archive
ExecStart=/opt/hermes-claude/.venv/bin/python /opt/hermes-claude/scripts/reaper.py
```

```ini
# systemd/hermes-idle-reaper.timer
[Unit]
Description=Reap idle Hermes-Claude project-leads every 6 hours
[Timer]
OnBootSec=15min
OnUnitActiveSec=6h
Unit=hermes-idle-reaper.service
[Install]
WantedBy=timers.target
```

- [ ] **Step 5: Install all on the VPS**

```bash
scp systemd/*.service systemd/*.timer oci-hermes:/tmp/
ssh oci-hermes bash <<'EOF'
sudo install -m 644 /tmp/hermes-healthcheck.service /etc/systemd/system/
sudo install -m 644 /tmp/hermes-healthcheck.timer /etc/systemd/system/
sudo install -m 644 /tmp/hermes-cache-refresh.service /etc/systemd/system/
sudo install -m 644 /tmp/hermes-cache-refresh.timer /etc/systemd/system/
sudo install -m 644 /tmp/hermes-usage-snapshot.service /etc/systemd/system/
sudo install -m 644 /tmp/hermes-usage-snapshot.timer /etc/systemd/system/
sudo install -m 644 /tmp/hermes-idle-reaper.service /etc/systemd/system/
sudo install -m 644 /tmp/hermes-idle-reaper.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-healthcheck.timer hermes-cache-refresh.timer \
    hermes-usage-snapshot.timer hermes-idle-reaper.timer
sudo systemctl list-timers --no-pager | grep hermes
EOF
```
Expected: list shows all 4 timers next-run times.

- [ ] **Step 6: Commit**

```bash
git add systemd/hermes-healthcheck.* systemd/hermes-cache-refresh.* \
        systemd/hermes-usage-snapshot.* systemd/hermes-idle-reaper.*
git commit -m "Add 4 systemd timers (healthcheck, cache-refresh, usage-snapshot, reaper)"
```

---

### Task 46: Create the 3 RemoteTrigger routines

**Files:** none — done via Telegram + RemoteTrigger MCP tool

- [ ] **Step 1: Morning brief routine**

In Telegram, send:

> "Schedule a daily routine every weekday at 8am IST: run the portfolio-status skill and send the result to me on Telegram. Name the routine 'morning-brief'."

Claude invokes `schedule-routine` skill. Verify in `/admin/routines` that it appears with cron `7 8 * * 1-5` (or similar off-minute).

- [ ] **Step 2: Memory consolidation routine**

In Telegram, send:

> "Every Sunday at 11am IST, run the memory-consolidation routine: review my MEMORY.md across all projects, remove stale entries, and report what changed. Name it 'memory-consolidation'."

Claude creates a routine with cron `13 11 * * 0`.

- [ ] **Step 3: OAuth token expiry warning**

In Telegram, send:

> "Every month on the 1st at 10am IST, check how many days until my CLAUDE_CODE_OAUTH_TOKEN expires. If less than 60 days, tell me to refresh. Name it 'oauth-expiry-check'."

Claude creates a routine with cron `17 10 1 * *`.

- [ ] **Step 4: Verify in dashboard**

Open `https://claude.mayankgupta.in/admin/routines` — three routines listed.

- [ ] **Step 5: No commit (routines stored on Anthropic infrastructure, not in repo)**

---

### Task 47: Write the setup wizard (hermes-init)

**Files:**
- Create: `src/hermes_claude/wizard/init.py`
- Create: `tests/wizard/test_init.py`
- Create: `tests/wizard/__init__.py`

- [ ] **Step 1: Write a focused test**

```python
# tests/wizard/test_init.py
from __future__ import annotations

from pathlib import Path

from hermes_claude.wizard.init import (
    render_systemd_unit, render_caddyfile, validate_domain
)


def test_validate_domain_accepts_subdomain() -> None:
    assert validate_domain("claude.mayankgupta.in") is True


def test_validate_domain_rejects_invalid() -> None:
    assert validate_domain("not a domain") is False
    assert validate_domain("") is False


def test_render_caddyfile_includes_domain() -> None:
    out = render_caddyfile(domain="claude.mayankgupta.in", email="x@y.com")
    assert "claude.mayankgupta.in" in out
    assert "reverse_proxy localhost:9000" in out
    assert "reverse_proxy localhost:3000" in out


def test_render_systemd_unit_substitutes_paths() -> None:
    out = render_systemd_unit(
        name="hermes-claude-api", description="API",
        exec_start="/usr/bin/echo ok",
    )
    assert "ExecStart=/usr/bin/echo ok" in out
    assert "Description=API" in out
```

- [ ] **Step 2: Run; expect ImportError**

```bash
mkdir -p tests/wizard
touch tests/wizard/__init__.py
pytest tests/wizard/test_init.py -v
```

- [ ] **Step 3: Implement init.py**

```python
# src/hermes_claude/wizard/init.py
"""Interactive setup wizard: clean Ubuntu VPS → working hermes-claude in ~30 min.

Designed to be re-run safely (idempotent) on the OCI VPS. Reads/writes
/etc/hermes-claude/secrets.env, /etc/systemd/system/hermes-*, /etc/caddy/Caddyfile.

Run as: sudo hermes-init
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from typing import Iterable


REPO_ROOT = Path("/opt/hermes-claude")
SECRETS = Path("/etc/hermes-claude/secrets.env")


# ---- pure helpers (testable) ----------------------------------------------

DOMAIN_RX = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)


def validate_domain(d: str) -> bool:
    return bool(DOMAIN_RX.match(d))


def render_caddyfile(domain: str, email: str = "") -> str:
    email_block = f"    email {email}\n" if email else ""
    return dedent(f"""\
        {{
        {email_block}}}

        {domain} {{
            handle_path /api/* {{
                reverse_proxy localhost:9000
            }}
            handle {{
                reverse_proxy localhost:3000
            }}
            encode gzip zstd
            log {{
                output file /var/log/caddy/access.log
            }}
        }}
        """)


def render_systemd_unit(*, name: str, description: str, exec_start: str,
                        type_: str = "simple", user: str = "ubuntu",
                        env_file: str = "/etc/hermes-claude/secrets.env",
                        wd: str = "/opt/hermes-claude",
                        restart_sec: int = 5) -> str:
    return dedent(f"""\
        [Unit]
        Description={description}
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type={type_}
        User={user}
        Group={user}
        WorkingDirectory={wd}
        EnvironmentFile={env_file}
        Environment=PATH=/opt/hermes-claude/.venv/bin:/usr/local/bin:/usr/bin:/bin
        ExecStart={exec_start}
        Restart=always
        RestartSec={restart_sec}
        StandardOutput=append:/var/log/hermes-claude/{name}.log
        StandardError=append:/var/log/hermes-claude/{name}.err.log

        [Install]
        WantedBy=multi-user.target
        """)


# ---- I/O wrappers ---------------------------------------------------------

def prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{label}{suffix}: ").strip()
    return val or default


def confirm(label: str, default: bool = True) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    val = input(f"{label}{suffix}: ").strip().lower()
    if not val:
        return default
    return val.startswith("y")


def write_secret(name: str, value: str) -> None:
    SECRETS.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if SECRETS.exists():
        for ln in SECRETS.read_text().splitlines():
            if not ln.strip() or ln.strip().startswith("#") or "=" not in ln:
                lines.append(ln)
                continue
            k, _, _ = ln.partition("=")
            if k.strip() != name:
                lines.append(ln)
    lines.append(f"{name}={value}")
    SECRETS.write_text("\n".join(lines) + "\n")
    os.chmod(SECRETS, 0o600)


def install_units(units: Iterable[tuple[str, str]]) -> None:
    """Install systemd unit files. Each entry is (path, contents)."""
    for path, contents in units:
        target = Path("/etc/systemd/system") / Path(path).name
        target.write_text(contents)
        target.chmod(0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)


def enable_services(names: Iterable[str]) -> None:
    cmd = ["systemctl", "enable", "--now", *names]
    subprocess.run(cmd, check=True)


# ---- wizard flow ----------------------------------------------------------

def run() -> int:
    print("Hermes-Claude setup wizard")
    print("==========================\n")

    domain = prompt("Public dashboard domain", "claude.mayankgupta.in")
    if not validate_domain(domain):
        print(f"invalid domain: {domain!r}", file=sys.stderr)
        return 1

    email = prompt("Email for Let's Encrypt (optional)", "")

    print("\nMint a Claude Max OAuth token on a browser-capable machine:")
    print("    $ claude setup-token")
    print("Then paste the token here. (Starts with 'oat-')\n")
    token = prompt("CLAUDE_CODE_OAUTH_TOKEN", "")
    if token:
        write_secret("CLAUDE_CODE_OAUTH_TOKEN", token)

    print("\nGitHub OAuth app:")
    print("  1. https://github.com/settings/developers → New OAuth App")
    print(f"  2. Callback: https://{domain}/api/auth/callback/github")
    gh_id = prompt("AUTH_GITHUB_ID", "")
    gh_secret = prompt("AUTH_GITHUB_SECRET", "")
    if gh_id: write_secret("AUTH_GITHUB_ID", gh_id)
    if gh_secret: write_secret("AUTH_GITHUB_SECRET", gh_secret)
    write_secret("AUTH_SECRET",
                 subprocess.check_output(["openssl", "rand", "-hex", "32"],
                                         text=True).strip())
    write_secret("AUTH_URL", f"https://{domain}")
    write_secret("AUTH_TRUST_HOST", "true")

    print("\nTelegram bot:")
    print("  1. In Telegram, message @BotFather: /newbot")
    print("  2. Save the bot token printed (starts with 123:ABC).")
    print("  3. Run:  claude   then  /plugin install telegram@claude-plugins-official")
    print("     then  /telegram:configure <bot-token>")
    print("     then DM the bot once, get pairing code, run /telegram:access pair <code>")
    print("     then  /telegram:access policy allowlist")
    input("\nPress Enter once Telegram is configured ... ")

    print("\nInstalling Caddyfile...")
    cf = render_caddyfile(domain, email)
    Path("/etc/caddy").mkdir(parents=True, exist_ok=True)
    Path("/etc/caddy/Caddyfile").write_text(cf)
    subprocess.run(["systemctl", "reload", "caddy"], check=True)

    print("Installing systemd units...")
    subprocess.run(["cp", "-f",
                    *list((REPO_ROOT / "systemd").glob("*.service")),
                    *list((REPO_ROOT / "systemd").glob("*.timer")),
                    "/etc/systemd/system/"], check=True)
    subprocess.run(["systemctl", "daemon-reload"], check=True)

    print("Enabling services and timers...")
    enable_services([
        "hermes-claude-channel.service",
        "hermes-claude-api.service",
        "hermes-claude-frontend.service",
        "hermes-healthcheck.timer",
        "hermes-cache-refresh.timer",
        "hermes-usage-snapshot.timer",
        "hermes-idle-reaper.timer",
    ])

    print("\n✓ Setup complete.")
    print(f"  Public: https://{domain}/")
    print(f"  Admin:  https://{domain}/admin")
    return 0


def main() -> None:
    if os.geteuid() != 0:
        print("hermes-init must run as root (use sudo)", file=sys.stderr)
        sys.exit(1)
    sys.exit(run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify pass**

```bash
pytest tests/wizard/test_init.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Verify CLI entrypoint installs**

```bash
pip install -e ".[dev]"
which hermes-init   # expect path inside .venv/bin
```

- [ ] **Step 6: Commit**

```bash
git add src/hermes_claude/wizard/init.py tests/wizard/
git commit -m "Add hermes-init setup wizard (interactive, idempotent)"
```

---

### Task 48: Write the marketplace.json + V1 README pitch

**Files:**
- Modify: `README.md`
- Create: `.claude-plugin/marketplace.json`
- Create: `LICENSE`

- [ ] **Step 1: marketplace.json**

```json
{
  "name": "hermes-claude",
  "version": "0.1.0",
  "description": "Hermes-Agent's value in 10% the code — by riding Claude Code's native rails.",
  "author": "Mayank Gupta",
  "repository": "https://github.com/techfreakworm/hermes-claude",
  "homepage": "https://claude.mayankgupta.in",
  "license": "MIT",
  "plugins": [
    { "name": "hermes-claude", "path": "." }
  ]
}
```

- [ ] **Step 2: LICENSE (MIT)**

```text
MIT License

Copyright (c) 2026 Mayank Gupta

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

- [ ] **Step 3: README.md (V1 version)**

````markdown
# Hermes-Claude

> Hermes-Agent's value in 10% the code, by riding Claude Code's native rails.

A Claude Code plugin that gives Claude a body. Telegram for messaging, voice
in and voice out, persistent project-leads with their own agent teams, all
reachable from your phone — and a dashboard at `claude.mayankgupta.in` that
shows it running live.

It's a self-conscious response to [Hermes-Agent](https://github.com/NousResearch/hermes-agent)
by Nous Research — a 27,000-line platform for self-improving messaging agents.
After studying it, I asked: *what if your engine is Claude Code? How much do
you actually need to build?* The answer turned out to be ~4,000 lines.
Channels, cron, agent teams, memory, MCP, hooks, mobile push, remote control
— all native to Claude Code if you know where to look. The plugin fills the
last 5%: a voice pipeline, a project orchestrator, a dashboard, and curated
workflows.

No API keys. Runs on a single Oracle Cloud free-tier VPS. Authed via Claude
Max subscription.

## Quick install (Ubuntu ARM, fresh)

```bash
# On the VPS:
sudo apt update && sudo apt install -y git
git clone https://github.com/techfreakworm/hermes-claude.git /opt/hermes-claude
cd /opt/hermes-claude
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
sudo hermes-init   # interactive wizard
```

## What's in V1

- Telegram channel (text + voice memos)
- Voice pipeline: whisper.cpp STT + piper TTS, English
- Codex CLI delegation for image generation
- Project orchestrator: spawn independent `claude --bg` sessions per project,
  each with their own agent team
- Five project templates: web-scraper, llm-app, server-app, agentic-coding, custom
- Dashboard at `claude.mayankgupta.in` — live activity feed, project tree,
  routines, usage analytics
- Three scheduled routines: morning brief, weekly memory consolidation,
  monthly OAuth expiry check
- Idle reaper hibernates drifting projects at 24h, hard-deletes at 7d
- One-script setup wizard

## What's not in V1 (V2 / future)

- Trading MCPs (Zerodha, Dhan)
- Other messaging platforms (Discord, iMessage, WhatsApp, Signal, Email, Slack)
- Memory edit-in-place from dashboard
- Logs filter UI
- Transcript full-text search
- Voice cloning, multi-voice, emotion control

## Architecture

See [docs/superpowers/specs/2026-05-22-hermes-claude-design.md](docs/superpowers/specs/2026-05-22-hermes-claude-design.md)
for the design and [docs/superpowers/plans/2026-05-22-hermes-claude-v1.md](docs/superpowers/plans/2026-05-22-hermes-claude-v1.md)
for the implementation plan.

## License

MIT. See [LICENSE](LICENSE).
````

- [ ] **Step 4: Commit**

```bash
git add README.md LICENSE .claude-plugin/marketplace.json
git commit -m "README + LICENSE + marketplace.json for V1 ship"
```

---

### Task 49: End-to-end smoke test

**Files:** none — verification only

- [ ] **Step 1: Restart everything cleanly**

```bash
ssh oci-hermes bash <<'EOF'
sudo systemctl restart hermes-claude-channel.service \
                        hermes-claude-api.service \
                        hermes-claude-frontend.service
sudo systemctl reload caddy
sleep 15
sudo systemctl --no-pager is-active hermes-claude-channel.service \
    hermes-claude-api.service hermes-claude-frontend.service
EOF
```
Expected: all three return `active`.

- [ ] **Step 2: Verify dashboard loads**

Open `https://claude.mayankgupta.in/` in a browser.
- Public landing renders with live stats (probably zeros immediately after restart, which is fine)
- Console shows no errors

Open `https://claude.mayankgupta.in/admin`.
- GitHub OAuth flow completes
- Overview page renders with KPI cards and (initially empty) activity feed

- [ ] **Step 3: Voice round-trip from Telegram**

Send a voice memo: "What am I working on right now?"
Expected within ~20s: bot replies via Telegram voice with a portfolio summary.

- [ ] **Step 4: Spawn a project from Telegram**

Send: "Build me a tiny demo app called demo-smoke that just prints hello."
Expected: bot replies with Remote Control URL for `demo-smoke`. Open the URL on your phone — the lead session is alive in your Claude mobile app.

- [ ] **Step 5: Verify dashboard reflects state**

Refresh `https://claude.mayankgupta.in/admin/projects`.
- `demo-smoke` appears in the react-flow tree
- Click "attach" link → opens Remote Control URL

Open `/admin` activity feed — see SSE events trickling in.

- [ ] **Step 6: Kill the demo project**

In Telegram: "Shut down demo-smoke."
Expected: bot confirms hibernation; `/admin/projects` empties on refresh.

- [ ] **Step 7: Verify timers fired at least once each**

```bash
ssh oci-hermes 'sudo systemctl list-timers --no-pager | grep hermes'
```
Each timer should show a past `LAST` time within the last interval.

- [ ] **Step 8: Tag V1 complete**

```bash
git tag v0.1.0
echo "V1 SHIPPED."
```

(Week 4 done. V1 complete.)

---

## Self-Review (engineer-facing checklist)

Before claiming V1 done, run through these:

- [ ] Spec coverage — every section in `2026-05-22-hermes-claude-design.md` maps to one or more tasks above. Walk through and verify.
- [ ] Placeholder scan — search the plan for "TODO", "TBD", "fill in" — none should be in normative tasks (Open Questions / V2 deferrals are fine).
- [ ] Type/name consistency — `transcribe_impl`, `synthesize_impl`, `spawn_project_impl`, `list_projects_impl`, `kill_project_impl`, `send_to_project_impl`, `get_status_impl` are referenced consistently across server.py, tests, and route files.
- [ ] V1 OUT items — verify NO task accidentally implements trading MCPs, voice cloning, custom non-Telegram channels, or transcript FTS. Each of those should remain firmly in V2.
- [ ] Permission rules — channel session stays in `default`; project-leads use `acceptEdits`; orchestrator never elevates without user request via Telegram.
- [ ] Secrets discipline — `secrets.env` is the only place tokens live; gitignored; chmod 600.

If any check fails, fix inline before considering V1 shipped.

---

## Notes for V1.5

When V1 is stable in daily use (~2 weeks of personal use without major regression):

- Author a 60-second Loom demo (voice memo → spawn project → dashboard shows live)
- Render a high-res architecture PNG
- Write a blog post on mayankgupta.in: "Why I built Hermes-Claude instead of forking Hermes-Agent"
- Push to GitHub public + `/plugin marketplace add techfreakworm/hermes-claude`
- Pin a tweet thread

Only then does Hermes-Claude transition from "personal tool" to "portfolio showcase."
