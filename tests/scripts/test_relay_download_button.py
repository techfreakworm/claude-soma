"""Offline static assertions for the relay .md download button feature.

Covers:
  - config/markserv/markdown.html: button present, markserv tokens intact,
    no token collision in added markup, render simulation.
  - caddy/files.caddyfile: @mdraw handler present, correctly ordered, and
    live-structure regression guards (@protected, /oauth/*).
  - systemd/claude-soma-markserv.service: ExecStartPre=+ line present.
  - scripts/markserv-apply-template.sh: bash -n clean, exit-0 safety.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "config" / "markserv" / "markdown.html"
CADDYFILE = REPO / "caddy" / "files.caddyfile"
SERVICE = REPO / "systemd" / "claude-soma-markserv.service"
APPLY_SCRIPT = REPO / "scripts" / "markserv-apply-template.sh"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _template_content() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _caddy_content() -> str:
    return CADDYFILE.read_text(encoding="utf-8")


def _service_content() -> str:
    return SERVICE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# HTML template: button present
# ---------------------------------------------------------------------------

def test_template_has_download_button() -> None:
    content = _template_content()
    assert 'id="soma-dl-btn"' in content, "Download button anchor must have id='soma-dl-btn'"


def test_template_button_is_anchor_tag() -> None:
    content = _template_content()
    assert "<a id=\"soma-dl-btn\"" in content, "Button must be an <a> tag with id='soma-dl-btn'"


def test_template_button_outside_article() -> None:
    """Button anchor must appear before <article class="markdown-body">."""
    content = _template_content()
    btn_idx = content.index('id="soma-dl-btn"')
    article_idx = content.index('<article class="markdown-body">')
    assert btn_idx < article_idx, (
        "soma-dl-btn must appear before <article class='markdown-body'> so it is "
        "outside the content flow"
    )


def test_template_has_print_media_query() -> None:
    content = _template_content()
    assert "@media print" in content, "Template must include @media print { display:none } for button"
    assert "display: none" in content or "display:none" in content


# ---------------------------------------------------------------------------
# HTML template: markserv tokens intact
# ---------------------------------------------------------------------------

def test_template_has_title_token() -> None:
    assert "{{title}}" in _template_content()


def test_template_has_content_token() -> None:
    assert "{{{content}}}" in _template_content()


def test_template_has_pid_token() -> None:
    assert "{{pid}}" in _template_content()


def test_template_has_markserv_asset_token() -> None:
    content = _template_content()
    assert "{markserv}" in content, "Template must retain {markserv} asset token for CSS links"


def test_template_content_token_count() -> None:
    content = _template_content()
    count = content.count("{{{content}}}")
    assert count == 1, f"Exactly one {{{{content}}}} expected, found {count}"


# ---------------------------------------------------------------------------
# HTML template: no token collision in added markup
# ---------------------------------------------------------------------------

def test_template_no_spurious_double_braces() -> None:
    """After removing the known markserv tokens, no {{ or }} should remain.

    Known tokens that legitimately contain {{ or }}:
      {{title}}, {{pid}}, {{{content}}} (contains {{ and }} as substrings)
    We strip them all, then assert no {{ or }} remain in the rest.
    """
    content = _template_content()
    # Remove the legitimate token occurrences
    cleaned = content
    cleaned = cleaned.replace("{{{content}}}", "")
    cleaned = cleaned.replace("{{title}}", "")
    cleaned = cleaned.replace("{{pid}}", "")
    assert "{{" not in cleaned, (
        "Double-brace {{ found outside expected markserv tokens — would corrupt rendering"
    )
    assert "}}" not in cleaned, (
        "Double-brace }} found outside expected markserv tokens — would corrupt rendering"
    )


def test_template_no_spurious_markserv_token() -> None:
    """{markserv} must only appear on CSS/asset href lines, never inside JS/HTML added markup."""
    content = _template_content()
    # Find all lines containing {markserv}
    markserv_lines = [ln.strip() for ln in content.splitlines() if "{markserv}" in ln]
    for line in markserv_lines:
        # Each such line must be a <link rel="stylesheet" href="..."> or similar asset line
        assert "href=" in line or "src=" in line, (
            f"{{markserv}} found on a non-asset line: {line!r} — "
            "would be substituted unexpectedly in added markup"
        )


def test_template_no_adjacent_closing_braces_outside_tokens() -> None:
    """Verify }} does not appear in CSS/JS blocks (would be a token collision)."""
    content = _template_content()
    # Remove legitimate tokens, then check CSS and script blocks specifically
    cleaned = content.replace("{{{content}}}", "").replace("{{title}}", "").replace("{{pid}}", "")
    # Find <style> block
    style_match = re.search(r"<style>(.*?)</style>", cleaned, re.DOTALL)
    if style_match:
        style_content = style_match.group(1)
        assert "}}" not in style_content, "}} found inside <style> block — CSS must use single closing braces"
    # Find <script> blocks
    for script_match in re.finditer(r"<script[^>]*>(.*?)</script>", cleaned, re.DOTALL):
        script_content = script_match.group(1)
        assert "}}" not in script_content, "}} found inside <script> block — JS must use single closing braces"


# ---------------------------------------------------------------------------
# HTML template: render simulation (markserv substitution)
# ---------------------------------------------------------------------------

def test_template_render_simulation() -> None:
    """Simulate markserv's literal string substitution and verify clean output."""
    content = _template_content()

    # Perform the same four substitutions markserv does
    rendered = content
    rendered = rendered.replace("{{{content}}}", "<p>hello world</p>")
    rendered = rendered.replace("{{title}}", "Test Document")
    rendered = rendered.replace("{{pid}}", "12345")
    rendered = rendered.replace("{markserv}", "/")

    # Rendered output must contain the injected values
    assert "<p>hello world</p>" in rendered
    assert "Test Document" in rendered
    assert "12345" in rendered

    # Button must survive rendering intact
    assert 'id="soma-dl-btn"' in rendered

    # No leftover markserv tokens
    assert "{{{content}}}" not in rendered, "content token not consumed"
    assert "{{title}}" not in rendered, "title token not consumed"
    assert "{{pid}}" not in rendered, "pid token not consumed"
    assert "{markserv}" not in rendered, "markserv asset token not consumed"

    # No leftover {{ or }} (which would indicate an uncaught token collision)
    assert "{{" not in rendered, "leftover {{ in rendered output"
    assert "}}" not in rendered, "leftover }} in rendered output"


