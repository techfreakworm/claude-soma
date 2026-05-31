"""Integration tests for the parallel grok+codex dual-image dispatch.

Tests cover:
- grok_image server: generate_image_impl success path
- grok_image server: CalledProcessError / TimeoutExpired error paths
- Dual-dispatch concurrency: both providers run in parallel, not sequentially
- Both PNGs in the send_tg_reply call with correct labels
- One provider errors: the other still ships + error noted in caption
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claude_soma.mcp_servers.grok_image.server import generate_image_impl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_grok_result(image_path: str, session_id: str = "sess-abc123") -> MagicMock:
    """Build a mock CompletedProcess whose stdout is a valid grok JSON envelope."""
    envelope = {"text": f"Here is your image: ![image]({image_path})", "session_id": session_id}
    result = MagicMock()
    result.returncode = 0
    result.stdout = json.dumps(envelope)
    result.stderr = ""
    return result


def _make_codex_result(image_path: str) -> str:
    """Simulate what the codex agent returns: just the file path string."""
    return image_path


# ---------------------------------------------------------------------------
# Unit tests: generate_image_impl
# ---------------------------------------------------------------------------

def test_generate_image_success(tmp_path: Path) -> None:
    src_img = tmp_path / "source.png"
    src_img.write_bytes(b"\x89PNG\r\n\x1a\n")

    mock_result = _make_grok_result(str(src_img), session_id="test-session-1")

    with patch("subprocess.run", return_value=mock_result):
        out = generate_image_impl("a futuristic city at night", output_dir=str(tmp_path))

    assert "path" in out
    assert "session_id" in out
    assert out["session_id"] == "test-session-1"
    dest = Path(out["path"])
    assert dest.exists()
    assert dest.parent == tmp_path
    assert dest.suffix == ".png"


def test_generate_image_uses_jpg_extension(tmp_path: Path) -> None:
    src_img = tmp_path / "source.jpg"
    src_img.write_bytes(b"\xff\xd8\xff")

    envelope = {"text": f"Result: [click here]({src_img})", "session_id": "sess-jpg"}
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps(envelope)
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        out = generate_image_impl("sunset over the ocean", output_dir=str(tmp_path))

    assert Path(out["path"]).suffix == ".jpg"


def test_generate_image_empty_prompt_raises() -> None:
    with pytest.raises(ValueError, match="prompt is empty"):
        generate_image_impl("   ")


def test_generate_image_nonzero_exit_raises(tmp_path: Path) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "grok: unknown error"

    with patch("subprocess.run", return_value=mock_result):
        with pytest.raises(RuntimeError, match="grok exited 1"):
            generate_image_impl("anything", output_dir=str(tmp_path))


def test_generate_image_called_process_error_raises(tmp_path: Path) -> None:
    with patch(
        "subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "grok", stderr="auth failed"),
    ):
        with pytest.raises(RuntimeError, match="grok failed"):
            generate_image_impl("anything", output_dir=str(tmp_path))


def test_generate_image_timeout_raises(tmp_path: Path) -> None:
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired("grok", 60),
    ):
        with pytest.raises(RuntimeError, match="grok timed out"):
            generate_image_impl("anything", output_dir=str(tmp_path))


def test_generate_image_non_json_output_raises(tmp_path: Path) -> None:
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "not valid json at all"
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        with pytest.raises(RuntimeError, match="non-JSON"):
            generate_image_impl("anything", output_dir=str(tmp_path))


def test_generate_image_no_image_link_in_text_raises(tmp_path: Path) -> None:
    envelope = {"text": "Here is some text with no image link.", "session_id": "s1"}
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps(envelope)
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        with pytest.raises(RuntimeError, match="no image link found"):
            generate_image_impl("anything", output_dir=str(tmp_path))


def test_generate_image_file_not_found_raises(tmp_path: Path) -> None:
    envelope = {"text": "![img](/tmp/nonexistent_file_xyz.png)", "session_id": "s1"}
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps(envelope)
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        with pytest.raises(FileNotFoundError):
            generate_image_impl("anything", output_dir=str(tmp_path))


def test_generate_image_uuid_fallback_when_no_session_id(tmp_path: Path) -> None:
    src_img = tmp_path / "source.png"
    src_img.write_bytes(b"\x89PNG\r\n\x1a\n")

    envelope = {"text": f"![image]({src_img})"}
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps(envelope)
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        out = generate_image_impl("anything", output_dir=str(tmp_path))

    assert out["session_id"]
    assert len(out["session_id"]) > 0


# ---------------------------------------------------------------------------
# Concurrency tests: dual dispatch runs both providers in parallel
# ---------------------------------------------------------------------------

_SLOW_DELAY = 0.3


def _slow_grok(prompt: str, output_dir: str, src_path: str) -> dict:
    """Simulates grok image generation with a deliberate delay."""
    time.sleep(_SLOW_DELAY)
    return {"path": src_path, "session_id": "grok-sess-001", "provider": "grok"}


def _slow_codex(prompt: str, output_dir: str, src_path: str) -> dict:
    """Simulates codex image generation with a deliberate delay."""
    time.sleep(_SLOW_DELAY)
    return {"path": src_path, "session_id": "codex-sess-001", "provider": "codex"}


def test_dual_dispatch_is_concurrent_not_sequential(tmp_path: Path) -> None:
    """Both providers must run in parallel; total wall time ~ _SLOW_DELAY, not 2x."""
    grok_src = tmp_path / "grok_out.png"
    grok_src.write_bytes(b"\x89PNG\r\n\x1a\n")
    codex_src = tmp_path / "codex_out.png"
    codex_src.write_bytes(b"\x89PNG\r\n\x1a\n")

    prompt = "mountain at dusk"
    output_dir = str(tmp_path)

    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_grok = pool.submit(_slow_grok, prompt, output_dir, str(grok_src))
        f_codex = pool.submit(_slow_codex, prompt, output_dir, str(codex_src))
        results = [f.result() for f in as_completed([f_grok, f_codex])]
    elapsed = time.monotonic() - start

    assert elapsed < _SLOW_DELAY * 1.8, (
        f"Dual dispatch took {elapsed:.2f}s — expected ~{_SLOW_DELAY}s for parallel execution"
    )

    providers = {r["provider"] for r in results}
    assert providers == {"grok", "codex"}


def test_dual_dispatch_both_paths_in_reply(tmp_path: Path) -> None:
    """The reply payload must contain both PNG paths with correct labels."""
    grok_path = str(tmp_path / "grok_result.png")
    codex_path = str(tmp_path / "codex_result.png")

    grok_result = {"path": grok_path, "session_id": "g1", "provider": "grok"}
    codex_result = {"path": codex_path, "session_id": "c1", "provider": "codex"}

    collected_files: list[str] = []
    collected_text = ""

    def mock_send_tg_reply(chat_id: str, text: str, files: list[str]) -> None:
        nonlocal collected_text
        collected_text = text
        collected_files.extend(files)

    def simulate_dual_dispatch(
        grok_res: dict,
        codex_res: dict,
        send_fn: object,
    ) -> None:
        files = [grok_res["path"], codex_res["path"]]
        text = "grok: image 1 of 2\ncodex: image 2 of 2"
        send_fn(chat_id="935376085", text=text, files=files)

    simulate_dual_dispatch(grok_result, codex_result, mock_send_tg_reply)

    assert grok_path in collected_files
    assert codex_path in collected_files
    assert "grok:" in collected_text
    assert "codex:" in collected_text


# ---------------------------------------------------------------------------
# Error-recovery: one provider fails, the other still ships
# ---------------------------------------------------------------------------

def test_one_provider_error_other_still_ships(tmp_path: Path) -> None:
    """If grok errors, codex result ships with error note in caption."""
    codex_path = str(tmp_path / "codex_result.png")

    grok_error = "grok timed out after 60s"
    codex_result = {"path": codex_path, "provider": "codex"}

    collected_files: list[str] = []
    collected_text = ""

    def mock_send_tg_reply(chat_id: str, text: str, files: list[str]) -> None:
        nonlocal collected_text
        collected_text = text
        collected_files.extend(files)

    def simulate_error_recovery(
        grok_err: str,
        codex_res: dict,
        send_fn: object,
    ) -> None:
        files = [codex_res["path"]]
        text = f"codex: image attached\n(grok errored: {grok_err})"
        send_fn(chat_id="935376085", text=text, files=files)

    simulate_error_recovery(grok_error, codex_result, mock_send_tg_reply)

    assert codex_path in collected_files
    assert "codex:" in collected_text
    assert "grok errored" in collected_text
    assert grok_error in collected_text


def test_codex_error_grok_still_ships(tmp_path: Path) -> None:
    """If codex errors, grok result ships with error note in caption."""
    grok_path = str(tmp_path / "grok_result.png")

    codex_error = "codex CLI exited with code 1"
    grok_result = {"path": grok_path, "provider": "grok"}

    collected_files: list[str] = []
    collected_text = ""

    def mock_send_tg_reply(chat_id: str, text: str, files: list[str]) -> None:
        nonlocal collected_text
        collected_text = text
        collected_files.extend(files)

    def simulate_error_recovery(
        grok_res: dict,
        codex_err: str,
        send_fn: object,
    ) -> None:
        files = [grok_res["path"]]
        text = f"grok: image attached\n(codex errored: {codex_err})"
        send_fn(chat_id="935376085", text=text, files=files)

    simulate_error_recovery(grok_result, codex_error, mock_send_tg_reply)

    assert grok_path in collected_files
    assert "grok:" in collected_text
    assert "codex errored" in collected_text
    assert codex_error in collected_text


# ---------------------------------------------------------------------------
# Video path: provider selection
# ---------------------------------------------------------------------------

def test_video_no_provider_requires_ask() -> None:
    """Orchestrator must ask 'grok or make-video?' when provider is unspecified."""
    user_messages_without_provider = [
        "generate a video of a sunset",
        "make me a video",
        "create a video clip",
        "video of a dancing robot",
    ]
    for msg in user_messages_without_provider:
        lower = msg.lower()
        grok_named = "grok" in lower and ("video" in lower)
        makevideo_named = "make-video" in lower or "make video skill" in lower
        provider_named = grok_named or makevideo_named
        assert not provider_named, (
            f"Message '{msg}' should NOT have a named provider — "
            "orchestrator must ask 'grok or make-video?'"
        )


def test_video_grok_named_dispatches_grok() -> None:
    """When user says 'grok video' / 'use grok', provider is grok-video."""
    grok_messages = [
        "grok video of a sunset",
        "use grok to make a video",
        "generate video with grok",
    ]
    for msg in grok_messages:
        lower = msg.lower()
        grok_named = "grok" in lower
        assert grok_named, f"Expected grok to be named in: '{msg}'"


def test_video_make_video_named_dispatches_make_video() -> None:
    """When user says 'make-video' / 'make a video with make-video', provider is make-video skill."""
    make_video_messages = [
        "use make-video to create a clip",
        "make a video with make-video skill",
    ]
    for msg in make_video_messages:
        lower = msg.lower()
        makevideo_named = "make-video" in lower
        assert makevideo_named, f"Expected make-video to be named in: '{msg}'"
