from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.request
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP


_GROK_BIN_FALLBACK = "/usr/local/bin/grok"

_PATTERNS = [
    # 1. Markdown image: ![alt](/abs/path.jpg)
    re.compile(r"!\[[^\]]*\]\((?P<p>[^)\s]+\.(?:jpg|jpeg|png|webp))\)"),
    # 2. Markdown link (no bang): [text](/abs/path.jpg)
    re.compile(r"(?<!\!)\[[^\]]*\]\((?P<p>[^)\s]+\.(?:jpg|jpeg|png|webp))\)"),
    # 3. Backtick-quoted absolute path: `/abs/path.jpg`
    re.compile(r"`(?P<p>/[^`\s]+\.(?:jpg|jpeg|png|webp))`"),
    # 4. Plain http(s) URL — must precede bare-path so the // in :// does not confuse pattern 5
    re.compile(r"(?P<p>https?://[^\s)`\]<>]+\.(?:jpg|jpeg|png|webp))"),
    # 5. Bare absolute path with image extension (negative lookarounds exclude backtick/bracket/paren context)
    re.compile(r"(?<![`\(\[])(?P<p>/[^\s`)\]<>]+\.(?:jpg|jpeg|png|webp))(?![`\)\]])"),
]


def _extract_image_target(text: str) -> str | None:
    for pat in _PATTERNS:
        m = pat.search(text)
        if m:
            return m.group("p")
    return None


def _resolve_grok_bin() -> str:
    """Resolve the grok binary path. Order: GROK_BIN env > shutil.which("grok") > fallback.

    Resolved lazily per call so env changes (and PATH updates after MCP server start)
    are honored without restart. The fallback preserves the original
    `/usr/local/bin/grok` path for systems where that IS where grok lives.
    """
    env_override = os.environ.get("GROK_BIN")
    if env_override:
        return env_override
    found = shutil.which("grok")
    if found:
        return found
    return _GROK_BIN_FALLBACK


def generate_image_impl(
    prompt: str,
    output_dir: str = "/tmp",
    timeout_seconds: int = 120,
) -> dict:
    if not prompt.strip():
        raise ValueError("prompt is empty")

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    grok_bin = _resolve_grok_bin()

    try:
        result = subprocess.run(
            [grok_bin, "-p", f"/imagine {prompt}", "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "")[-500:]
        raise RuntimeError(f"grok failed: {stderr}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"grok timed out after {timeout_seconds}s") from e

    if result.returncode != 0:
        stderr = (result.stderr or "")[-500:]
        raise RuntimeError(f"grok exited {result.returncode}: {stderr}")

    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"grok returned non-JSON output: {result.stdout[:200]}") from e

    text_field = envelope.get("text", "")
    target = _extract_image_target(text_field)
    if target is None:
        raise RuntimeError("grok returned no image reference: " + text_field[:300])

    session_id = envelope.get("sessionId") or envelope.get("session_id") or str(uuid.uuid4())

    if target.startswith(("http://", "https://")):
        suffix = Path(target).suffix
        dest = out_path / f"grok_{session_id}{suffix}"
        urllib.request.urlretrieve(target, str(dest))
    else:
        src = Path(target)
        if not src.exists():
            raise FileNotFoundError(f"grok image file not found: {src}")
        dest = out_path / f"grok_{session_id}{src.suffix}"
        shutil.copy2(str(src), str(dest))

    return {"path": str(dest), "session_id": session_id}


mcp = FastMCP("grok_image")


@mcp.tool()
def generate_image(
    prompt: str,
    output_dir: str = "/tmp",
    timeout_seconds: int = 120,
) -> dict:
    """Generate an image via the grok CLI (/imagine) and return its local path.

    Args:
        prompt: The natural-language image prompt to send to grok's /imagine.
        output_dir: Directory where the resulting file is copied. Default: /tmp.
        timeout_seconds: Hard timeout for the grok CLI invocation in seconds.
            Default: 120. Honor the dual-photo dispatch convention in
            responsive_bot.md — each provider gets at most 120s.

    Returns:
        {"path": "<absolute path>", "session_id": "<id>"}
    """
    return generate_image_impl(prompt, output_dir, timeout_seconds)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
