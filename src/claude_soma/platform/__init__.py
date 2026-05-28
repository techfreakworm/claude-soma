"""claude_soma.platform — cross-platform abstraction layer (Phase 1: any-Linux).

Phases 2 (macOS), 3 (WSL2/Windows), 4 (native Windows) are design-only;
see docs/MULTI_PLATFORM_INSTALL.md for the full roadmap.

Public surface:
    Action           — a described, optionally-privileged command plan unit.
    Paths            — resolved per-OS path dataclass.
    resolve()        — detect OS and return a Paths instance.
    render_mcp_json() — render a .mcp.json string from a Paths instance.
    PackageManager   — package-manager enum.
    detect_package_manager() — detect the active package manager.
    pkg_install()    — produce an Action for installing a logical package.
    Service          — service definition dataclass.
    ServiceBackend   — abstract base for service-manager backends.
    SystemdBackend   — concrete systemd implementation (Linux/WSL2).
    LaunchdBackend   — Phase 2 stub.
    OpenRcBackend    — Phase 1 degraded stub (Alpine/no-systemd Linux).
    WindowsServiceBackend — Phase 4 stub.
"""
# Action is defined in _action.py to avoid a circular import: pkg.py and
# services.py both produce Actions, and __init__.py imports from both.
from claude_soma.platform._action import Action  # noqa: E402

from claude_soma.platform.paths import Paths, resolve, render_mcp_json  # noqa: E402
from claude_soma.platform.pkg import (  # noqa: E402
    PackageManager,
    detect_package_manager,
    pkg_install,
)
from claude_soma.platform.services import (  # noqa: E402
    Service,
    ServiceBackend,
    SystemdBackend,
    LaunchdBackend,
    OpenRcBackend,
    WindowsServiceBackend,
)

__all__ = [
    "Action",
    "Paths",
    "resolve",
    "render_mcp_json",
    "PackageManager",
    "detect_package_manager",
    "pkg_install",
    "Service",
    "ServiceBackend",
    "SystemdBackend",
    "LaunchdBackend",
    "OpenRcBackend",
    "WindowsServiceBackend",
]
