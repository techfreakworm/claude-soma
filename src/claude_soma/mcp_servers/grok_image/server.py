from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from mcp.server.fastmcp import FastMCP


GROK_BIN = "/usr/local/bin/grok"

_IMAGE_LINK_RE = re.compile(r"\(([^)]+\.(?:jpg|png))\)")


def generate_image_impl(prompt: str, output_dir: str = "/tmp") -> dict:
    if not prompt.strip():
        raise ValueError("prompt is empty")

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            [GROK_BIN, "-p", f"/imagine {prompt}", "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "")[-500:]
        raise RuntimeError(f"grok failed: {stderr}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("grok timed out after 60s") from e

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
def generate_image(prompt: str, output_dir: str = "/tmp") -> dict:
    """Generate an image via the grok CLI (/imagine) and return its local path."""
    return generate_image_impl(prompt, output_dir)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