# ---------------------------------------------------------------------------
# Caddy: @mdraw handler present and correct
# ---------------------------------------------------------------------------

def test_caddy_has_mdraw_matcher() -> None:
    assert "@mdraw" in _caddy_content()


def test_caddy_mdraw_path_matcher() -> None:
    content = _caddy_content()
    assert "path *.md" in content


def test_caddy_mdraw_query_matcher() -> None:
    content = _caddy_content()
    assert "query dl=1" in content


def test_caddy_mdraw_content_disposition() -> None:
    content = _caddy_content()
    # Both @binary and @mdraw use Content-Disposition attachment
    assert "Content-Disposition attachment" in content


def test_caddy_mdraw_before_catchall() -> None:
    """@mdraw handle block must appear before the catch-all reverse_proxy."""
    content = _caddy_content()
    mdraw_idx = content.index("handle @mdraw")
    catchall_idx = content.index("reverse_proxy 127.0.0.1:18081")
    assert mdraw_idx < catchall_idx, (
        "handle @mdraw must appear before the catch-all reverse_proxy block"
    )


# ---------------------------------------------------------------------------
# Caddy: regression guards (live structure must be present)
# ---------------------------------------------------------------------------

def test_caddy_has_protected_matcher() -> None:
    """@protected not path /oauth/* matcher must be present (live reconciliation)."""
    content = _caddy_content()
    assert "@protected" in content
    assert "not path /oauth/*" in content


def test_caddy_has_oauth_handler() -> None:
    """handle /oauth/* block must be present."""
    content = _caddy_content()
    assert "handle /oauth/*" in content


def test_caddy_basicauth_scoped_to_protected() -> None:
    """basicauth must reference @protected matcher, not /*."""
    content = _caddy_content()
    assert "basicauth @protected" in content
    # The old incorrect form basicauth /* must NOT be present
    assert "basicauth /*" not in content, (
        "basicauth must be scoped to @protected, not /* (would block /oauth/* too)"
    )


def test_caddy_has_placeholder_domain() -> None:
    """Repo file must use __FILES_DOMAIN__ placeholder, not hardcoded domain."""
    content = _caddy_content()
    assert "__FILES_DOMAIN__" in content, "Must use __FILES_DOMAIN__ placeholder"
    assert "files.mayankgupta.in" not in content, "Must not hardcode the live domain"


def test_caddy_has_placeholder_hash() -> None:
    """Repo file must use __BCRYPT_HASH__ placeholder, not a real committed hash."""
    content = _caddy_content()
    assert "__BCRYPT_HASH__" in content, "Must use __BCRYPT_HASH__ placeholder"
    # Ensure NO real bcrypt hash is committed (regex, not a pinned salt fragment):
    # finalize-caddy.sh substitutes the real $2a$/$2b$/$2y$ hash at deploy time.
    assert not re.search(r"\$2[aby]\$\d\d\$[./A-Za-z0-9]{20,}", content), (
        "A real bcrypt hash must never be committed to the repo Caddy template"
    )


def test_caddy_handler_order_oauth_before_binary() -> None:
    """handler /oauth/* must appear before @binary block."""
    content = _caddy_content()
    oauth_idx = content.index("handle /oauth/*")
    binary_idx = content.index("handle @binary")
    assert oauth_idx < binary_idx


def test_caddy_handler_order_binary_before_mdraw() -> None:
    """@binary handler must appear before @mdraw handler."""
    content = _caddy_content()
    binary_idx = content.index("handle @binary")
    mdraw_idx = content.index("handle @mdraw")
    assert binary_idx < mdraw_idx


