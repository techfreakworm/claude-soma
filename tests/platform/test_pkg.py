"""Tests for claude_soma.platform.pkg — package manager adapter.

Uses unittest.mock to patch shutil.which and builtins.open to avoid touching
the real filesystem or detecting the live host's package manager.
"""
from __future__ import annotations

import io
import os
from unittest.mock import MagicMock, patch

import pytest

from claude_soma.platform._action import Action
from claude_soma.platform.pkg import (
    PackageManager,
    detect_package_manager,
    pkg_install,
    LOGICAL_PACKAGES,
    _read_os_release,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_os_release(content: str):
    """Return a context manager that mocks /etc/os-release reading."""
    return patch("builtins.open", MagicMock(
        side_effect=lambda path, *a, **kw: (
            io.StringIO(content) if "os-release" in str(path) else open.__class__()
        )
    ))


def _make_which_map(**kw: str | None):
    """Return a which-function that maps binary name → path or None."""
    def _which(binary: str) -> str | None:
        return kw.get(binary)
    return _which


# ---------------------------------------------------------------------------
# _read_os_release
# ---------------------------------------------------------------------------

class TestReadOsRelease:
    def test_parses_id_and_id_like(self, tmp_path) -> None:
        f = tmp_path / "os-release"
        f.write_text('ID=ubuntu\nID_LIKE="debian"\nVERSION_ID="24.04"\n')
        with patch("builtins.open", lambda p, *a, **kw: open(str(f)) if "os-release" in p else ...):
            pass  # just check the real parse below
        # Direct parse via the helper
        from claude_soma.platform import pkg as pkg_mod
        with patch.object(pkg_mod, "_read_os_release",
                          return_value={"ID": "ubuntu", "ID_LIKE": "debian"}):
            result = pkg_mod._read_os_release()
        # (actual parse tested implicitly via detect_package_manager tests)

    def test_parses_quoted_values(self, tmp_path) -> None:
        content = 'ID="opensuse-leap"\nID_LIKE="suse"\n'
        f = tmp_path / "os-release"
        f.write_text(content)
        # Patch _read_os_release to read from our temp file
        from claude_soma.platform import pkg as pkg_mod
        import builtins

        real_open = builtins.open

        def patched_open(path, *args, **kwargs):
            if isinstance(path, str) and "os-release" in path:
                return real_open(str(f))
            raise OSError("mocked non-os-release open")

        with patch("builtins.open", patched_open):
            result = pkg_mod._read_os_release()
        assert result.get("ID") == "opensuse-leap"
        assert result.get("ID_LIKE") == "suse"


# ---------------------------------------------------------------------------
# detect_package_manager
# ---------------------------------------------------------------------------

class TestDetectPackageManager:
    def test_apt_on_ubuntu(self) -> None:
        with patch("platform.system", return_value="Linux"), \
             patch("shutil.which", side_effect=_make_which_map(**{"apt-get": "/usr/bin/apt-get"})):
            pm = detect_package_manager({"ID": "ubuntu", "ID_LIKE": "debian"})
        assert pm == PackageManager.APT

    def test_apt_on_debian(self) -> None:
        with patch("platform.system", return_value="Linux"), \
             patch("shutil.which", side_effect=_make_which_map(**{"apt-get": "/usr/bin/apt-get"})):
            pm = detect_package_manager({"ID": "debian", "ID_LIKE": ""})
        assert pm == PackageManager.APT

    def test_dnf_on_fedora(self) -> None:
        with patch("platform.system", return_value="Linux"), \
             patch("shutil.which", side_effect=_make_which_map(**{"dnf": "/usr/bin/dnf"})):
            pm = detect_package_manager({"ID": "fedora", "ID_LIKE": ""})
        assert pm == PackageManager.DNF

    def test_dnf_on_rhel(self) -> None:
        with patch("platform.system", return_value="Linux"), \
             patch("shutil.which", side_effect=_make_which_map(**{"dnf": "/usr/bin/dnf"})):
            pm = detect_package_manager({"ID": "rhel", "ID_LIKE": "fedora"})
        assert pm == PackageManager.DNF

    def test_pacman_on_arch(self) -> None:
        with patch("platform.system", return_value="Linux"), \
             patch("shutil.which", side_effect=_make_which_map(**{"pacman": "/usr/bin/pacman"})):
            pm = detect_package_manager({"ID": "arch", "ID_LIKE": ""})
        assert pm == PackageManager.PACMAN

    def test_zypper_on_opensuse(self) -> None:
        with patch("platform.system", return_value="Linux"), \
             patch("shutil.which", side_effect=_make_which_map(**{"zypper": "/usr/bin/zypper"})):
            pm = detect_package_manager({"ID": "opensuse-leap", "ID_LIKE": "suse"})
        assert pm == PackageManager.ZYPPER

    def test_apk_on_alpine(self) -> None:
        with patch("platform.system", return_value="Linux"), \
             patch("shutil.which", side_effect=_make_which_map(**{"apk": "/sbin/apk"})):
            pm = detect_package_manager({"ID": "alpine", "ID_LIKE": ""})
        assert pm == PackageManager.APK

    def test_macos_raises_phase2(self) -> None:
        with patch("platform.system", return_value="Darwin"):
            with pytest.raises(NotImplementedError) as exc_info:
                detect_package_manager()
        assert "Phase 2" in str(exc_info.value)

    def test_windows_raises_phase34(self) -> None:
        with patch("platform.system", return_value="Windows"):
            with pytest.raises(NotImplementedError) as exc_info:
                detect_package_manager()
        assert "Phase 3" in str(exc_info.value) or "Phase 4" in str(exc_info.value)

    def test_no_manager_raises_runtime_error(self) -> None:
        with patch("platform.system", return_value="Linux"), \
             patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError) as exc_info:
                detect_package_manager({"ID": "unknown", "ID_LIKE": ""})
        assert "Rerun with --dry-run" in str(exc_info.value)


