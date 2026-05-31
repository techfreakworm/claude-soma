# src/claude_soma/mcp_servers/project_orchestrator/spawner.py
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


# claude --bg was removed in Claude Code 2.1.150. We now wrap each project lead
# in a detached tmux session, matching the pattern used by
# systemd/claude-soma-channel.service. The native claude install at
# ~/.local/bin/claude is required because the npm wrap at /usr/bin/claude
# silently downgrades to --print mode for the flags we need.
NATIVE_CLAUDE_DEFAULT = "/home/ubuntu/.local/bin/claude"
CLAUDE_BIN = os.environ.get("HERMES_CLAUDE_BIN", NATIVE_CLAUDE_DEFAULT)
TMUX_BIN = os.environ.get("HERMES_TMUX_BIN", "/usr/bin/tmux")
TMUX_SESSION_PREFIX = "soma-proj-"

# Each lead runs inside its OWN transient systemd service, created at spawn time
# with systemd-run, so it lives in a sibling cgroup to
# claude-soma-channel.service instead of inside it. Restarting the channel
# (KillMode=control-group) then can't reach the lead. The lead also gets a
# DEDICATED tmux socket so its server is the only thing on that socket -- the
# server is born INSIDE the new unit, so it is parented to the lead's cgroup,
# not the orchestrator's. See docs/notes/2026-05-25-project-lead-cgroup-teardown.md.
SUDO_BIN = os.environ.get("HERMES_SUDO_BIN", "/usr/bin/sudo")
SYSTEMD_RUN_BIN = os.environ.get("HERMES_SYSTEMD_RUN_BIN", "/usr/bin/systemd-run")
SYSTEMCTL_BIN = os.environ.get("HERMES_SYSTEMCTL_BIN", "/usr/bin/systemctl")
LEAD_SOCKET_PREFIX = "soma-lead-"
LEAD_UNIT_PREFIX = "claude-soma-lead-"

# The fresh systemd unit does NOT inherit the channel's environment the way the
# old shared-tmux spawn did, so we restore the essentials explicitly. The OAuth
# token (no API key -- Max OAuth only) comes from the EnvironmentFile, never the
# command line, so it can't leak via `ps`/audit. Leading `-` => optional, so
# spawn doesn't fail on a box without the file (CI/dev).
LEAD_USER = os.environ.get("HERMES_LEAD_USER", "ubuntu")
LEAD_GROUP = os.environ.get("HERMES_LEAD_GROUP", "ubuntu")
LEAD_HOME = os.environ.get("HERMES_LEAD_HOME", "/home/ubuntu")
LEAD_ENV_FILE = os.environ.get("HERMES_LEAD_ENV_FILE", "/etc/claude-soma/secrets.env")
LEAD_PATH = os.environ.get(
    "HERMES_LEAD_PATH",
    "/opt/claude-soma/.venv/bin:/home/ubuntu/.local/bin:/home/ubuntu/bin:"
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin",
)

# Each lead's pane output is teed to <log dir>/<name>.log so a dead lead's
# output survives for forensics. The env var is read at call time (via
# _lead_log_path) so tests can redirect it off /var/log. The CLAUDE.md canonical
# log dir is /var/log/claude-soma (ubuntu-owned, so the orchestrator can create
# the file).
LEAD_LOG_DIR_DEFAULT = "/var/log/claude-soma"

# Leads load the bot's TOOL MCP servers (playwright x5, voice-stt/tts) via this
# curated --mcp-config: it is the bot's .mcp.json MINUS the control-plane
# servers (hermes-api, which unlink+rebinds the bot's dashboard socket, and
# project-orchestrator, which shares the registry and could recursively
# spawn/kill leads). Telegram is excluded everywhere (bot-only via --settings).
# Read at call time (HERMES_LEAD_MCP_CONFIG) so tests can point elsewhere; if
# the file is absent the flag is simply omitted (leads degrade to their own
# scope rather than failing to spawn).
LEAD_MCP_CONFIG_DEFAULT = "/opt/claude-soma/config/claude/lead-mcp.json"

# systemd-run returns quickly once the oneshot ExecStart (tmux new-session -d)
# detaches; 20s is generous headroom for talking to PID 1 under load.
SPAWN_TIMEOUT = 20

# Where Claude Code stores per-cwd trust state (NOT ~/.claude/settings.json —
# that key is ignored in 2.1.150; only this file is consulted). Override with
# HERMES_CLAUDE_GLOBAL_JSON for tests.
CLAUDE_GLOBAL_JSON_DEFAULT = str(Path.home() / ".claude.json")

MAX_BRIEF_CHARS = 100_000  # safety: keep briefs reasonable
NAME_RX = re.compile(r"^[a-z][a-z0-9-]{0,63}$")

