"""claude_soma.platform.services — service-manager adapter (Phase 1: Linux).

Provides an abstract ``ServiceBackend`` with four concrete implementations:

    SystemdBackend       — fully implemented; Linux + WSL2.
    OpenRcBackend        — Phase 1 degraded stub (Alpine / no-systemd Linux).
    LaunchdBackend       — Phase 2 stub (macOS).
    WindowsServiceBackend — Phase 4 stub (native Windows).

``SystemdBackend`` mirrors the exact ``sudo systemd-run`` invocation used by
``spawner.py`` so the install module and the orchestrator share the same
cgroup-isolation pattern.

SECRETS NOTE: No method in this module passes secrets on argv.  Secrets are
loaded at runtime via systemd EnvironmentFile (leading ``-`` = optional).
The EnvironmentFile path appears in the unit file written to disk, not on the
command line of any subprocess call.
"""
from __future__ import annotations

import os
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal, Optional

from claude_soma.platform._action import Action


# ---------------------------------------------------------------------------
# Service dataclass
# ---------------------------------------------------------------------------

@dataclass
class Service:
    """Logical description of a long-running service (backend-agnostic).

    Backends render this into their native unit format (systemd .service
    file, launchd plist, NSSM arguments, …).
    """
    name: str
    description: str
    exec_argv: list[str]           # e.g. ["/usr/bin/python", "-m", "..."]
    env: dict[str, str] = field(default_factory=dict)
    work_dir: str = "/opt/claude-soma"
    restart_policy: Literal["always", "on-failure", "no"] = "always"
    restart_sec: int = 5
    log_paths: dict[str, str] = field(default_factory=dict)
                                   # {"stdout": "/var/log/...", "stderr": "..."}
    user: str = "ubuntu"
    group: str = "ubuntu"
    after: list[str] = field(default_factory=lambda: ["network-online.target"])
    requires: list[str] = field(default_factory=list)
    type_: Literal["simple", "oneshot"] = "simple"
    remain_after_exit: bool = False
    env_file: Optional[str] = None  # systemd EnvironmentFile path (no secret on argv)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class ServiceBackend(ABC):
    """Abstract service-manager backend.

    Callers always receive ``Action`` objects.  When ``dry_run=True`` (on
    the install module level) the caller prints rather than executes.

    ``isolation_strength`` is a property rather than a method so the install
    module can warn operators on non-Linux platforms without instantiating a
    backend:

        if backend.isolation_strength != "strong":
            print("WARNING: degraded lead isolation on this platform …")
    """

    @property
    @abstractmethod
    def isolation_strength(self) -> Literal["strong", "medium", "none"]:
        """Isolation level this backend provides for lead processes.

        strong — cgroup-isolated (systemd-run); channel restart cannot
                 reach leads.
        medium — process-group isolated (setsid/launchd); no cgroup blast
                 radius protection.
        none   — no isolation; best-effort only.
        """

    @abstractmethod
    def install_service(self, svc: Service) -> Action:
        """Return an Action that installs (but does not start) the service."""

    @abstractmethod
    def install_timer(
        self,
        name: str,
        on_calendar: str,
        *,
        service_name: str,
    ) -> Action:
        """Return an Action that installs a timer/scheduled job."""

    @abstractmethod
    def enable(self, name: str) -> Action:
        """Return an Action that enables and starts the named service."""

    @abstractmethod
    def restart(self, name: str) -> Action:
        """Return an Action that restarts the named service."""

    @abstractmethod
    def status(self, name: str) -> str:
        """Return a human-readable status string for the named service.

        This is the one backend method that RUNS immediately (read-only).
        It is excluded from the dry-run plan because it produces no state
        change.
        """

    @abstractmethod
    def spawn_isolated(
        self,
        name: str,
        argv: list[str],
        env: dict[str, str],
        *,
        isolation_strength: Literal["strong", "medium", "none"],
    ) -> Action:
        """Return an Action that spawns a lead process in isolation.

        Mirrors the systemd-run invocation in ``spawner._wrap_in_transient_unit``.

        SECRETS NOTE: ``env`` must NOT contain raw secret values — pass
        env-var NAMES that reference values already present in the
        EnvironmentFile, or use a separate env-file path.
        """


# ---------------------------------------------------------------------------
# Systemd unit file templates (Python f-strings, stdlib only)
# ---------------------------------------------------------------------------