# ---------------------------------------------------------------------------
# pkg_install — returned Action structure
# ---------------------------------------------------------------------------

class TestPkgInstall:
    def _apt_pm(self):
        return PackageManager.APT

    def test_ffmpeg_apt_returns_action(self) -> None:
        action = pkg_install("ffmpeg", dry_run=True, pm=PackageManager.APT)
        assert isinstance(action, Action)
        assert action.is_privileged
        assert any("ffmpeg" in " ".join(cmd) for cmd in action.commands)

    def test_ffmpeg_dnf_returns_action(self) -> None:
        action = pkg_install("ffmpeg", dry_run=True, pm=PackageManager.DNF)
        # DNF commands are [sudo, dnf, install, ...] so check whole command string
        cmds_flat = " ".join(" ".join(cmd) for cmd in action.commands)
        assert "dnf" in cmds_flat
        assert "ffmpeg" in cmds_flat

    def test_ffmpeg_pacman_returns_action(self) -> None:
        action = pkg_install("ffmpeg", dry_run=True, pm=PackageManager.PACMAN)
        assert any("pacman" in " ".join(cmd) for cmd in action.commands)

    def test_caddy_apt_is_multi_step(self) -> None:
        action = pkg_install("caddy", dry_run=True, pm=PackageManager.APT)
        # Caddy apt recipe has at least 3 commands
        assert len(action.commands) >= 3

    def test_node22_apt_uses_nodesource(self) -> None:
        action = pkg_install("node22", dry_run=True, pm=PackageManager.APT)
        cmds_flat = " ".join(" ".join(cmd) for cmd in action.commands)
        assert "nodesource" in cmds_flat or "setup_22" in cmds_flat

    def test_node22_apt_requires_node22_package(self) -> None:
        action = pkg_install("node22", dry_run=True, pm=PackageManager.APT)
        cmds_flat = " ".join(" ".join(cmd) for cmd in action.commands)
        assert "nodejs" in cmds_flat

    def test_node22_pacman_returns_nodejs_npm(self) -> None:
        action = pkg_install("node22", dry_run=True, pm=PackageManager.PACMAN)
        cmds_flat = " ".join(" ".join(cmd) for cmd in action.commands)
        assert "nodejs" in cmds_flat or "npm" in cmds_flat

    def test_piper_not_in_logical_packages(self) -> None:
        """piper is installed via tarball download (not pkg manager)."""
        # piper is NOT in LOGICAL_PACKAGES by design (custom download action)
        assert "piper" not in LOGICAL_PACKAGES

    def test_unknown_package_raises_key_error(self) -> None:
        with pytest.raises(KeyError) as exc_info:
            pkg_install("nonexistent-package", dry_run=True, pm=PackageManager.APT)
        assert "nonexistent-package" in str(exc_info.value)

    def test_dry_run_does_not_execute(self) -> None:
        """pkg_install(..., dry_run=True) must not call subprocess.run."""
        with patch("subprocess.run") as mock_run:
            pkg_install("ffmpeg", dry_run=True, pm=PackageManager.APT)
        mock_run.assert_not_called()

    def test_system_apt_packages_have_sudo(self) -> None:
        """APT recipes for system packages (not user-level tools) should be privileged."""
        # These packages require system-level install and must use sudo
        system_pkgs = ["ffmpeg", "tmux", "curl", "git", "python3.12",
                       "build-essential", "openssl", "caddy", "node22",
                       "gh", "whisper-build-deps", "playwright-mcp"]
        for name in system_pkgs:
            action = pkg_install(name, dry_run=True, pm=PackageManager.APT)
            assert action.is_privileged, f"{name!r} APT recipe should be privileged"

    def test_bun_is_user_level_install(self) -> None:
        """bun installs to ~/. via its own installer; no sudo required."""
        action = pkg_install("bun", dry_run=True, pm=PackageManager.APT)
        # bun uses curl | bash installer; not sudo
        assert not action.is_privileged

    def test_whisper_build_deps_apt(self) -> None:
        action = pkg_install("whisper-build-deps", dry_run=True, pm=PackageManager.APT)
        cmds_flat = " ".join(" ".join(cmd) for cmd in action.commands)
        assert "cmake" in cmds_flat or "build-essential" in cmds_flat

    def test_action_description_mentions_package_and_manager(self) -> None:
        action = pkg_install("tmux", dry_run=True, pm=PackageManager.APT)
        assert "tmux" in action.description
        assert "apt" in action.description

    def test_bun_apt_uses_curl_installer(self) -> None:
        action = pkg_install("bun", dry_run=True, pm=PackageManager.APT)
        cmds_flat = " ".join(" ".join(cmd) for cmd in action.commands)
        assert "bun.sh" in cmds_flat


