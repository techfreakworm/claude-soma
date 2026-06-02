"""Regression tests for grok_image parser robustness (PAN-W1A-GROK-PARSER).

Covers B1-B8: multi-shape image extraction, session-id key fix, URL download
path, refusal-text error message, and empty-stdout disambiguation.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_soma.mcp_servers.grok_image.server import generate_image_impl


def _fake_proc(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_grok_parser_markdown_image_link(tmp_path: Path) -> None:
    src = tmp_path / "x.jpg"
    src.write_bytes(b"\xff\xd8\xff")
    env = {"text": f"![alt]({src})", "sessionId": "sid1"}
    with patch("subprocess.run", return_value=_fake_proc(json.dumps(env))):
        out = generate_image_impl("test prompt", output_dir=str(tmp_path))
    assert out["path"].endswith(".jpg")
    assert out["session_id"] == "sid1"
    assert Path(out["path"]).parent == tmp_path


def test_grok_parser_plain_png_url(tmp_path: Path) -> None:
    env = {"text": "Result: https://example.com/a.png", "sessionId": "sid2"}

    def fake_urlretrieve(url: str, dest: str) -> None:
        Path(dest).write_bytes(b"\x89PNG\r\n\x1a\n")

    with patch("subprocess.run", return_value=_fake_proc(json.dumps(env))), \
         patch("urllib.request.urlretrieve", side_effect=fake_urlretrieve):
        out = generate_image_impl("test prompt", output_dir=str(tmp_path))
    assert Path(out["path"]).parent == tmp_path
    assert out["session_id"] == "sid2"


def test_grok_parser_local_path_no_url(tmp_path: Path) -> None:
    src = tmp_path / "d.webp"
    src.write_bytes(b"RIFF\x00\x00\x00\x00WEBP")
    env = {"text": f"Saved to {src} ok", "sessionId": "sid3"}
    with patch("subprocess.run", return_value=_fake_proc(json.dumps(env))):
        out = generate_image_impl("test prompt", output_dir=str(tmp_path))
    assert out["path"].endswith(".webp")
    assert out["session_id"] == "sid3"
    assert Path(out["path"]).parent == tmp_path


def test_grok_parser_multi_paragraph_text_extracts_url(tmp_path: Path) -> None:
    src = tmp_path / "multi.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n")
    text = (
        "Here is the image.\n\n"
        "Generated from prompt: foo\n\n"
        f"![image]({src})\n\n"
        "Thanks!"
    )
    env = {"text": text, "sessionId": "sid4"}
    with patch("subprocess.run", return_value=_fake_proc(json.dumps(env))):
        out = generate_image_impl("test prompt", output_dir=str(tmp_path))
    assert out["path"].endswith(".png")
    assert out["session_id"] == "sid4"


def test_grok_parser_refusal_text_raises_with_helpful_message(tmp_path: Path) -> None:
    env = {"text": "I cannot generate that image because it violates policy.", "sessionId": "sid5"}
    with patch("subprocess.run", return_value=_fake_proc(json.dumps(env))):
        with pytest.raises(RuntimeError) as exc_info:
            generate_image_impl("test prompt", output_dir=str(tmp_path))
    msg = str(exc_info.value)
    assert msg.startswith("grok returned no image reference: ")
    assert "I cannot generate" in msg


def test_grok_parser_empty_stdout_raises_distinct_error(tmp_path: Path) -> None:
    with patch("subprocess.run", return_value=_fake_proc("")):
        with pytest.raises(RuntimeError) as exc_info:
            generate_image_impl("test prompt", output_dir=str(tmp_path))
    msg = str(exc_info.value)
    assert "non-JSON" in msg
    assert "grok returned no image reference" not in msg


def test_grok_parser_camelcase_session_id(tmp_path: Path) -> None:
    src = tmp_path / "c7.jpg"
    src.write_bytes(b"\xff\xd8\xff")
    env = {"text": f"![img]({src})", "sessionId": "sid-camel"}
    with patch("subprocess.run", return_value=_fake_proc(json.dumps(env))):
        out = generate_image_impl("test prompt", output_dir=str(tmp_path))
    assert out["session_id"] == "sid-camel"


def test_grok_parser_backtick_path_live_failure_shape(tmp_path: Path) -> None:
    src = tmp_path / "c8.jpg"
    src.write_bytes(b"\xff\xd8\xff")
    text = f"**Image generated:** `{src}`\n\nA peacock sitting on a tree."
    env = {"text": text, "sessionId": "sid8"}
    with patch("subprocess.run", return_value=_fake_proc(json.dumps(env))):
        out = generate_image_impl("test prompt", output_dir=str(tmp_path))
    assert out["path"].endswith(".jpg")
    assert out["session_id"] == "sid8"
    assert Path(out["path"]).parent == tmp_path
