from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP


_GROK_BIN_FALLBACK = "/usr/local/bin/grok"

_IMAGE_LINK_RE = re.compile(r"\(([^)]+\.(?:jpg|png))\)")


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
    match = _IMAGE_LINK_RE.search(text_field)
    if match is None:
        raise RuntimeError(
            f"no image link found in grok text field: {text_field[:200]}"
        )

    src = Path(match.group(1))
    if not src.exists():
        raise FileNotFoundError(f"grok image file not found: {src}")

    session_id = envelope.get("session_id") or str(uuid.uuid4())
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