_SYSTEMD_SERVICE_TMPL = """\
# Generated by claude_soma.platform.services.SystemdBackend
# DO NOT EDIT BY HAND — regenerate with: python -m claude_soma.install
[Unit]
Description={description}
After={after}
Wants={after}
{requires_line}
[Service]
Type={type_}
{remain_after_exit_line}User={user}
Group={group}
WorkingDirectory={work_dir}
{env_file_line}{env_lines}{exec_start_line}Restart={restart_policy}
RestartSec={restart_sec}
{log_lines}
[Install]
WantedBy=multi-user.target
"""

_SYSTEMD_TIMER_TMPL = """\
# Generated by claude_soma.platform.services.SystemdBackend
[Unit]
Description=Timer for {service_name}
After=network-online.target

[Timer]
OnCalendar={on_calendar}
Persistent=true
Unit={service_name}.service

[Install]
WantedBy=timers.target
"""


def _render_service(svc: Service) -> str:
    """Render a systemd .service unit file from a ``Service`` dataclass."""
    after_str = " ".join(svc.after) if svc.after else "multi-user.target"
    requires_line = ""
    if svc.requires:
        requires_line = f"Requires={' '.join(svc.requires)}\n"

    remain_line = "RemainAfterExit=yes\n" if svc.remain_after_exit else ""

    env_file_line = ""
    if svc.env_file:
        # Leading `-` = optional (so spawn doesn't fail on a box without the
        # file, e.g. CI/dev).  SECRETS NOTE: path only, no secret value.
        env_file_line = f"EnvironmentFile=-{svc.env_file}\n"

    env_lines = ""
    for k, v in svc.env.items():
        env_lines += f"Environment={k}={v}\n"

    exec_start_line = "ExecStart=" + " ".join(svc.exec_argv) + "\n"

    log_lines = ""
    if "stdout" in svc.log_paths:
        log_lines += f"StandardOutput=append:{svc.log_paths['stdout']}\n"
    if "stderr" in svc.log_paths:
        log_lines += f"StandardError=append:{svc.log_paths['stderr']}\n"

    return _SYSTEMD_SERVICE_TMPL.format(
        description=svc.description,
        after=after_str,
        requires_line=requires_line,
        type_=svc.type_,
        remain_after_exit_line=remain_line,
        user=svc.user,
        group=svc.group,
        work_dir=svc.work_dir,
        env_file_line=env_file_line,
        env_lines=env_lines,
        exec_start_line=exec_start_line,
        restart_policy=svc.restart_policy,
        restart_sec=svc.restart_sec,
        log_lines=log_lines.rstrip(),
    )


def _render_timer(name: str, on_calendar: str, service_name: str) -> str:
    return _SYSTEMD_TIMER_TMPL.format(
        service_name=service_name,
        on_calendar=on_calendar,
    )


# ---------------------------------------------------------------------------
# SystemdBackend
# ---------------------------------------------------------------------------

