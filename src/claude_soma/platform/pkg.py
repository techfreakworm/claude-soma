"""claude_soma.platform.pkg — package-manager adapter (Phase 1: Linux).

Detects the active package manager and produces ``Action`` objects (never
runs anything when ``dry_run=True``).

Supported managers (Phase 1):
    apt   — Debian/Ubuntu (full recipes including Caddy repo, NodeSource)
    dnf   — Fedora/RHEL/CentOS (full recipes)
    pacman — Arch Linux (full recipes)
    zypper — openSUSE (best-effort)
    apk   — Alpine Linux (best-effort; note: no systemd on Alpine, so
             the service layer will warn about degraded isolation)

Phase 2+ stubs:
    brew  — macOS (raises NotImplementedError)
    winget — Windows (raises NotImplementedError)

The LOGICAL_PACKAGES table maps abstract names to per-manager recipes.
Some entries (Caddy, Node 22, piper, whisper.cpp) are multi-step recipes
because the package manager alone can't install them cleanly.

SECRETS NOTE: None of these commands pass secrets on argv.  OAuth tokens
and credentials are always read from EnvironmentFile/env-var, never argv.
"""
from __future__ import annotations

import platform
import shutil
from enum import Enum

# Import Action from _action.py (not from __init__) to break the circular
# dependency: __init__.py imports from pkg.py which would import from __init__.
from claude_soma.platform._action import Action


class PackageManager(Enum):
    """Supported package managers."""
    APT = "apt"
    DNF = "dnf"
    PACMAN = "pacman"
    ZYPPER = "zypper"
    APK = "apk"
    # Phase 2+ stubs (raise NotImplementedError when recipes are requested)
    BREW = "brew"
    WINGET = "winget"


def _read_os_release() -> dict[str, str]:
    """Parse /etc/os-release into a dict (best-effort)."""
    result: dict[str, str] = {}
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, _, v = line.partition("=")
                        result[k.strip()] = v.strip().strip('"').strip("'")
            break
        except OSError:
            continue
    return result


def detect_package_manager(
    _os_release_override: dict[str, str] | None = None,
) -> PackageManager:
    """Detect the package manager on the current system.

    Parameters
    ----------
    _os_release_override:
        For testing: supply a pre-parsed ``/etc/os-release`` dict to avoid
        touching the real file.

    On macOS raises ``NotImplementedError`` (Phase 2).
    On Windows raises ``NotImplementedError`` (Phase 3/4).
    """
    sys_name = platform.system()
    if sys_name == "Darwin":
        raise NotImplementedError(
            "macOS package manager (brew) is Phase 2. "
            "See docs/MULTI_PLATFORM_INSTALL.md §6."
        )
    if sys_name == "Windows":
        raise NotImplementedError(
            "Windows package manager (winget/choco) is Phase 3/4. "
            "See docs/MULTI_PLATFORM_INSTALL.md §6."
        )

    osr = _os_release_override if _os_release_override is not None else _read_os_release()
    os_id = osr.get("ID", "").lower()
    os_id_like = osr.get("ID_LIKE", "").lower()

    # APT: Debian/Ubuntu family
    if shutil.which("apt-get") and (
        "debian" in os_id_like
        or "ubuntu" in os_id_like
        or os_id in ("debian", "ubuntu", "linuxmint", "raspbian", "pop")
    ):
        return PackageManager.APT
    # DNF: Fedora/RHEL/CentOS/Rocky/Alma
    if shutil.which("dnf") and (
        "fedora" in os_id_like
        or "rhel" in os_id_like
        or os_id in ("fedora", "rhel", "centos", "rocky", "almalinux")
    ):
        return PackageManager.DNF
    # Pacman: Arch / Manjaro / EndeavourOS
    if shutil.which("pacman") and (
        "arch" in os_id_like or os_id in ("arch", "manjaro", "endeavouros", "garuda")
    ):
        return PackageManager.PACMAN
    # Zypper: openSUSE
    if shutil.which("zypper") and (
        "suse" in os_id_like or os_id in ("opensuse-leap", "opensuse-tumbleweed", "suse")
    ):
        return PackageManager.ZYPPER
    # APK: Alpine
    if shutil.which("apk") and os_id == "alpine":
        return PackageManager.APK

    # Fallback: try by binary alone (less reliable without os-release)
    for binary, pm in [
        ("apt-get", PackageManager.APT),
        ("dnf", PackageManager.DNF),
        ("pacman", PackageManager.PACMAN),
        ("zypper", PackageManager.ZYPPER),
        ("apk", PackageManager.APK),
    ]:
        if shutil.which(binary):
            return pm

    raise RuntimeError(
        "Cannot detect a supported package manager (apt/dnf/pacman/zypper/apk). "
        "Rerun with --dry-run to see the full plan."
    )


