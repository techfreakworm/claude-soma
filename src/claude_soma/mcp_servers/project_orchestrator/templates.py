# src/claude_soma/mcp_servers/project_orchestrator/templates.py
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