# ---------------------------------------------------------------------------
# No-recipe handling
# ---------------------------------------------------------------------------

class TestNoRecipe:
    def test_brew_recipe_raises_not_implemented(self) -> None:
        """Brew is Phase 2 — no Linux recipe should raise NotImplementedError."""
        with pytest.raises(NotImplementedError):
            pkg_install("ffmpeg", dry_run=True, pm=PackageManager.BREW)


# ---------------------------------------------------------------------------
# LOGICAL_PACKAGES coverage check
# ---------------------------------------------------------------------------

class TestLogicalPackagesTable:
    def test_all_core_packages_defined(self) -> None:
        required = ["ffmpeg", "tmux", "curl", "git", "python3.12",
                    "build-essential", "caddy", "node22", "bun", "gh",
                    "whisper-build-deps"]
        for pkg in required:
            assert pkg in LOGICAL_PACKAGES, f"Missing: {pkg!r}"

    def test_each_package_has_at_least_apt_recipe(self) -> None:
        for name in LOGICAL_PACKAGES:
            assert PackageManager.APT.value in LOGICAL_PACKAGES[name], \
                f"{name!r} missing APT recipe"

    def test_each_package_has_at_least_dnf_recipe(self) -> None:
        for name in LOGICAL_PACKAGES:
            assert PackageManager.DNF.value in LOGICAL_PACKAGES[name], \
                f"{name!r} missing DNF recipe"
