"""Tests for scripts/soma-relay (bash helper).

Each test invokes the bash script via subprocess with monkeypatched env vars
and a tmpdir as the relay root, so no live filesystem paths are touched.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "soma-relay"


def _run(args: list[str], env: dict | None = None, **kwargs) -> subprocess.CompletedProcess:
    base_env = {**os.environ}
    if env:
        base_env.update(env)
    return subprocess.run(
        ["bash", str(SCRIPT)] + args,
        capture_output=True,
        text=True,
        env=base_env,
        **kwargs,
    )


def test_publish_creates_file_in_lead_namespace(tmp_path: Path) -> None:
    src = tmp_path / "artifact.txt"
    src.write_text("hello relay")
    relay_root = tmp_path / "relay"

    result = _run(
        ["publish", str(src)],
        env={
            "HERMES_LEAD_NAME": "test-lead",
            "SOMA_RELAY_DOMAIN": "files.example.test",
            "SOMA_RELAY_ROOT": str(relay_root),
        },
    )
    assert result.returncode == 0, result.stderr
    url = result.stdout.strip()
    assert url == "https://files.example.test/test-lead/artifact.txt"

    dest = relay_root / "test-lead" / "artifact.txt"
    assert dest.exists()
    assert dest.read_text() == "hello relay"


def test_publish_public_creates_file_in_pub_namespace(tmp_path: Path) -> None:
    src = tmp_path / "hero.png"
    src.write_bytes(b"\x89PNG\r\n")
    relay_root = tmp_path / "relay"

    result = _run(
        ["publish", "--public", str(src)],
        env={
            "HERMES_LEAD_NAME": "test-lead",
            "SOMA_RELAY_DOMAIN": "files.example.test",
            "SOMA_RELAY_ROOT": str(relay_root),
        },
    )
    assert result.returncode == 0, result.stderr
    url = result.stdout.strip()
    assert url.startswith("https://files.example.test/pub/")
    assert url.endswith("/hero.png")

    # Extract slug from URL: https://domain/pub/<slug>/file
    parts = url.split("/")
    slug = parts[-2]
    assert len(slug) == 12, f"Expected 12-hex slug, got {slug!r}"
    assert all(c in "0123456789abcdef" for c in slug), f"Non-hex slug: {slug!r}"

    dest = relay_root / "pub" / slug / "hero.png"
    assert dest.exists()


def test_rm_strips_url_and_deletes_file(tmp_path: Path) -> None:
    relay_root = tmp_path / "relay"
    lead_dir = relay_root / "my-lead"
    lead_dir.mkdir(parents=True)
    target = lead_dir / "report.pdf"
    target.write_bytes(b"%PDF")

    url = "https://files.example.test/my-lead/report.pdf"
    result = _run(
        ["rm", url],
        env={
            "SOMA_RELAY_DOMAIN": "files.example.test",
            "SOMA_RELAY_ROOT": str(relay_root),
        },
    )
    assert result.returncode == 0, result.stderr
    assert not target.exists()
    assert "Deleted" in result.stdout


def test_rm_refuses_path_outside_root(tmp_path: Path) -> None:
    relay_root = tmp_path / "relay"
    relay_root.mkdir(parents=True)
    evil = tmp_path / "evil.txt"
    evil.write_text("should not be deleted")

    result = _run(
        ["rm", str(evil)],
        env={
            "SOMA_RELAY_DOMAIN": "files.example.test",
            "SOMA_RELAY_ROOT": str(relay_root),
        },
    )
    assert result.returncode != 0
    assert "refusing to delete outside relay root" in result.stderr
    assert evil.exists()


def test_list_walks_both_namespaces(tmp_path: Path) -> None:
    relay_root = tmp_path / "relay"

    # Set up lead dir
    lead_dir = relay_root / "camp-lead"
    lead_dir.mkdir(parents=True)
    (lead_dir / "deck.pptx").write_bytes(b"PK")

    # Set up pub dir
    pub_dir = relay_root / "pub" / "abc123def456"
    pub_dir.mkdir(parents=True)
    (pub_dir / "image.png").write_bytes(b"\x89PNG")

    result = _run(
        ["list"],
        env={
            "SOMA_RELAY_DOMAIN": "files.example.test",
            "SOMA_RELAY_ROOT": str(relay_root),
        },
    )
    assert result.returncode == 0, result.stderr
    assert "camp-lead" in result.stdout
    assert "https://files.example.test/camp-lead/deck.pptx" in result.stdout
    assert "https://files.example.test/pub/abc123def456/image.png" in result.stdout


def test_publish_missing_file_exits_nonzero(tmp_path: Path) -> None:
    relay_root = tmp_path / "relay"
    result = _run(
        ["publish", "/nonexistent/path/file.txt"],
        env={
            "HERMES_LEAD_NAME": "test-lead",
            "SOMA_RELAY_DOMAIN": "files.example.test",
            "SOMA_RELAY_ROOT": str(relay_root),
        },
    )
    assert result.returncode != 0
    assert "not found" in result.stderr


def test_publish_defaults_lead_to_general_when_env_unset(tmp_path: Path) -> None:
    src = tmp_path / "file.txt"
    src.write_text("data")
    relay_root = tmp_path / "relay"

    env = {
        "SOMA_RELAY_DOMAIN": "files.example.test",
        "SOMA_RELAY_ROOT": str(relay_root),
    }
    # Remove HERMES_LEAD_NAME if inherited from environment
    env["HERMES_LEAD_NAME"] = ""
    result = _run(["publish", str(src)], env=env)
    # The script sets LEAD to empty string when HERMES_LEAD_NAME is empty
    # but falls back to 'general' only if unset. To test the default, we
    # need to truly unset it — do that by using a filtered env.
    filtered_env = {k: v for k, v in os.environ.items() if k != "HERMES_LEAD_NAME"}
    filtered_env.update({
        "SOMA_RELAY_DOMAIN": "files.example.test",
        "SOMA_RELAY_ROOT": str(relay_root),
    })
    result2 = subprocess.run(
        ["bash", str(SCRIPT), "publish", str(src)],
        capture_output=True,
        text=True,
        env=filtered_env,
    )
    assert result2.returncode == 0, result2.stderr
    url = result2.stdout.strip()
    assert "/general/" in url