# claude prints a Remote Control URL like
# https://claude.ai/code/session_018tGGH6weoRDokVSn7fmMhR at startup (verified
# live 2026-05-26 across three running leads). Older builds printed
# https://rc.claude.com/<id>; keep that as a fallback so a version skew doesn't
# silently break capture again. The session id is the only variable part
# (base62), so bound it to URL-safe chars instead of \S+ -- \S+ would swallow a
# trailing box-drawing glyph if the URL ever renders mid-line in the pane.
RC_URL_RX = re.compile(
    r"https?://claude\.ai/code/session_[A-Za-z0-9_-]+"
    r"|https?://rc\.claude\.com/\S+"
)

# How long to keep polling tmux capture for the Remote Control URL before
# giving up. claude prints the URL within ~5s usually, but the first capture
# right after tmux returns sometimes misses it.
RC_URL_POLL_SECONDS = 30
RC_URL_POLL_INTERVAL = 2


class BriefTooLong(Exception):
    pass


class InvalidProjectName(Exception):
    pass


def _claude() -> str:
    if Path(CLAUDE_BIN).exists():
        return CLAUDE_BIN
    found = shutil.which(CLAUDE_BIN) or shutil.which("claude")
    if found is None:
        raise RuntimeError(
            f"claude binary not found (tried {CLAUDE_BIN!r} and PATH)"
        )
    return found


def _tmux() -> str:
    if Path(TMUX_BIN).exists():
        return TMUX_BIN
    found = shutil.which(TMUX_BIN) or shutil.which("tmux")
    if found is None:
        raise RuntimeError(f"tmux binary not found (tried {TMUX_BIN!r} and PATH)")
    return found


def _session_name(name: str) -> str:
    return f"{TMUX_SESSION_PREFIX}{name}"


def _bare_name(name: str) -> str:
    """Accept either a bare project name or a full `soma-proj-<name>` session
    name (agent_id) and return the bare name."""
    if name.startswith(TMUX_SESSION_PREFIX):
        return name[len(TMUX_SESSION_PREFIX):]
    return name


def _lead_socket(name: str) -> str:
    return f"{LEAD_SOCKET_PREFIX}{name}"


def _lead_unit(name: str) -> str:
    return f"{LEAD_UNIT_PREFIX}{name}.service"


def _lead_log_path(name: str) -> Path:
    base = os.environ.get("HERMES_LEAD_LOG_DIR", LEAD_LOG_DIR_DEFAULT)
    return Path(base) / f"{name}.log"


def _wrap_in_transient_unit(name: str, inner_argv: list[str]) -> list[str]:
    """Wrap `inner_argv` so it runs inside its own transient systemd service,
    giving the lead a cgroup independent of claude-soma-channel.service."""
    return [
        SUDO_BIN, "-n", SYSTEMD_RUN_BIN, "--collect", "--quiet",
        f"--unit={_lead_unit(name)}",
        "--property=Type=oneshot",
        "--property=RemainAfterExit=yes",
        f"--property=User={LEAD_USER}",
        f"--property=Group={LEAD_GROUP}",
        f"--property=EnvironmentFile=-{LEAD_ENV_FILE}",
        f"--setenv=HOME={LEAD_HOME}",
        f"--setenv=PATH={LEAD_PATH}",
        "--setenv=CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1",
        f"--setenv=HERMES_LEAD_NAME={name}",
        "--setenv=HERMES_NOTIFY_ENDPOINT=http://127.0.0.1:9100",
        "--",
        *inner_argv,
    ]


def _claude_global_json() -> Path:
    return Path(os.environ.get("HERMES_CLAUDE_GLOBAL_JSON", CLAUDE_GLOBAL_JSON_DEFAULT))