# ---------------------------------------------------------------------------
# Systemd service: ExecStartPre=+ present
# ---------------------------------------------------------------------------

def test_service_has_execstartpre_apply_template() -> None:
    content = _service_content()
    assert "ExecStartPre=+/opt/claude-soma/scripts/markserv-apply-template.sh" in content


def test_service_execstartpre_before_execstart() -> None:
    """ExecStartPre must appear before ExecStart in the service file."""
    content = _service_content()
    pre_idx = content.index("ExecStartPre=+/opt/claude-soma/scripts/markserv-apply-template.sh")
    start_idx = content.index("ExecStart=/opt/claude-soma/scripts/markserv-launch.sh")
    assert pre_idx < start_idx


def test_service_user_still_ubuntu() -> None:
    """User=ubuntu must still be present (ExecStartPre=+ overrides root for just that command)."""
    content = _service_content()
    assert "User=ubuntu" in content


# ---------------------------------------------------------------------------
# apply-template script: bash -n syntax check
# ---------------------------------------------------------------------------

def test_apply_script_bash_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(APPLY_SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"bash -n failed:\n{result.stderr}"


# ---------------------------------------------------------------------------
# apply-template script: exit-0 safety (nonexistent src + tpl)
# ---------------------------------------------------------------------------

def test_apply_script_exits_zero_missing_src(tmp_path: Path) -> None:
    """Script must exit 0 when SOMA_MARKSERV_SRC points to a nonexistent file."""
    env = {**os.environ,
           "SOMA_MARKSERV_SRC": str(tmp_path / "nonexistent_src.html"),
           "SOMA_MARKSERV_TPL": str(tmp_path / "nonexistent_tpl.html")}
    result = subprocess.run(
        ["bash", str(APPLY_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (
        f"Script must exit 0 even with missing src/tpl; got rc={result.returncode}\n"
        f"stderr: {result.stderr}"
    )


def test_apply_script_exits_zero_missing_tpl_dir(tmp_path: Path) -> None:
    """Script must exit 0 when SOMA_MARKSERV_TPL directory does not exist."""
    # src exists, tpl is in a nonexistent directory
    fake_src = tmp_path / "fake_markdown.html"
    fake_src.write_text("<html><body>test</body></html>")
    env = {**os.environ,
           "SOMA_MARKSERV_SRC": str(fake_src),
           "SOMA_MARKSERV_TPL": "/nonexistent/deep/path/markdown.html"}
    result = subprocess.run(
        ["bash", str(APPLY_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (
        f"Script must exit 0 when tpl dir missing; got rc={result.returncode}\n"
        f"stderr: {result.stderr}"
    )


def test_apply_script_copies_when_src_and_tpl_exist(tmp_path: Path) -> None:
    """Script must copy SRC to TPL when both exist and contents differ."""
    tpl_dir = tmp_path / "templates"
    tpl_dir.mkdir()

    fake_src = tmp_path / "new_markdown.html"
    fake_src.write_text("<html><body>NEW</body></html>")

    fake_tpl = tpl_dir / "markdown.html"
    fake_tpl.write_text("<html><body>OLD</body></html>")

    env = {**os.environ,
           "SOMA_MARKSERV_SRC": str(fake_src),
           "SOMA_MARKSERV_TPL": str(fake_tpl)}
    result = subprocess.run(
        ["bash", str(APPLY_SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"rc={result.returncode}\nstderr: {result.stderr}"
    assert fake_tpl.read_text() == "<html><body>NEW</body></html>", "TPL should be overwritten with SRC"
    assert (tpl_dir / "markdown.html.orig").exists(), ".orig backup should be created"


def test_apply_script_noop_when_already_applied(tmp_path: Path) -> None:
    """Script must be idempotent: running twice should not re-copy if contents match."""
    tpl_dir = tmp_path / "templates"
    tpl_dir.mkdir()

    content = "<html><body>SAME</body></html>"
    fake_src = tmp_path / "markdown.html"
    fake_src.write_text(content)

    fake_tpl = tpl_dir / "markdown.html"
    fake_tpl.write_text(content)

    env = {**os.environ,
           "SOMA_MARKSERV_SRC": str(fake_src),
           "SOMA_MARKSERV_TPL": str(fake_tpl)}

    # Run twice
    for _ in range(2):
        result = subprocess.run(
            ["bash", str(APPLY_SCRIPT)],
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0

    # Content unchanged
    assert fake_tpl.read_text() == content


# ---------------------------------------------------------------------------
# markserv-launch.sh: bash -n syntax check (sanity)
# ---------------------------------------------------------------------------

def test_launch_script_bash_syntax() -> None:
    launch_script = REPO / "scripts" / "markserv-launch.sh"
    result = subprocess.run(
        ["bash", "-n", str(launch_script)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"bash -n failed on markserv-launch.sh:\n{result.stderr}"
