"""Shared Action dataclass — imported by pkg.py and services.py.

Lives in a separate module to break the circular dependency that would arise
if pkg.py and services.py imported Action from __init__.py (which itself
imports from those modules).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Action:
    """A planned unit of work returned by the platform layer.

    When ``dry_run=True`` the caller prints each Action without executing it.
    When ``dry_run=False`` the caller executes each command list in order.

    Fields:
        commands      Each sub-list is one argv to pass to subprocess.run.
                      An action with zero commands is informational-only.
        description   One-line human-readable summary (shown in dry-run output
                      and written to install-plan.log).
        is_privileged True when any command requires elevated privileges (sudo,
                      systemctl, chown, chmod …).  Privileged actions are
                      always listed in the sudo audit log.
        writes        Tuples of (dest_path, content) for files that will be
                      templated/written by this action.  In dry-run mode the
                      installer prints the path and a content header instead of
                      actually writing.
        note          Optional extra annotation shown in dry-run output, e.g.
                      a pointer to where a secret is read from (never the
                      secret value itself).
    """

    commands: list[list[str]]
    description: str
    is_privileged: bool = False
    writes: list[tuple[str, str]] = field(default_factory=list)
    note: str = ""