def _pretrust_cwd(cwd: Path) -> None:
    """Pre-mark `cwd` as trusted in ~/.claude.json so claude skips the
    "Quick safety check: is this a project you trust?" interactive dialog at
    startup. Without this, a tmux-detached claude blocks forever waiting for
    a human to hit Enter.

    Idempotent: existing project entries are preserved/merged. The whole file
    is rewritten atomically (tempfile + os.replace) to avoid a torn write that
    would corrupt other projects' state.
    """
    path = _claude_global_json()
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        # Don't bulldoze a corrupt file; just bail and let the dialog appear
        # (operator will see the prompt in the tmux pane and can investigate).
        return

    projects = data.setdefault("projects", {})
    entry = projects.setdefault(str(cwd), {})
    entry["hasTrustDialogAccepted"] = True
    entry.setdefault("projectOnboardingSeenCount", 0)

    # Atomic write: write to a sibling tempfile, fsync, rename. Preserves
    # the existing inode's owner/mode by recreating them after the rename.
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=".claude.json.", suffix=".tmp", dir=str(parent),
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _capture_rc_url(session: str, socket: str, timeout: float | None = None) -> str:
    """Poll the tmux pane until the Remote Control URL appears, or timeout.

    The original implementation was a single capture-pane call right after
    tmux returned, which raced with claude's startup output — most of the
    time the URL hadn't printed yet at that exact instant and we silently
    returned "". Polling gives claude time to actually emit the URL.

    `timeout=None` reads the module constant at call time, so tests can
    monkeypatch RC_URL_POLL_SECONDS without rebinding the default.

    `socket` is the lead's dedicated tmux socket (-L), since each lead now runs
    its own tmux server inside its own systemd unit.
    """
    if timeout is None:
        timeout = RC_URL_POLL_SECONDS
    deadline = time.monotonic() + timeout
    last_stdout = ""
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                [_tmux(), "-L", socket, "capture-pane", "-p", "-t", session],
                capture_output=True, text=True, check=True, timeout=10,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return ""
        last_stdout = result.stdout or ""
        match = RC_URL_RX.search(last_stdout)
        if match:
            return match.group(0)
        time.sleep(RC_URL_POLL_INTERVAL)
    return ""