class SystemdBackend(ServiceBackend):
    """Fully-implemented backend for Linux systems running systemd (Phase 1).

    Also covers WSL2 (which ships systemd since 2022).

    ``spawn_isolated`` builds the exact ``sudo systemd-run`` invocation used
    by ``spawner._wrap_in_transient_unit`` so the install module and the
    orchestrator share one authoritative pattern.

    SECRETS NOTE: No method here passes secrets on argv.  The
    EnvironmentFile path is embedded in the unit file body (a write), not
    on the systemctl/systemd-run command line.
    """

    # Paths to privileged binaries — overridable for tests.
    systemctl_bin: str = "/usr/bin/systemctl"
    systemd_run_bin: str = "/usr/bin/systemd-run"
    sudo_bin: str = "/usr/bin/sudo"
    unit_dir: str = "/etc/systemd/system"

    def __init__(
        self,
        *,
        systemctl_bin: str = "/usr/bin/systemctl",
        systemd_run_bin: str = "/usr/bin/systemd-run",
        sudo_bin: str = "/usr/bin/sudo",
        unit_dir: str = "/etc/systemd/system",
    ) -> None:
        self.systemctl_bin = systemctl_bin
        self.systemd_run_bin = systemd_run_bin
        self.sudo_bin = sudo_bin
        self.unit_dir = unit_dir

    @property
    def isolation_strength(self) -> Literal["strong", "medium", "none"]:
        return "strong"

    def install_service(self, svc: Service) -> Action:
        """Render the unit file and produce install + daemon-reload commands.

        Writes the unit file using ``sudo install`` (atomic, correct perms)
        so we never need a tempfile visible on argv.
        """
        unit_path = f"{self.unit_dir}/{svc.name}.service"
        content = _render_service(svc)

        # We write via `sudo tee` (stdin pipe, no secret on argv) so:
        #   echo <content> | sudo tee <dest>
        # But echo puts content on argv — use `sudo install` with a tempfile.
        # In real execution the installer writes to a tmp file then installs;
        # for the Action we record the write tuple and the install command.
        #
        # PRIVILEGED ACTIONS:
        #   sudo install -m 644 <tmpfile> <unit_path>   (file write)
        #   sudo systemctl daemon-reload                (systemd state change)
        return Action(
            commands=[
                # install.py will write the rendered content to a tempfile
                # then run this command.  The tempfile path is a placeholder
                # that install.py replaces at execution time.
                [self.sudo_bin, "install", "-m", "644",
                 "__TMPFILE__", unit_path],
                [self.sudo_bin, self.systemctl_bin, "daemon-reload"],
            ],
            description=f"Install systemd unit {svc.name}.service",
            is_privileged=True,
            writes=[(unit_path, content)],
            note=(
                f"Unit file content rendered from Service dataclass. "
                f"Env secrets loaded at runtime via EnvironmentFile "
                f"({svc.env_file or 'none'}), never on argv."
            ),
        )

    def install_timer(
        self,
        name: str,
        on_calendar: str,
        *,
        service_name: str,
    ) -> Action:
        """Render a .timer unit and produce install + daemon-reload commands."""
        timer_path = f"{self.unit_dir}/{name}.timer"
        # A companion .service stub (Type=oneshot) is usually already installed
        # by install_service; this only installs the .timer file.
        content = _render_timer(name, on_calendar, service_name)
        return Action(
            commands=[
                [self.sudo_bin, "install", "-m", "644",
                 "__TMPFILE__", timer_path],
                [self.sudo_bin, self.systemctl_bin, "daemon-reload"],
            ],
            description=f"Install systemd timer {name}.timer (OnCalendar={on_calendar!r})",
            is_privileged=True,
            writes=[(timer_path, content)],
        )

    def enable(self, name: str) -> Action:
        """Produce an ``enable --now`` action for one or more unit names."""
        return Action(
            commands=[[self.sudo_bin, self.systemctl_bin, "enable", "--now", name]],
            description=f"Enable and start {name}",
            is_privileged=True,
        )

    def restart(self, name: str) -> Action:
        return Action(
            commands=[[self.sudo_bin, self.systemctl_bin, "restart", name]],
            description=f"Restart {name}",
            is_privileged=True,
        )

    def status(self, name: str) -> str:
        """Run ``systemctl status`` and return stdout (read-only)."""
        import subprocess  # noqa: PLC0415
        try:
            result = subprocess.run(
                [self.sudo_bin, self.systemctl_bin, "status", name],
                capture_output=True, text=True, timeout=10,
            )
            return result.stdout or result.stderr
        except (subprocess.SubprocessError, OSError) as exc:
            return f"error: {exc}"

    def spawn_isolated(
        self,
        name: str,
        argv: list[str],
        env: dict[str, str],
        *,
        isolation_strength: Literal["strong", "medium", "none"],
    ) -> Action:
        """Build the systemd-run lead-spawn argv.

        Mirrors ``spawner._wrap_in_transient_unit`` exactly.  The lead's
        EnvironmentFile provides the OAuth token — it is NEVER on argv.

        Parameters
        ----------
        name:
            Lead project name (bare, without prefix/suffix).
        argv:
            Inner argv (the tmux + claude command list).
        env:
            Extra ``--setenv=K=V`` entries.  Must NOT contain raw secret
            values (use env-var names that reference EnvironmentFile).
        isolation_strength:
            ``"strong"`` → systemd-run (cgroup-isolated).
            Others raise ``NotImplementedError`` for Phase 1.
        """
        if isolation_strength != "strong":
            raise NotImplementedError(
                f"SystemdBackend only supports isolation_strength='strong'. "
                f"Got {isolation_strength!r}.  "
                f"Medium/none isolation is Phase 1 degraded (OpenRcBackend) "
                f"or Phase 2/4.  See docs/MULTI_PLATFORM_INSTALL.md §5."
            )

        unit = f"claude-soma-lead-{name}.service"
        cmd: list[str] = [
            self.sudo_bin, "-n",
            self.systemd_run_bin, "--collect", "--quiet",
            f"--unit={unit}",
            "--property=Type=oneshot",
            "--property=RemainAfterExit=yes",
        ]
        for k, v in env.items():
            # SECRETS NOTE: only pass non-secret env-var names here.
            # Tokens must come via EnvironmentFile in the unit, not --setenv.
            cmd.append(f"--setenv={k}={v}")
        cmd.append("--")
        cmd.extend(argv)

        return Action(
            commands=[cmd],
            description=f"Spawn isolated lead {name!r} (cgroup via systemd-run)",
            is_privileged=True,
            note=(
                "SECRETS NOTE: OAuth token loaded from EnvironmentFile "
                f"at runtime ({unit}), never on argv."
            ),
        )


