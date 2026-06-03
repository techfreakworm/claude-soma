from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def test_install_md_exists():
    assert (REPO_ROOT / "INSTALL.md").exists()


def test_install_md_references_bootstrap():
    content = (REPO_ROOT / "INSTALL.md").read_text()
    assert "scripts/bootstrap.sh" in content


def test_install_md_references_secrets_example():
    content = (REPO_ROOT / "INSTALL.md").read_text()
    assert "secrets.env.example" in content


def test_install_md_references_smoke():
    content = (REPO_ROOT / "INSTALL.md").read_text()
    assert "scripts/smoke_install.sh" in content


def test_readme_quickstart_points_to_bootstrap():
    content = (REPO_ROOT / "README.md").read_text()
    assert "scripts/bootstrap.sh" in content


def test_readme_no_deploy_sh_in_quickstart():
    content = (REPO_ROOT / "README.md").read_text()
    quickstart_start = content.find("## Quickstart")
    assert quickstart_start != -1, "Quickstart section not found in README.md"
    next_heading = content.find("\n## ", quickstart_start + 1)
    if next_heading == -1:
        quickstart_section = content[quickstart_start:]
    else:
        quickstart_section = content[quickstart_start:next_heading]
    assert "scripts/deploy.sh" not in quickstart_section


def test_next_md_no_hermes_unit_names():
    content = (REPO_ROOT / "NEXT.md").read_text()
    matches = re.findall(r"hermes-(health|cache|secrets|pw|usage)", content)
    assert len(matches) == 0, f"Found {len(matches)} hermes-* unit name(s): {matches}"


def test_next_md_no_claude_mayankgupta_domain():
    content = (REPO_ROOT / "NEXT.md").read_text()
    assert "claude.mayankgupta.in" not in content, (
        "NEXT.md still references claude.mayankgupta.in (should be soma.mayankgupta.in)"
    )


def test_claude_md_week3_complete():
    content = (REPO_ROOT / "CLAUDE.md").read_text()
    assert "Week 3 (dashboard)**: not started" not in content, (
        "CLAUDE.md still says Week 3 not started"
    )
    assert "Week 3 (dashboard)**: complete" in content, (
        "CLAUDE.md does not say Week 3 complete"
    )