def spawn_background_lead(
    *,
    name: str,
    brief: str,
    cwd: Path,
    permission_mode: str,
    extra_args: list[str] | None = None,
) -> dict:
    if not NAME_RX.match(name):
        raise InvalidProjectName(
            f"project name must match {NAME_RX.pattern}, got {name!r}"
        )
    if len(brief) > MAX_BRIEF_CHARS:
        raise BriefTooLong(f"brief is {len(brief)} chars (max {MAX_BRIEF_CHARS})")
    cwd.mkdir(parents=True, exist_ok=True)

    # Mark the cwd as trusted BEFORE spawning, otherwise claude blocks on the
    # safety dialog forever in a detached tmux pane (no human to hit Enter).
    _pretrust_cwd(cwd)

    session = _session_name(name)
    socket = _lead_socket(name)
    claude_argv: list[str] = [
        _claude(),
        # --remote-control <name>: project leads stay alive after their first
        # task completes (otherwise claude exits) AND get an rc.claude.com URL
        # the operator can attach to from the Claude mobile app / web. The name
        # matches our tmux session name so they're easy to correlate.
        "--remote-control", session,
        "--add-dir", str(cwd),
        "--permission-mode", permission_mode,
        "--dangerously-skip-permissions",
        "--effort", "max",
        # Load USER scope too, not just project,local. This gives leads the
        # user-scope MCP servers (sequential-thinking) and the user's
        # skills/agents/plugins. It used to be `project,local` to keep the
        # then-user-scoped telegram plugin out of leads (it would hijack the
        # bot's poller). That risk is GONE: telegram was moved out of user
        # scope -- the bot now opts in ONLY via
        # `--settings .../channel-settings.json`, which leads never receive.
        # So a lead gets every user MCP/skill/plugin EXCEPT telegram. (Verified
        # 2026-05-26: ~/.claude.json + ~/.claude/settings.json enabledPlugins
        # contain no telegram; /home/ubuntu even disables it explicitly.)
        "--setting-sources", "user,project,local",
    ]
    # Inject the bot's tool MCP servers (playwright, voice) from the curated
    # lead config -- but NOT hermes-api/project-orchestrator (see
    # LEAD_MCP_CONFIG_DEFAULT). Omit the flag if the file isn't present so a
    # box without it (pre-deploy / CI) still spawns leads cleanly.
    lead_mcp_config = os.environ.get("HERMES_LEAD_MCP_CONFIG", LEAD_MCP_CONFIG_DEFAULT)
    if Path(lead_mcp_config).exists():
        claude_argv += ["--mcp-config", lead_mcp_config]
    if extra_args:
        claude_argv.extend(extra_args)
    # The brief is the positional prompt and MUST be guarded by a `--`
    # end-of-options separator. claude's --mcp-config is variadic ("<configs...>"),
    # so a bare trailing brief right after --mcp-config <path> gets swallowed as a
    # second config-file path -> ENAMETOOLONG -> the lead crashes at startup
    # (regression fixed here). `--` also protects a brief that starts with `-`.
    claude_argv += ["--", brief]

    # Tee the lead's pane output to its own log so a dead lead's last output
    # survives. Best-effort: create the dir if we can, but never fail the spawn
    # over logging -- if the dir is unwritable the chained `cat` just exits and
    # the pane is unaffected.
    log_path = _lead_log_path(name)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    # tmux on the lead's OWN socket, wrapped in its OWN transient systemd unit
    # so the server is parented to the lead's cgroup, not the channel's. The
    # `;` is tmux's command separator: chaining pipe-pane into the SAME
    # invocation tees the pane from the moment the session is born, adds no
    # extra spawn subprocess, and -- because the `cat` writer is forked by the
    # tmux server (the lead's cgroup) -- the logging survives a channel restart
    # exactly like the lead does. -O pipes output only; -o is a no-op if a pipe
    # already exists (idempotent).
    tmux_argv: list[str] = [
        _tmux(), "-L", socket, "new-session", "-d", "-s", session,
        "-c", str(cwd), *claude_argv,
        ";", "pipe-pane", "-O", "-o", "-t", session,
        f"cat >> {shlex.quote(str(log_path))}",
    ]
    cmd: list[str] = _wrap_in_transient_unit(name, tmux_argv)

    try:
        subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=SPAWN_TIMEOUT,
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "")[-500:]
        if "already exists" in stderr.lower():
            raise RuntimeError(
                f"a systemd unit for project {name!r} already exists "
                f"({_lead_unit(name)}); kill the existing project first, or if "
                f"it is a stale/dead lead run kill_project to clear it. "
                f"stderr: {stderr}"
            ) from e
        raise RuntimeError(f"spawn failed for {name!r}: {stderr}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"spawn timed out for {name!r} ({SPAWN_TIMEOUT}s)") from e

    rc_url = _capture_rc_url(session, socket)

    return {
        "agent_id": session,
        "rc_url": rc_url,
        "cwd": str(cwd),
    }


def is_lead_alive(name: str) -> bool:
    """True iff the lead's tmux session is alive on its dedicated socket.

    Ground-truth liveness: the lead's claude process IS the tmux pane process,
    and with remain-on-exit off (the default) tmux destroys the session the
    instant claude exits, so a live session means a live lead. We deliberately
    do NOT trust the systemd unit state: the transient unit is Type=oneshot +
    RemainAfterExit=yes, so it reads `active (exited)` even after the tmux
    server inside it has died -- `systemctl is-active` would call a dead lead
    alive.

    `tmux has-session` exits non-zero (no exception) when the session is gone
    OR the socket no longer exists, which is exactly the vanished-lead case.
    Conservative on a genuine tool error (tmux missing, timeout): return True so
    a transient glitch never demotes a live lead to 'dead' -- a false 'dead'
    hides a running lead from list_projects and risks a duplicate respawn, a
    worse outcome than briefly showing a ghost.
    """
    bare = _bare_name(name)
    session = _session_name(bare)
    socket = _lead_socket(bare)
    try:
        result = subprocess.run(
            [_tmux(), "-L", socket, "has-session", "-t", session],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return True
    return result.returncode == 0


def discover_team(name: str) -> list[dict[str, str]]:
    """Best-effort roster of a lead's agent-team teammates, read live from its
    tmux panes.

    With CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 a lead spawns its teammates as
    split panes in its window (see docs/KNOWN_BUGS.md), so every pane beyond the
    lead's own is a teammate. We read it live (rather than persist a table)
    because teammates are ephemeral -- they die with the session -- so a cached
    roster would go stale. Returns [] if the lead is gone or has no team.

    Coarse by design: the pane view gives a teammate's live activity + status but
    not its exact TeamCreate handle (@ping, ...). Exact handles would need leads
    to self-report into a registry table -- a noted enhancement.
    """
    bare = _bare_name(name)
    session = _session_name(bare)
    socket = _lead_socket(bare)
    try:
        result = subprocess.run(
            [_tmux(), "-L", socket, "list-panes", "-s", "-t", session,
             "-F", "#{pane_index}\t#{pane_dead}\t#{pane_title}"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return []
    if result.returncode != 0:
        return []
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    # The first pane is the lead itself; the rest are its teammates.
    team: list[dict[str, str]] = []
    for i, line in enumerate(lines[1:], start=1):
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        idx, dead, title = parts[0], parts[1], parts[2]
        team.append({
            "handle": f"teammate-{idx}",
            "role": title.strip() or "teammate",
            "status": "dead" if dead == "1" else "active",
        })
    return team


def kill_session(name: str) -> None:
    bare = _bare_name(name)
    session = _session_name(bare)
    socket = _lead_socket(bare)
    unit = _lead_unit(bare)
    # Stop the transient unit first: KillMode=control-group tears down the whole
    # cgroup (tmux server included) and avoids leaking an `active (exited)` unit.
    # Best-effort -- the unit may already be gone.
    try:
        subprocess.run(
            [SUDO_BIN, "-n", SYSTEMCTL_BIN, "stop", unit],
            capture_output=True, text=True, check=True, timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    # Belt-and-suspenders: kill the session on its own socket too, tolerating a
    # server already torn down by the unit stop above.
    try:
        subprocess.run(
            [_tmux(), "-L", socket, "kill-session", "-t", session],
            capture_output=True, text=True, check=True, timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # Already gone, or tmux server down — nothing to do.
        return