# ---------------------------------------------------------------------------
# Degraded / stub backends
# ---------------------------------------------------------------------------

class OpenRcBackend(ServiceBackend):
    """Phase 1 degraded stub for Alpine Linux (no systemd / OpenRC).

    Lead isolation is medium at best (setsid + process group; no cgroup).
    The install module warns the operator when this backend is selected.

    Full OpenRC implementation is a TODO for Phase 1 follow-up.
    """

    @property
    def isolation_strength(self) -> Literal["strong", "medium", "none"]:
        return "medium"

    def _not_implemented(self, what: str) -> Action:
        raise NotImplementedError(
            f"OpenRcBackend.{what} is not yet implemented (Phase 1 degraded). "
            "Alpine Linux OpenRC support requires manual service registration. "
            "See docs/MULTI_PLATFORM_INSTALL.md §6 for the roadmap."
        )

    def install_service(self, svc: Service) -> Action:
        return self._not_implemented("install_service")

    def install_timer(self, name: str, on_calendar: str, *, service_name: str) -> Action:
        return self._not_implemented("install_timer")

    def enable(self, name: str) -> Action:
        return self._not_implemented("enable")

    def restart(self, name: str) -> Action:
        return self._not_implemented("restart")

    def status(self, name: str) -> str:
        raise NotImplementedError(
            "OpenRcBackend.status is not yet implemented (Phase 1 degraded)."
        )

    def spawn_isolated(
        self,
        name: str,
        argv: list[str],
        env: dict[str, str],
        *,
        isolation_strength: Literal["strong", "medium", "none"],
    ) -> Action:
        return self._not_implemented("spawn_isolated")


class LaunchdBackend(ServiceBackend):
    """Phase 2 stub — macOS launchd.

    Not implemented.  Raises NotImplementedError with a "Phase 2" label
    on every method so callers can detect and report the gap clearly.
    """

    @property
    def isolation_strength(self) -> Literal["strong", "medium", "none"]:
        return "medium"

    def _phase2(self, what: str) -> Action:
        raise NotImplementedError(
            f"LaunchdBackend.{what} is Phase 2 (macOS, not yet implemented). "
            "See docs/MULTI_PLATFORM_INSTALL.md §6."
        )

    def install_service(self, svc: Service) -> Action:
        return self._phase2("install_service")

    def install_timer(self, name: str, on_calendar: str, *, service_name: str) -> Action:
        return self._phase2("install_timer")

    def enable(self, name: str) -> Action:
        return self._phase2("enable")

    def restart(self, name: str) -> Action:
        return self._phase2("restart")

    def status(self, name: str) -> str:
        raise NotImplementedError("LaunchdBackend.status is Phase 2.")

    def spawn_isolated(
        self,
        name: str,
        argv: list[str],
        env: dict[str, str],
        *,
        isolation_strength: Literal["strong", "medium", "none"],
    ) -> Action:
        return self._phase2("spawn_isolated")


class WindowsServiceBackend(ServiceBackend):
    """Phase 4 stub — native Windows (NSSM / Windows Service Manager).

    Not implemented.  Raises NotImplementedError with a "Phase 4" label.
    Note: Windows via WSL2 should use SystemdBackend (Phase 3 path).
    """

    @property
    def isolation_strength(self) -> Literal["strong", "medium", "none"]:
        return "none"

    def _phase4(self, what: str) -> Action:
        raise NotImplementedError(
            f"WindowsServiceBackend.{what} is Phase 4 (native Windows, not yet "
            "implemented).  For Windows with cgroup parity, use WSL2 (Phase 3) "
            "which ships systemd.  See docs/MULTI_PLATFORM_INSTALL.md §6."
        )

    def install_service(self, svc: Service) -> Action:
        return self._phase4("install_service")

    def install_timer(self, name: str, on_calendar: str, *, service_name: str) -> Action:
        return self._phase4("install_timer")

    def enable(self, name: str) -> Action:
        return self._phase4("enable")

    def restart(self, name: str) -> Action:
        return self._phase4("restart")

    def status(self, name: str) -> str:
        raise NotImplementedError("WindowsServiceBackend.status is Phase 4.")

    def spawn_isolated(
        self,
        name: str,
        argv: list[str],
        env: dict[str, str],
        *,
        isolation_strength: Literal["strong", "medium", "none"],
    ) -> Action:
        return self._phase4("spawn_isolated")