# ---------------------------------------------------------------------------
# Internal recipe helpers
# ---------------------------------------------------------------------------

# Type alias: a recipe is a list of argv lists (each list = one command)
_Recipe = list[list[str]]


def _apt_install(*pkgs: str) -> _Recipe:
    return [["sudo", "apt-get", "install", "-y", *pkgs]]


def _dnf_install(*pkgs: str) -> _Recipe:
    return [["sudo", "dnf", "install", "-y", *pkgs]]


def _pacman_install(*pkgs: str) -> _Recipe:
    return [["sudo", "pacman", "-S", "--noconfirm", *pkgs]]


def _zypper_install(*pkgs: str) -> _Recipe:
    return [["sudo", "zypper", "--non-interactive", "install", *pkgs]]


def _apk_add(*pkgs: str) -> _Recipe:
    return [["sudo", "apk", "add", *pkgs]]


# ---------------------------------------------------------------------------
# LOGICAL_PACKAGES
#
# Maps a logical name → {PackageManager.value: recipe (list[list[str]])}.
#
# Rules:
# - Privileged commands must start with ["sudo", ...] so callers can detect
#   and log them.
# - SECRETS NOTE: no recipe passes secrets/tokens on argv.
# ---------------------------------------------------------------------------
LOGICAL_PACKAGES: dict[str, dict[str, _Recipe]] = {

    # ---------------------------------------------------------------- ffmpeg
    "ffmpeg": {
        PackageManager.APT.value:    _apt_install("ffmpeg"),
        PackageManager.DNF.value:    _dnf_install("ffmpeg"),
        PackageManager.PACMAN.value: _pacman_install("ffmpeg"),
        PackageManager.ZYPPER.value: _zypper_install("ffmpeg"),
        PackageManager.APK.value:    _apk_add("ffmpeg"),
    },

    # ------------------------------------------------------------------ tmux
    "tmux": {
        PackageManager.APT.value:    _apt_install("tmux"),
        PackageManager.DNF.value:    _dnf_install("tmux"),
        PackageManager.PACMAN.value: _pacman_install("tmux"),
        PackageManager.ZYPPER.value: _zypper_install("tmux"),
        PackageManager.APK.value:    _apk_add("tmux"),
    },

    # ----------------------------------------------------------------- curl
    "curl": {
        PackageManager.APT.value:    _apt_install("curl"),
        PackageManager.DNF.value:    _dnf_install("curl"),
        PackageManager.PACMAN.value: _pacman_install("curl"),
        PackageManager.ZYPPER.value: _zypper_install("curl"),
        PackageManager.APK.value:    _apk_add("curl"),
    },

    # ------------------------------------------------------------------ git
    "git": {
        PackageManager.APT.value:    _apt_install("git"),
        PackageManager.DNF.value:    _dnf_install("git"),
        PackageManager.PACMAN.value: _pacman_install("git"),
        PackageManager.ZYPPER.value: _zypper_install("git"),
        PackageManager.APK.value:    _apk_add("git"),
    },

    # ------------------------------------------------------------ python3.12
    "python3.12": {
        PackageManager.APT.value: _apt_install("python3.12", "python3.12-venv"),
        PackageManager.DNF.value: _dnf_install("python3.12"),
        PackageManager.PACMAN.value: _pacman_install("python"),
        PackageManager.ZYPPER.value: _zypper_install("python312"),
        PackageManager.APK.value: _apk_add("python3", "py3-pip"),
    },

    # ------------------------------------------------------ build-essential
    "build-essential": {
        PackageManager.APT.value: _apt_install("build-essential", "cmake"),
        PackageManager.DNF.value: _dnf_install("gcc", "g++", "cmake", "make"),
        PackageManager.PACMAN.value: _pacman_install("base-devel", "cmake"),
        PackageManager.ZYPPER.value: _zypper_install("gcc", "gcc-c++", "cmake", "make"),
        PackageManager.APK.value: _apk_add("build-base", "cmake"),
    },

    # --------------------------------------------------------------- openssl
    "openssl": {
        PackageManager.APT.value:    _apt_install("openssl"),
        PackageManager.DNF.value:    _dnf_install("openssl"),
        PackageManager.PACMAN.value: _pacman_install("openssl"),
        PackageManager.ZYPPER.value: _zypper_install("openssl"),
        PackageManager.APK.value:    _apk_add("openssl"),
    },

    # ----------------------------------------------------------------- caddy
    # Caddy is a multi-step recipe: add the official apt/dnf/pacman repo,
    # then install.
    "caddy": {
        PackageManager.APT.value: [
            ["sudo", "apt-get", "install", "-y",
             "debian-keyring", "debian-archive-keyring", "apt-transport-https", "curl"],
            # SECRETS NOTE: curl fetches a PUBLIC GPG key — no secret on argv.
            ["sudo", "bash", "-c",
             "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key'"
             " | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg"],
            ["sudo", "bash", "-c",
             "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt'"
             " | tee /etc/apt/sources.list.d/caddy-stable.list"],
            ["sudo", "apt-get", "update"],
            ["sudo", "apt-get", "install", "-y", "caddy"],
        ],
        PackageManager.DNF.value: [
            ["sudo", "dnf", "copr", "enable", "-y", "caddy/caddy"],
            ["sudo", "dnf", "install", "-y", "caddy"],
        ],
        PackageManager.PACMAN.value: _pacman_install("caddy"),
        PackageManager.ZYPPER.value: [
            ["sudo", "zypper", "addrepo", "--refresh",
             "https://download.opensuse.org/repositories/server:http/openSUSE_Leap_15.5/",
             "server-http"],
            ["sudo", "zypper", "--non-interactive", "install", "caddy"],
        ],
        PackageManager.APK.value: _apk_add("caddy"),
    },

    # --------------------------------------------------- node22 (Node.js 22)
    # SECRETS NOTE: NodeSource setup script fetches from a PUBLIC URL only.
    "node22": {
        PackageManager.APT.value: [
            ["sudo", "bash", "-c",
             "curl -fsSL https://deb.nodesource.com/setup_22.x | bash -"],
            ["sudo", "apt-get", "install", "-y", "nodejs"],
        ],
        PackageManager.DNF.value: [
            ["sudo", "bash", "-c",
             "curl -fsSL https://rpm.nodesource.com/setup_22.x | bash -"],
            ["sudo", "dnf", "install", "-y", "nodejs"],
        ],
        PackageManager.PACMAN.value: _pacman_install("nodejs", "npm"),
        PackageManager.ZYPPER.value: _zypper_install("nodejs22", "npm22"),
        PackageManager.APK.value: _apk_add("nodejs", "npm"),
    },

    # ---------------------------------------------------- bun (JS runtime)
    # SECRETS NOTE: bun installer fetches from a PUBLIC URL only.
    "bun": {
        PackageManager.APT.value: [
            ["bash", "-c", "curl -fsSL https://bun.sh/install | bash"],
        ],
        PackageManager.DNF.value: [
            ["bash", "-c", "curl -fsSL https://bun.sh/install | bash"],
        ],
        PackageManager.PACMAN.value: _pacman_install("bun"),
        PackageManager.ZYPPER.value: [
            ["bash", "-c", "curl -fsSL https://bun.sh/install | bash"],
        ],
        PackageManager.APK.value: [
            ["bash", "-c", "curl -fsSL https://bun.sh/install | bash"],
        ],
    },

    # ------------------------------------------------------ gh (GitHub CLI)
    "gh": {
        PackageManager.APT.value: [
            # SECRETS NOTE: GitHub CLI GPG key is fetched from a PUBLIC URL.
            ["sudo", "bash", "-c",
             "(type -p wget >/dev/null || apt-get install wget -y)"
             " && mkdir -p -m 755 /etc/apt/keyrings"
             " && wget -qO- https://cli.github.com/packages/githubcli-archive-keyring.gpg"
             " | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null"
             " && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg"
             " && echo \"deb [arch=$(dpkg --print-architecture)"
             " signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg]"
             " https://cli.github.com/packages stable main\""
             " | tee /etc/apt/sources.list.d/github-cli.list > /dev/null"
             " && apt-get update && apt-get install gh -y"],
        ],
        PackageManager.DNF.value: _dnf_install("gh"),
        PackageManager.PACMAN.value: _pacman_install("github-cli"),
        PackageManager.ZYPPER.value: _zypper_install("gh"),
        PackageManager.APK.value: _apk_add("github-cli"),
    },

    # -------------------------------------------- whisper-build-deps
    # whisper.cpp must be built from source.  These are the compile-time deps.
    # The actual clone + cmake + make is done by the install module separately.
    "whisper-build-deps": {
        PackageManager.APT.value: _apt_install(
            "build-essential", "cmake", "libsdl2-dev", "libgomp1",
        ),
        PackageManager.DNF.value: _dnf_install(
            "gcc", "gcc-c++", "cmake", "SDL2-devel", "libgomp",
        ),
        PackageManager.PACMAN.value: _pacman_install(
            "base-devel", "cmake", "sdl2",
        ),
        PackageManager.ZYPPER.value: _zypper_install(
            "gcc", "gcc-c++", "cmake", "libSDL2-devel",
        ),
        PackageManager.APK.value: _apk_add("build-base", "cmake", "sdl2-dev"),
    },

    # --------------------------------------------------------- playwright-mcp
    # SECRETS NOTE: npm installs from a PUBLIC registry only.
    "playwright-mcp": {
        PackageManager.APT.value: [
            ["sudo", "npm", "install", "-g", "@playwright/mcp@latest"],
        ],
        PackageManager.DNF.value: [
            ["sudo", "npm", "install", "-g", "@playwright/mcp@latest"],
        ],
        PackageManager.PACMAN.value: [
            ["sudo", "npm", "install", "-g", "@playwright/mcp@latest"],
        ],
        PackageManager.ZYPPER.value: [
            ["sudo", "npm", "install", "-g", "@playwright/mcp@latest"],
        ],
        PackageManager.APK.value: [
            ["sudo", "npm", "install", "-g", "@playwright/mcp@latest"],
        ],
    },
}


