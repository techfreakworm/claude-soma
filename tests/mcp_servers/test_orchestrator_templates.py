# tests/mcp_servers/test_orchestrator_templates.py
from __future__ import annotations

import pytest

from claude_soma.mcp_servers.project_orchestrator.templates import (
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
