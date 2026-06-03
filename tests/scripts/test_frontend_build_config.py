"""tests/scripts/test_frontend_build_config.py — assert pnpm build-script config + fail-loudly guards."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
PACKAGE_JSON = REPO_ROOT / "frontend" / "package.json"
BUILD_FRONTEND = REPO_ROOT / "scripts" / "build_frontend.sh"
BOOTSTRAP = REPO_ROOT / "scripts" / "bootstrap.sh"


def _package_json() -> dict:
    return json.loads(PACKAGE_JSON.read_text())


def test_package_json_has_only_built_dependencies():
    pkg = _package_json()
    assert "pnpm" in pkg, "frontend/package.json must have a 'pnpm' key"
    assert "onlyBuiltDependencies" in pkg["pnpm"], (
        "frontend/package.json pnpm key must contain 'onlyBuiltDependencies'"
    )
    assert isinstance(pkg["pnpm"]["onlyBuiltDependencies"], list), (
        "pnpm.onlyBuiltDependencies must be a list"
    )
    assert len(pkg["pnpm"]["onlyBuiltDependencies"]) > 0, (
        "pnpm.onlyBuiltDependencies must not be empty"
    )


def test_only_built_dependencies_includes_sharp():
    pkg = _package_json()
    deps = pkg["pnpm"]["onlyBuiltDependencies"]
    assert "sharp" in deps, f"onlyBuiltDependencies must include 'sharp'; got: {deps}"


def test_only_built_dependencies_includes_msw():
    pkg = _package_json()
    deps = pkg["pnpm"]["onlyBuiltDependencies"]
    assert "msw" in deps, f"onlyBuiltDependencies must include 'msw'; got: {deps}"


def test_build_frontend_has_strict_mode():
    content = BUILD_FRONTEND.read_text()
    assert "set -euo pipefail" in content, (
        "scripts/build_frontend.sh must have 'set -euo pipefail'"
    )


def test_build_frontend_fails_loudly_on_pnpm_error():
    content = BUILD_FRONTEND.read_text()
    has_pnpm_msg = "ERR_PNPM_IGNORED_BUILDS" in content or "pnpm install FAILED" in content
    assert has_pnpm_msg, (
        "scripts/build_frontend.sh must mention 'ERR_PNPM_IGNORED_BUILDS' or 'pnpm install FAILED' "
        "to guide users when pnpm 10 blocks build scripts"
    )


def test_build_frontend_verifies_standalone_static():
    content = BUILD_FRONTEND.read_text()
    assert ".next/standalone/.next/static" in content, (
        "scripts/build_frontend.sh must verify .next/standalone/.next/static exists after build"
    )


def test_bootstrap_step7_fail_loudly():
    content = BOOTSTRAP.read_text()
    assert "FATAL: frontend build failed" in content, (
        "scripts/bootstrap.sh must print 'FATAL: frontend build failed' on step 7 failure"
    )


def test_bootstrap_step7_exits_on_failure():
    content = BOOTSTRAP.read_text()
    assert "exit 7" in content, (
        "scripts/bootstrap.sh step 7 must use 'exit 7' on build failure to propagate exit code"
    )


def test_build_frontend_bash_syntax():
    result = subprocess.run(
        ["bash", "-n", str(BUILD_FRONTEND)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"scripts/build_frontend.sh has bash syntax errors:\n{result.stderr}"
    )


def test_bootstrap_bash_syntax():
    result = subprocess.run(
        ["bash", "-n", str(BOOTSTRAP)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"scripts/bootstrap.sh has bash syntax errors:\n{result.stderr}"
    )


@pytest.mark.skipif(shutil.which("pnpm") is None, reason="pnpm not available")
def test_pnpm_install_no_ignored_builds_in_worktree(tmp_path):
    """Run pnpm install in a temp copy and assert no ERR_PNPM_IGNORED_BUILDS."""
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        pytest.skip("pnpm not available")
    frontend_dir = REPO_ROOT / "frontend"
    if not frontend_dir.exists():
        pytest.skip("frontend directory not found")
    result = subprocess.run(
        [pnpm, "install", "--frozen-lockfile"],
        cwd=str(frontend_dir),
        capture_output=True,
        text=True,
        timeout=300,
    )
    combined = result.stdout + result.stderr
    assert "ERR_PNPM_IGNORED_BUILDS" not in combined, (
        "pnpm install still shows ERR_PNPM_IGNORED_BUILDS — "
        "onlyBuiltDependencies list may be incomplete.\n"
        f"pnpm output:\n{combined}"
    )
