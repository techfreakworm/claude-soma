from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
MANIFEST_PATH = REPO_ROOT / ".claude-plugin" / "marketplace.json"


def _load() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


def test_marketplace_json_file_exists() -> None:
    assert MANIFEST_PATH.exists(), f"marketplace.json not found at {MANIFEST_PATH}"


def test_marketplace_json_is_valid_json() -> None:
    text = MANIFEST_PATH.read_text()
    parsed = json.loads(text)
    assert isinstance(parsed, dict), "marketplace.json root must be a JSON object"


def test_marketplace_json_has_required_fields() -> None:
    data = _load()
    required = ("name", "version", "description", "author", "repository", "license")
    missing = [f for f in required if f not in data]
    assert not missing, f"marketplace.json missing required fields: {missing}"


def test_marketplace_author_is_object_not_string() -> None:
    data = _load()
    author = data.get("author")
    assert isinstance(author, dict), (
        f"author must be an object (dict), got {type(author).__name__!r}. "
        "A bare string fails Claude plugin Zod validation."
    )


def test_marketplace_author_has_name_and_email() -> None:
    data = _load()
    author = data["author"]
    assert "name" in author, "author object missing 'name'"
    assert "email" in author, "author object missing 'email'"
    assert author["name"], "author.name must be non-empty"
    assert author["email"], "author.email must be non-empty"


def test_marketplace_version_is_semver_like() -> None:
    data = _load()
    version = data.get("version", "")
    assert re.match(r"^\d+\.\d+\.\d+", version), (
        f"version {version!r} does not start with semver (MAJOR.MINOR.PATCH)"
    )


def test_marketplace_name_is_nonempty_string() -> None:
    data = _load()
    name = data.get("name")
    assert isinstance(name, str) and name, "name must be a non-empty string"