def pkg_install(
    logical_name: str,
    *,
    dry_run: bool,  # noqa: ARG001  (kept for API consistency; caller checks)
    pm: PackageManager | None = None,
) -> Action:
    """Return an Action describing how to install ``logical_name``.

    Parameters
    ----------
    logical_name:
        A key from ``LOGICAL_PACKAGES`` (e.g. ``"ffmpeg"``, ``"caddy"``).
    dry_run:
        Accepted but not used internally; the Action is always returned
        without execution.  The *caller* (install.py) decides whether to
        execute or print.
    pm:
        Override the auto-detected package manager.  Useful in tests.

    Returns
    -------
    Action
        ``commands`` is the list of argv lists to execute in order.
        ``is_privileged`` is ``True`` when any command contains ``"sudo"``.

    Raises
    ------
    KeyError
        Unknown logical package name.
    NotImplementedError
        No recipe for this package/manager combination.
    RuntimeError
        Cannot detect a package manager.
    """
    if logical_name not in LOGICAL_PACKAGES:
        raise KeyError(
            f"Unknown logical package {logical_name!r}. "
            f"Known: {sorted(LOGICAL_PACKAGES)}"
        )

    if pm is None:
        pm = detect_package_manager()

    pm_recipes = LOGICAL_PACKAGES[logical_name]
    recipe = pm_recipes.get(pm.value)
    if recipe is None:
        raise NotImplementedError(
            f"No recipe for {logical_name!r} on {pm.value}. "
            "Open a PR to add it, or install manually."
        )

    privileged = any("sudo" in cmd for cmd in recipe)

    return Action(
        commands=list(recipe),
        description=f"Install {logical_name!r} via {pm.value}",
        is_privileged=privileged,
        # SECRETS NOTE: no package-install recipe passes secrets on argv.
    )
